import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models.enums import ExecutionStatus, OrderType, SignalDirection, SignalStatus
from app.models.execution import Execution
from app.models.signal import Signal
from app.models.strategy import Strategy
from app.models.user import User
from app.services.hyperliquid import HyperliquidService
from app.services.notifications import NotificationService
from app.services.verification import VerificationService
from app.utils.helpers import utc_now
from app.utils.rate_limiter import InMemoryRateLimiter
from app.utils.security import decrypt_private_key

logger = logging.getLogger(__name__)
settings = get_settings()

ate_rate_limiter = InMemoryRateLimiter(
    max_requests=settings.ate_max_executions_per_hour,
    window_seconds=3600,
)


class ATEService:
    def __init__(self):
        self.hyperliquid = HyperliquidService()

    def evaluate_signal(
        self,
        signal: Signal,
        strategy: Strategy,
        open_position_count: int,
    ) -> tuple[bool, str]:
        if signal.direction == SignalDirection.HOLD:
            return False, "Signal is HOLD"

        if signal.confidence < settings.ate_confidence_threshold:
            return (
                False,
                f"Confidence {signal.confidence} below threshold {settings.ate_confidence_threshold}",
            )

        if signal.status != SignalStatus.NEW:
            return False, f"Signal status is {signal.status.value}, expected NEW"

        if signal.expires_at and signal.expires_at < utc_now():
            return False, "Signal has expired"

        if open_position_count >= strategy.max_positions:
            return False, f"Max positions ({strategy.max_positions}) reached"

        return True, "Signal approved for execution"

    def calculate_position_size(
        self,
        strategy: Strategy,
        entry_price: float,
    ) -> float:
        capital = float(strategy.capital_allocation)
        leverage = min(strategy.leverage_limit, settings.ate_default_leverage)

        risk_multiplier = {
            "low": 0.02,
            "medium": 0.05,
            "high": 0.10,
        }.get(strategy.risk_profile.value, 0.02)

        position_value = capital * risk_multiplier * leverage
        if entry_price <= 0:
            return 0.0
        return round(position_value / entry_price, 8)

    async def execute_signal(
        self,
        db: AsyncSession,
        signal: Signal,
        strategy: Strategy,
        user: User,
    ) -> Execution | None:
        rate_key = f"ate:{user.id}"
        if not ate_rate_limiter.check(rate_key):
            logger.warning(f"ATE rate limit reached for user {user.id}")
            return None

        # Count open positions
        result = await db.execute(
            select(func.count(Execution.id)).where(
                Execution.user_id == user.id,
                Execution.strategy_id == strategy.id,
                Execution.status.in_(
                    [
                        ExecutionStatus.PENDING,
                        ExecutionStatus.FILLED,
                    ]
                ),
            )
        )
        open_count = result.scalar() or 0

        can_execute, reason = self.evaluate_signal(signal, strategy, open_count)
        if not can_execute:
            logger.info(f"Signal {signal.id} rejected: {reason}")
            return None

        entry_price = float(signal.entry_price)
        quantity = self.calculate_position_size(strategy, entry_price)
        if quantity <= 0:
            logger.warning(f"Calculated position size is 0 for signal {signal.id}")
            return None

        is_buy = signal.direction == SignalDirection.BUY

        # Create execution record
        execution = Execution(
            signal_id=signal.id,
            user_id=user.id,
            strategy_id=strategy.id,
            wallet_address_hash=user.wallet_address_hash,
            order_type=OrderType.MARKET,
            direction=signal.direction,
            entry_price=entry_price,
            quantity=quantity,
            leverage=min(strategy.leverage_limit, settings.ate_default_leverage),
            status=ExecutionStatus.PENDING,
        )
        db.add(execution)

        # Update signal status
        signal.status = SignalStatus.EXECUTING

        # Attempt execution via Hyperliquid
        private_key = None
        if user.encrypted_private_key:
            try:
                private_key = decrypt_private_key(user.encrypted_private_key)
            except ValueError:
                logger.error(f"Failed to decrypt private key for user {user.id}")
                execution.status = ExecutionStatus.FAILED
                signal.status = SignalStatus.NEW
                await db.flush()
                return execution

        if private_key:
            # For connected wallets (agent key), pass account_address so the
            # agent signs on behalf of the user's main Hyperliquid wallet.
            # For generated wallets, wallet_address IS the trading wallet.
            from app.models.enums import WalletType

            account_address = (
                user.wallet_address
                if user.wallet_type == WalletType.CONNECTED
                else None
            )

            order_result = await self.hyperliquid.place_order(
                wallet_private_key=private_key,
                symbol=signal.symbol,
                is_buy=is_buy,
                size=quantity,
                account_address=account_address,
                order_type="market",
            )

            if order_result.get("success"):
                execution.status = ExecutionStatus.FILLED
                execution.tx_hash = str(order_result.get("tx_hash", ""))
                execution.executed_at = utc_now()
                signal.status = SignalStatus.FILLED
            else:
                execution.status = ExecutionStatus.FAILED
                signal.status = SignalStatus.NEW
                logger.error(
                    f"Order failed for signal {signal.id}: {order_result.get('error')}"
                )
        else:
            execution.status = ExecutionStatus.FAILED
            signal.status = SignalStatus.NEW
            logger.warning(f"No private key available for user {user.id}")

        await db.flush()

        # Record rate limit
        ate_rate_limiter.record(rate_key)

        # Log transaction
        if execution.status == ExecutionStatus.FILLED:
            await VerificationService.log_execution_transaction(
                db=db,
                execution=execution,
                amount=quantity * entry_price,
                asset=signal.symbol,
            )

            # Send trade executed notification
            try:
                notification_service = NotificationService()
                await notification_service.send_trade_executed(
                    user=user,
                    symbol=signal.symbol,
                    direction=signal.direction.value,
                    quantity=str(quantity),
                    entry_price=str(entry_price),
                    strategy_name=strategy.name,
                )
            except Exception as e:
                logger.error("Failed to send trade executed email: %s", e)

        return execution

    async def check_drawdown(
        self,
        db: AsyncSession,
        strategy: Strategy,
        user: User,
    ) -> bool:
        """Check if strategy has exceeded its max drawdown limit.
        Returns True if drawdown limit is hit and strategy should be paused.
        """
        result = await db.execute(
            select(func.sum(Execution.pnl)).where(
                Execution.strategy_id == strategy.id,
                Execution.user_id == user.id,
                Execution.status == ExecutionStatus.FILLED,
                Execution.created_at >= utc_now() - timedelta(days=1),
            )
        )
        daily_pnl = result.scalar() or 0.0

        capital = float(strategy.capital_allocation)
        if capital <= 0:
            return False

        drawdown_percent = abs(min(0, daily_pnl)) / capital * 100

        if drawdown_percent >= strategy.max_drawdown_percent:
            strategy.is_active = False
            logger.warning(
                f"Strategy {strategy.id} auto-paused: drawdown {drawdown_percent:.1f}% "
                f">= limit {strategy.max_drawdown_percent}%"
            )

            # Send strategy paused notification
            try:
                notification_service = NotificationService()
                await notification_service.send_strategy_paused(
                    user=user,
                    strategy_name=strategy.name,
                    reason=f"Daily drawdown {drawdown_percent:.1f}% exceeded limit of {strategy.max_drawdown_percent}%",
                )
            except Exception as e:
                logger.error("Failed to send strategy paused email: %s", e)

            return True

        return False

    async def monitor_positions(self, db: AsyncSession) -> list[dict]:
        """Monitor open positions for TP/SL hits.

        Queries all FILLED executions without an exit_price, compares
        current market prices against the signal's take_profit/stop_loss
        levels, and closes positions that have been triggered.

        Returns a list of result dicts for each closed position.
        """
        # Load all open executions with their signals, strategies, and users
        result = await db.execute(
            select(Execution)
            .options(
                selectinload(Execution.signal),
                selectinload(Execution.strategy),
                selectinload(Execution.user),
            )
            .where(
                Execution.status == ExecutionStatus.FILLED,
                Execution.exit_price == None,  # noqa: E711
            )
        )
        open_executions = result.scalars().all()

        if not open_executions:
            return []

        # Batch-fetch current prices
        try:
            all_mids = await self.hyperliquid.get_all_mids()
        except Exception as e:
            logger.error("Failed to fetch market prices for TP/SL monitoring: %s", e)
            return []

        closed = []
        notification_service = NotificationService()

        for execution in open_executions:
            signal = execution.signal
            user = execution.user
            strategy = execution.strategy

            if not signal or not user:
                continue

            symbol = signal.symbol
            mid_str = all_mids.get(symbol)
            if not mid_str:
                continue

            current_price = float(mid_str)
            tp_price = float(signal.take_profit) if signal.take_profit else None
            sl_price = float(signal.stop_loss) if signal.stop_loss else None
            entry_price = float(execution.entry_price)
            is_buy = execution.direction == SignalDirection.BUY

            # Determine if TP or SL has been hit
            triggered = None
            if tp_price:
                if (is_buy and current_price >= tp_price) or (
                    not is_buy and current_price <= tp_price
                ):
                    triggered = "take_profit"
            if sl_price and triggered is None:
                if (is_buy and current_price <= sl_price) or (
                    not is_buy and current_price >= sl_price
                ):
                    triggered = "stop_loss"

            if triggered is None:
                continue

            # Close position
            private_key = None
            if user.encrypted_private_key:
                try:
                    private_key = decrypt_private_key(user.encrypted_private_key)
                except ValueError:
                    logger.error(
                        "Failed to decrypt key for user %s during TP/SL close", user.id
                    )
                    continue

            if not private_key:
                logger.warning(
                    "No private key for user %s, cannot close position %s",
                    user.id,
                    execution.id,
                )
                continue

            # Determine account_address for agent wallet mode
            from app.models.enums import WalletType

            account_address = (
                user.wallet_address
                if user.wallet_type == WalletType.CONNECTED
                else None
            )

            close_result = await self.hyperliquid.close_position(
                wallet_private_key=private_key,
                symbol=symbol,
                size=float(execution.quantity),
                is_buy=is_buy,
                account_address=account_address,
            )

            if close_result.get("success"):
                execution.exit_price = current_price
                quantity = float(execution.quantity)
                if is_buy:
                    execution.pnl = (current_price - entry_price) * quantity
                else:
                    execution.pnl = (entry_price - current_price) * quantity
                execution.status = ExecutionStatus.CLOSED

                pnl_str = f"{float(execution.pnl):+.4f}"
                strategy_name = strategy.name if strategy else "Unknown"

                # Send notification
                try:
                    if triggered == "take_profit":
                        await notification_service.send_take_profit_hit(
                            user=user,
                            symbol=symbol,
                            direction=execution.direction.value,
                            entry_price=str(entry_price),
                            exit_price=str(current_price),
                            pnl=pnl_str,
                            strategy_name=strategy_name,
                        )
                    else:
                        await notification_service.send_stop_loss_hit(
                            user=user,
                            symbol=symbol,
                            direction=execution.direction.value,
                            entry_price=str(entry_price),
                            exit_price=str(current_price),
                            pnl=pnl_str,
                            strategy_name=strategy_name,
                        )
                except Exception as e:
                    logger.error("Failed to send %s notification: %s", triggered, e)

                closed.append(
                    {
                        "execution_id": str(execution.id),
                        "symbol": symbol,
                        "triggered": triggered,
                        "entry_price": entry_price,
                        "exit_price": current_price,
                        "pnl": float(execution.pnl),
                    }
                )

                logger.info(
                    "%s hit for %s: entry=%.4f exit=%.4f pnl=%s",
                    triggered.upper().replace("_", " "),
                    symbol,
                    entry_price,
                    current_price,
                    pnl_str,
                )
            else:
                logger.error(
                    "Failed to close position %s for %s: %s",
                    execution.id,
                    triggered,
                    close_result.get("error"),
                )

        if closed:
            await db.flush()

        return closed
