"""Celery task definitions for the EulerX analysis and execution pipeline.

Three-stage pipeline triggered by Celery Beat every N hours:
  1. fetch_market_data — get top symbols from Hyperliquid
  2. generate_signals_for_strategy — AI consensus signals per active strategy
  3. execute_signal_task — auto-execute each signal via ATE

Plus maintenance:
  - expire_stale_signals — clean up expired signals every 30 minutes
  - check_expiring_subscriptions — notify users of expiring subscriptions daily

Plus notifications:
  - send_notification_email — async email dispatch via ZeptoMail
  - send_notification_telegram — async Telegram dispatch
"""

import logging
import uuid

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from datetime import timedelta

from app.config import get_settings
from app.db.base import async_session_factory
from app.models.billing import Plan, Subscription
from app.models.admin_config import AdminConfig
from app.models.enums import SignalStatus, SubscriptionStatus
from app.models.signal import Signal
from app.models.strategy import Strategy
from app.services.ai_engine import AIEngineService
from app.services.ate import ATEService
from app.services.hyperliquid import HyperliquidService
from app.services.notifications import NotificationService
from app.utils.helpers import utc_now
from app.worker.async_runner import run_async

logger = logging.getLogger(__name__)
settings = get_settings()


# ── Async Pipeline Functions ─────────────────────────────────────


async def _fetch_market_data_async() -> list[dict]:
    """Fetch market data from Hyperliquid."""
    service = HyperliquidService()
    symbols = await service.get_top_gainers(limit=settings.analysis_top_symbols_limit)
    logger.info("Fetched %d symbols from Hyperliquid", len(symbols))
    return symbols


async def _get_active_strategy_ids_async() -> list[str]:
    """Query all active strategy IDs from the database."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(Strategy.id).where(Strategy.is_active == True)  # noqa: E712
        )
        rows = result.scalars().all()
        return [str(sid) for sid in rows]


async def _generate_signals_for_strategy_async(
    strategy_id: str,
    market_data: list[dict],
) -> list[str]:
    """Generate signals for a single strategy. Returns list of signal IDs."""
    ai_engine = AIEngineService()

    async with async_session_factory() as session:
        try:
            # Load strategy with user relationship
            result = await session.execute(
                select(Strategy)
                .options(selectinload(Strategy.user))
                .where(
                    Strategy.id == uuid.UUID(strategy_id),
                    Strategy.is_active == True,  # noqa: E712
                )
            )
            strategy = result.scalar_one_or_none()

            if strategy is None:
                logger.warning("Strategy %s not found or inactive, skipping", strategy_id)
                return []

            user = strategy.user

            # Verify user has ATE access via subscription
            sub_result = await session.execute(
                select(Subscription)
                .options(selectinload(Subscription.plan))
                .where(
                    Subscription.user_id == user.id,
                    Subscription.status.in_([
                        SubscriptionStatus.ACTIVE,
                        SubscriptionStatus.EXPIRING_SOON,
                    ]),
                )
                .order_by(Subscription.created_at.desc())
                .limit(1)
            )
            subscription = sub_result.scalar_one_or_none()

            if subscription and subscription.plan and not subscription.plan.ate_access:
                logger.info(
                    "User %s plan lacks ATE access, skipping strategy %s",
                    user.id,
                    strategy_id,
                )
                return []

            # Check drawdown before generating signals
            ate_service = ATEService()
            drawdown_hit = await ate_service.check_drawdown(session, strategy, user)
            if drawdown_hit:
                logger.warning("Strategy %s hit drawdown limit, auto-paused", strategy_id)
                await session.commit()
                return []

            # Generate signals
            signals = await ai_engine.generate_signals(
                symbols=market_data,
                db=session,
                strategy_id=strategy_id,
            )

            if signals:
                await session.commit()
                generated_ids = [str(s.id) for s in signals]
                logger.info(
                    "Generated %d signals for strategy %s",
                    len(signals),
                    strategy_id,
                )

                # Notify user of generated signals
                try:
                    notification_service = NotificationService()
                    await notification_service.send_signal_generated(
                        user=user,
                        strategy_name=strategy.name,
                        signal_count=len(signals),
                    )
                except Exception as e:
                    logger.error("Failed to send signal generated email: %s", e)

                return generated_ids

            logger.info("No actionable signals for strategy %s", strategy_id)
            return []

        except Exception:
            await session.rollback()
            raise


async def _is_trading_halted(session) -> bool:
    """Check if the global trading halt flag is active."""
    result = await session.execute(
        select(AdminConfig).where(AdminConfig.key == "trading_halt")
    )
    config = result.scalar_one_or_none()
    return bool(config and config.value.get("halted"))


async def _execute_signal_async(signal_id: str, strategy_id: str) -> dict:
    """Execute a single signal via the ATE. Returns execution result dict."""
    ate_service = ATEService()

    async with async_session_factory() as session:
        try:
            # Check global trading halt before executing anything
            if await _is_trading_halted(session):
                return {"status": "skipped", "reason": "Trading is halted"}

            # Load signal
            signal_result = await session.execute(
                select(Signal).where(Signal.id == uuid.UUID(signal_id))
            )
            signal = signal_result.scalar_one_or_none()
            if signal is None:
                return {"status": "skipped", "reason": "Signal not found"}

            if signal.status != SignalStatus.NEW:
                return {"status": "skipped", "reason": f"Signal status is {signal.status.value}"}

            if signal.expires_at and signal.expires_at < utc_now():
                signal.status = SignalStatus.EXPIRED
                await session.commit()
                return {"status": "expired", "reason": "Signal expired"}

            # Load strategy and user
            strategy_result = await session.execute(
                select(Strategy)
                .options(selectinload(Strategy.user))
                .where(Strategy.id == uuid.UUID(strategy_id))
            )
            strategy = strategy_result.scalar_one_or_none()
            if strategy is None or not strategy.is_active:
                return {"status": "skipped", "reason": "Strategy not found or inactive"}

            user = strategy.user

            # Execute via ATE
            execution = await ate_service.execute_signal(
                db=session,
                signal=signal,
                strategy=strategy,
                user=user,
            )

            await session.commit()

            if execution is None:
                return {"status": "rejected", "reason": "ATE rejected signal"}

            return {
                "status": execution.status.value,
                "execution_id": str(execution.id),
                "signal_id": signal_id,
            }

        except Exception:
            await session.rollback()
            raise


async def _expire_stale_signals_async() -> int:
    """Mark expired signals. Returns count of expired signals."""
    async with async_session_factory() as session:
        try:
            now = utc_now()
            result = await session.execute(
                select(Signal).where(
                    Signal.status == SignalStatus.NEW,
                    Signal.expires_at != None,  # noqa: E711
                    Signal.expires_at < now,
                )
            )
            stale_signals = result.scalars().all()

            for signal in stale_signals:
                signal.status = SignalStatus.EXPIRED

            if stale_signals:
                await session.commit()
                logger.info("Expired %d stale signals", len(stale_signals))

            return len(stale_signals)
        except Exception:
            await session.rollback()
            raise


# ── Celery Tasks ──────────────────────────────────────────────────


@shared_task(
    name="app.worker.tasks.fetch_market_data",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    soft_time_limit=120,
    time_limit=150,
    acks_late=True,
    track_started=True,
)
def fetch_market_data(self) -> list[dict]:
    """Fetch current market data from Hyperliquid. Stage 1 of pipeline."""
    try:
        return run_async(_fetch_market_data_async())
    except SoftTimeLimitExceeded:
        logger.error("fetch_market_data hit soft time limit")
        raise
    except Exception as exc:
        logger.error("fetch_market_data failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


@shared_task(
    name="app.worker.tasks.generate_signals_for_strategy",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    soft_time_limit=300,
    time_limit=360,
    acks_late=True,
    track_started=True,
)
def generate_signals_for_strategy(
    self,
    strategy_id: str,
    market_data: list[dict],
) -> list[str]:
    """Generate AI signals for a specific strategy. Stage 2 of pipeline."""
    try:
        return run_async(
            _generate_signals_for_strategy_async(strategy_id, market_data)
        )
    except SoftTimeLimitExceeded:
        logger.error(
            "generate_signals_for_strategy(%s) hit soft time limit",
            strategy_id,
        )
        raise
    except Exception as exc:
        logger.error(
            "generate_signals_for_strategy(%s) failed: %s",
            strategy_id,
            exc,
            exc_info=True,
        )
        raise self.retry(exc=exc)


@shared_task(
    name="app.worker.tasks.execute_signal_task",
    bind=True,
    max_retries=1,
    default_retry_delay=30,
    soft_time_limit=120,
    time_limit=150,
    acks_late=True,
    track_started=True,
)
def execute_signal_task(
    self,
    signal_id: str,
    strategy_id: str,
) -> dict:
    """Execute a single signal via the ATE. Stage 3 of pipeline.

    Low retry count (1) to minimize double-execution risk.
    ATE checks signal.status == NEW for idempotency.
    """
    try:
        return run_async(_execute_signal_async(signal_id, strategy_id))
    except SoftTimeLimitExceeded:
        logger.error("execute_signal_task(%s) hit soft time limit", signal_id)
        raise
    except Exception as exc:
        logger.error(
            "execute_signal_task(%s) failed: %s",
            signal_id,
            exc,
            exc_info=True,
        )
        raise self.retry(exc=exc)


@shared_task(
    name="app.worker.tasks.run_analysis_pipeline",
    bind=True,
    max_retries=1,
    soft_time_limit=settings.celery_task_soft_time_limit,
    time_limit=settings.celery_task_time_limit,
    acks_late=True,
    track_started=True,
)
def run_analysis_pipeline(self) -> dict:
    """Orchestrator: runs the full analysis + execution pipeline.

    Triggered by Celery Beat every N hours. Flow:
      1. Fetch market data from Hyperliquid
      2. Query all active strategies
      3. For each strategy: generate signals via AI Engine
      4. For each new signal: execute via ATE
      5. Return summary
    """
    pipeline_id = str(uuid.uuid4())[:8]
    logger.info("[Pipeline %s] Starting analysis pipeline", pipeline_id)

    try:
        # Stage 1: Fetch market data
        market_data = run_async(_fetch_market_data_async())

        if not market_data:
            logger.warning("[Pipeline %s] No market data available", pipeline_id)
            return {
                "pipeline_id": pipeline_id,
                "status": "completed",
                "market_data_count": 0,
                "strategies_processed": 0,
                "signals_generated": 0,
                "executions_attempted": 0,
            }

        # Stage 2: Get all active strategies
        active_strategy_ids = run_async(_get_active_strategy_ids_async())

        if not active_strategy_ids:
            logger.info("[Pipeline %s] No active strategies found", pipeline_id)
            return {
                "pipeline_id": pipeline_id,
                "status": "completed",
                "market_data_count": len(market_data),
                "strategies_processed": 0,
                "signals_generated": 0,
                "executions_attempted": 0,
            }

        logger.info(
            "[Pipeline %s] Processing %d strategies with %d symbols",
            pipeline_id,
            len(active_strategy_ids),
            len(market_data),
        )

        # Stage 3: Generate signals for each strategy (sequential to avoid API thundering herd)
        total_signals = 0
        total_executions = 0
        all_signal_pairs: list[tuple[str, str]] = []  # (signal_id, strategy_id)

        for strategy_id in active_strategy_ids:
            try:
                signal_ids = run_async(
                    _generate_signals_for_strategy_async(strategy_id, market_data)
                )
                if signal_ids:
                    total_signals += len(signal_ids)
                    all_signal_pairs.extend(
                        (sid, strategy_id) for sid in signal_ids
                    )
            except Exception as exc:
                logger.error(
                    "[Pipeline %s] Signal generation failed for strategy %s: %s",
                    pipeline_id,
                    strategy_id,
                    exc,
                )
                continue

        # Stage 4: Execute each generated signal
        for signal_id, strategy_id in all_signal_pairs:
            try:
                result = run_async(
                    _execute_signal_async(signal_id, strategy_id)
                )
                if result.get("status") in ("filled", "FILLED"):
                    total_executions += 1
                logger.info(
                    "[Pipeline %s] Signal %s: %s",
                    pipeline_id,
                    signal_id,
                    result,
                )
            except Exception as exc:
                logger.error(
                    "[Pipeline %s] Execution failed for signal %s: %s",
                    pipeline_id,
                    signal_id,
                    exc,
                )
                continue

        summary = {
            "pipeline_id": pipeline_id,
            "status": "completed",
            "market_data_count": len(market_data),
            "strategies_processed": len(active_strategy_ids),
            "signals_generated": total_signals,
            "executions_attempted": total_executions,
        }
        logger.info("[Pipeline %s] Pipeline completed: %s", pipeline_id, summary)
        return summary

    except SoftTimeLimitExceeded:
        logger.error("[Pipeline %s] Pipeline hit soft time limit", pipeline_id)
        raise
    except Exception as exc:
        logger.error(
            "[Pipeline %s] Pipeline failed: %s",
            pipeline_id,
            exc,
            exc_info=True,
        )
        raise self.retry(exc=exc)


@shared_task(
    name="app.worker.tasks.expire_stale_signals",
    soft_time_limit=60,
    time_limit=90,
    acks_late=True,
)
def expire_stale_signals() -> dict:
    """Maintenance: expire signals past their expires_at timestamp."""
    count = run_async(_expire_stale_signals_async())
    return {"expired_count": count}


# ── Notification Tasks ───────────────────────────────────────────


async def _send_notification_email_async(
    to_email: str,
    to_name: str,
    subject: str,
    html_body: str,
) -> bool:
    """Send a single notification email."""
    service = NotificationService()
    return await service.send_email(to_email, to_name, subject, html_body)


async def _check_expiring_subscriptions_async() -> int:
    """Find subscriptions expiring within 3 days and send notifications."""
    service = NotificationService()
    now = utc_now()
    threshold = now + timedelta(days=3)
    notified = 0

    async with async_session_factory() as session:
        try:
            result = await session.execute(
                select(Subscription)
                .options(
                    selectinload(Subscription.user),
                    selectinload(Subscription.plan),
                )
                .where(
                    Subscription.status == SubscriptionStatus.ACTIVE,
                    Subscription.expires_at != None,  # noqa: E711
                    Subscription.expires_at <= threshold,
                    Subscription.expires_at > now,
                )
            )
            subs = result.scalars().all()

            for sub in subs:
                user = sub.user
                if not user:
                    continue

                plan = sub.plan
                plan_name = plan.name if plan else "Unknown"

                days_remaining = max(1, (sub.expires_at - now).days)
                expires_str = sub.expires_at.strftime("%B %d, %Y")

                # Dual-channel: sends email + Telegram if configured
                await service.send_subscription_expiring(
                    user=user,
                    plan_name=plan_name,
                    days_remaining=days_remaining,
                    expires_at=expires_str,
                )
                notified += 1

            # Mark as expiring soon
            for sub in subs:
                sub.status = SubscriptionStatus.EXPIRING_SOON

            if subs:
                await session.commit()

            return notified
        except Exception:
            await session.rollback()
            raise


@shared_task(
    name="app.worker.tasks.send_notification_email",
    soft_time_limit=30,
    time_limit=45,
    acks_late=True,
)
def send_notification_email(
    to_email: str,
    to_name: str,
    subject: str,
    html_body: str,
) -> dict:
    """Send a notification email asynchronously via Celery."""
    success = run_async(
        _send_notification_email_async(to_email, to_name, subject, html_body)
    )
    return {"sent": success, "to": to_email}


@shared_task(
    name="app.worker.tasks.check_expiring_subscriptions",
    soft_time_limit=120,
    time_limit=150,
    acks_late=True,
)
def check_expiring_subscriptions() -> dict:
    """Daily: notify users whose subscriptions expire within 3 days."""
    count = run_async(_check_expiring_subscriptions_async())
    return {"notified_count": count}


# ── Telegram Notification Task ──────────────────────────────────


async def _send_notification_telegram_async(
    bot_token: str,
    chat_id: str,
    text: str,
) -> bool:
    """Send a single Telegram notification."""
    return await NotificationService.send_telegram(bot_token, chat_id, text)


@shared_task(
    name="app.worker.tasks.send_notification_telegram",
    soft_time_limit=30,
    time_limit=45,
    acks_late=True,
)
def send_notification_telegram(
    bot_token: str,
    chat_id: str,
    text: str,
) -> dict:
    """Send a Telegram notification asynchronously via Celery."""
    success = run_async(
        _send_notification_telegram_async(bot_token, chat_id, text)
    )
    return {"sent": success, "chat_id": chat_id}


# ── TP/SL Position Monitoring Task ────────────────────────────────


async def _monitor_open_positions_async() -> dict:
    """Monitor all open positions for TP/SL hits and close triggered ones."""
    ate_service = ATEService()

    async with async_session_factory() as session:
        try:
            results = await ate_service.monitor_positions(session)
            if results:
                await session.commit()
            return {
                "positions_closed": len(results),
                "details": results,
            }
        except Exception:
            await session.rollback()
            raise


@shared_task(
    name="app.worker.tasks.monitor_open_positions",
    soft_time_limit=120,
    time_limit=150,
    acks_late=True,
)
def monitor_open_positions() -> dict:
    """Monitor open positions for TP/SL hits. Runs every 1 minute via Beat."""
    try:
        return run_async(_monitor_open_positions_async())
    except SoftTimeLimitExceeded:
        logger.error("monitor_open_positions hit soft time limit")
        raise
    except Exception as exc:
        logger.error("monitor_open_positions failed: %s", exc, exc_info=True)
        raise


# ── Data Retention Cleanup Task ───────────────────────────────────


async def _cleanup_old_data_async() -> dict:
    """Remove old expired signals and audit log entries beyond retention period."""
    retention_days = 90
    cutoff = utc_now() - timedelta(days=retention_days)

    async with async_session_factory() as session:
        try:
            # Delete expired signals older than retention period
            from sqlalchemy import delete
            result = await session.execute(
                delete(Signal).where(
                    Signal.status == SignalStatus.EXPIRED,
                    Signal.created_at < cutoff,
                )
            )
            deleted_signals = result.rowcount

            await session.commit()
            logger.info("Data cleanup: deleted %d old expired signals", deleted_signals)
            return {"deleted_signals": deleted_signals}
        except Exception:
            await session.rollback()
            raise


@shared_task(
    name="app.worker.tasks.cleanup_old_data",
    soft_time_limit=300,
    time_limit=360,
    acks_late=True,
)
def cleanup_old_data() -> dict:
    """Weekly: clean up old expired signals beyond retention period."""
    return run_async(_cleanup_old_data_async())
