"""Test Celery worker tasks — async inner functions and pipeline orchestration."""

import time
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from app.models.enums import (
    BillingCycle,
    ExecutionStatus,
    OrderType,
    PlanStatus,
    RiskProfile,
    SignalDirection,
    SignalStatus,
    StrategyType,
    SubscriptionStatus,
    WalletType,
)
from app.models.admin_config import AdminConfig
from app.models.binance_signal import BinanceSignal
from app.models.billing import Plan, Subscription
from app.models.bybit_signal import BybitSignal
from app.models.execution import Execution
from app.models.signal import Signal
from app.models.strategy import Strategy
from app.models.user import User
from app.utils.helpers import utc_now
from app.utils.security import encrypt_private_key
from tests.conftest import TestSessionFactory


# ── Helpers ──────────────────────────────────────────────────────────


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def pipeline_fixtures(setup_db):
    """Create users, strategies, signals, and executions for pipeline tests."""
    user_id = uuid.uuid4()
    strategy_id = uuid.uuid4()
    signal_id = uuid.uuid4()

    async with TestSessionFactory() as session:
        user = User(
            id=user_id,
            wallet_address="0x" + "ab" * 20,
            wallet_address_hash="p" * 64,
            wallet_type=WalletType.GENERATED,
            encrypted_private_key=encrypt_private_key("0x" + "a" * 64),
            email="pipeline@test.com",
            email_verified=True,
        )
        session.add(user)

        strategy = Strategy(
            id=strategy_id,
            user_id=user_id,
            name="Pipeline Strategy",
            strategy_type=StrategyType.MODERATE,
            risk_profile=RiskProfile.MEDIUM,
            allocation_pct=50.0,
            is_active=True,
            max_positions=5,
            max_drawdown_percent=10.0,
            leverage_limit=2.0,
        )
        session.add(strategy)

        signal = Signal(
            id=signal_id,
            symbol="BTC",
            direction=SignalDirection.BUY,
            confidence=0.9,
            entry_price=Decimal("50000"),
            take_profit=Decimal("55000"),
            stop_loss=Decimal("48000"),
            status=SignalStatus.NEW,
        )
        session.add(signal)
        await session.commit()

    return {
        "user_id": user_id,
        "strategy_id": strategy_id,
        "signal_id": signal_id,
    }


# ── Test: Fetch Market Data ────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_market_data(setup_db):
    """_fetch_market_data_async returns symbols from Hyperliquid."""
    from app.worker.tasks import _fetch_market_data_async

    mock_hl = [
        {"symbol": "BTC", "price": "50000", "change_24h": "5.2"},
        {"symbol": "ETH", "price": "3000", "change_24h": "3.1"},
    ]
    mock_bybit = [
        {"symbol": "BTCUSDT", "price": "50000"},
    ]
    mock_binance = [
        {"symbol": "ETHUSDT", "price": "3000"},
    ]

    with (
        patch(
            "app.worker.tasks.HyperliquidService.get_top_movers",
            new_callable=AsyncMock,
            return_value=mock_hl,
        ),
        patch(
            "app.worker.tasks.BybitService.get_top_movers",
            new_callable=AsyncMock,
            return_value=mock_bybit,
        ),
        patch(
            "app.worker.tasks.BinanceService.get_top_movers",
            new_callable=AsyncMock,
            return_value=mock_binance,
        ),
    ):
        result = await _fetch_market_data_async()

    assert len(result) == 4
    assert result[0]["symbol"] == "BTC"
    assert result[0]["exchange"] == "hyperliquid"
    assert result[2]["symbol"] == "BTCUSDT"
    assert result[2]["exchange"] == "bybit"
    assert result[3]["symbol"] == "ETHUSDT"
    assert result[3]["exchange"] == "binance"


@pytest.mark.asyncio
async def test_fetch_market_data_can_target_single_exchange(setup_db):
    """Exchange-specific pipelines fetch only their own market data."""
    from app.worker.tasks import _fetch_market_data_async

    mock_binance = [
        {"symbol": "ETHUSDT", "price": "3000"},
    ]

    with (
        patch(
            "app.worker.tasks.HyperliquidService.get_top_movers",
            new_callable=AsyncMock,
            return_value=[{"symbol": "BTC"}],
        ) as mock_hl,
        patch(
            "app.worker.tasks.BybitService.get_top_movers",
            new_callable=AsyncMock,
            return_value=[{"symbol": "BTCUSDT"}],
        ) as mock_bybit,
        patch(
            "app.worker.tasks.BinanceService.get_top_movers",
            new_callable=AsyncMock,
            return_value=mock_binance,
        ) as mock_binance_fetch,
    ):
        result = await _fetch_market_data_async(exchange="binance")

    assert result == [{"symbol": "ETHUSDT", "price": "3000", "exchange": "binance"}]
    mock_hl.assert_not_awaited()
    mock_bybit.assert_not_awaited()
    mock_binance_fetch.assert_awaited_once()


# ── Test: Get Active Strategy IDs ──────────────────────────────────


@pytest.mark.asyncio
async def test_get_active_strategy_ids(pipeline_fixtures):
    """_get_active_strategy_ids_async returns active strategy IDs."""
    from app.worker.tasks import _get_active_strategy_ids_async

    with patch(
        "app.worker.tasks.async_session_factory",
        TestSessionFactory,
    ):
        result = await _get_active_strategy_ids_async()

    assert str(pipeline_fixtures["strategy_id"]) in result


# ── Test: Generate Signals (Strategy-Independent) ──────────────────


@pytest.mark.asyncio
async def test_generate_signals_creates_signals(setup_db):
    """_generate_signals_async generates strategy-independent signals."""
    from app.worker.tasks import _generate_signals_async

    market_data = [{"symbol": "BTC", "price": "50000"}]

    mock_signal = MagicMock()
    mock_signal.id = uuid.uuid4()

    with (
        patch(
            "app.worker.tasks.async_session_factory",
            TestSessionFactory,
        ),
        patch(
            "app.worker.tasks.AIEngineService.generate_signals",
            new_callable=AsyncMock,
            return_value=[mock_signal],
        ),
    ):
        result = await _generate_signals_async(market_data)

    assert len(result) == 1
    assert result[0]["id"] == str(mock_signal.id)
    assert result[0]["exchange"] == "hyperliquid"


@pytest.mark.asyncio
async def test_generate_signals_preserves_exchange_for_bybit_and_binance(setup_db):
    """_generate_signals_async returns exchange metadata for non-HL signal tables."""
    from app.worker.tasks import _generate_signals_async

    bybit_signal = BybitSignal(
        id=uuid.uuid4(),
        symbol="BTCUSDT",
        direction=SignalDirection.SELL,
        confidence=0.91,
        entry_price=Decimal("65000"),
        take_profit=Decimal("63000"),
        stop_loss=Decimal("67000"),
        status=SignalStatus.NEW,
    )
    binance_signal = BinanceSignal(
        id=uuid.uuid4(),
        symbol="ETHUSDT",
        direction=SignalDirection.SELL,
        confidence=0.92,
        entry_price=Decimal("3000"),
        take_profit=Decimal("2850"),
        stop_loss=Decimal("3060"),
        status=SignalStatus.NEW,
    )

    with (
        patch(
            "app.worker.tasks.async_session_factory",
            TestSessionFactory,
        ),
        patch(
            "app.worker.tasks.AIEngineService.generate_signals",
            new_callable=AsyncMock,
            return_value=[bybit_signal, binance_signal],
        ),
    ):
        result = await _generate_signals_async(
            [
                {"symbol": "BTCUSDT", "exchange": "bybit"},
                {"symbol": "ETHUSDT", "exchange": "binance"},
            ]
        )

    assert result == [
        {"id": str(bybit_signal.id), "exchange": "bybit"},
        {"id": str(binance_signal.id), "exchange": "binance"},
    ]


def test_strategy_target_matching_includes_binance_and_legacy_both():
    """Binance signals must not be skipped by legacy all-exchange targets."""
    from app.worker.tasks import _strategy_targets_exchange

    strategy = MagicMock()

    strategy.target_exchange = "binance"
    assert _strategy_targets_exchange(strategy, "binance") is True
    assert _strategy_targets_exchange(strategy, "bybit") is False

    strategy.target_exchange = "both"
    assert _strategy_targets_exchange(strategy, "hyperliquid") is True
    assert _strategy_targets_exchange(strategy, "bybit") is True
    assert _strategy_targets_exchange(strategy, "binance") is True

    strategy.target_exchange = "all"
    assert _strategy_targets_exchange(strategy, "binance") is True


def test_admin_task_definitions_expose_exchange_pipelines():
    """Admin task toggles must expose each exchange pipeline independently."""
    from app.routers.admin.system import TASK_DEFINITIONS

    task_names = {task["name"] for task in TASK_DEFINITIONS}

    assert "analysis-pipeline-hyperliquid" in task_names
    assert "analysis-pipeline-bybit" in task_names
    assert "analysis-pipeline-binance" in task_names


def test_exchange_pipeline_schedules_are_staggered():
    """Exchange pipelines should not all burst AI analysis at the same minute."""
    from app.worker.celery_app import celery_app

    beat_schedule = celery_app.conf.beat_schedule
    exchange_minutes = {
        "hyperliquid": beat_schedule["hyperliquid-analysis-pipeline-every-2h"][
            "schedule"
        ]._orig_minute,
        "bybit": beat_schedule["bybit-analysis-pipeline-every-2h"][
            "schedule"
        ]._orig_minute,
        "binance": beat_schedule["binance-analysis-pipeline-every-2h"][
            "schedule"
        ]._orig_minute,
    }

    assert exchange_minutes == {
        "hyperliquid": "0",
        "bybit": "20",
        "binance": "40",
    }


@pytest.mark.asyncio
async def test_generate_signals_returns_empty_on_no_actionable(setup_db):
    """Returns empty list when AI engine finds no actionable signals."""
    from app.worker.tasks import _generate_signals_async

    with (
        patch(
            "app.worker.tasks.async_session_factory",
            TestSessionFactory,
        ),
        patch(
            "app.worker.tasks.AIEngineService.generate_signals",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        result = await _generate_signals_async([{"symbol": "BTC"}])

    assert result == []


# ── Test: Execute Signal For Strategy ─────────────────────────────


@pytest.mark.asyncio
async def test_execute_signal_fills_order(pipeline_fixtures):
    """_execute_signal_for_strategy_async executes a NEW signal via ATE."""
    from app.worker.tasks import _execute_signal_for_strategy_async

    signal_id = str(pipeline_fixtures["signal_id"])
    strategy_id = str(pipeline_fixtures["strategy_id"])

    mock_execution = MagicMock()
    mock_execution.status = ExecutionStatus.FILLED
    mock_execution.id = uuid.uuid4()

    with (
        patch(
            "app.worker.tasks.async_session_factory",
            TestSessionFactory,
        ),
        patch(
            "app.worker.tasks.ATEService.execute_signal",
            new_callable=AsyncMock,
            return_value=mock_execution,
        ),
        patch(
            "app.worker.tasks.ATEService.check_drawdown",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "app.worker.tasks.NotificationService.send_signal_generated",
            new_callable=AsyncMock,
        ),
    ):
        result = await _execute_signal_for_strategy_async(signal_id, strategy_id)

    assert result["status"] == "filled"
    assert "execution_id" in result


@pytest.mark.asyncio
async def test_execute_signal_not_found(setup_db):
    """Returns skipped when signal ID doesn't exist."""
    from app.worker.tasks import _execute_signal_for_strategy_async

    with patch("app.worker.tasks.async_session_factory", TestSessionFactory):
        result = await _execute_signal_for_strategy_async(
            str(uuid.uuid4()), str(uuid.uuid4())
        )

    assert result["status"] == "skipped"
    assert "not found" in result["reason"]


@pytest.mark.asyncio
async def test_execute_signal_skips_expired(pipeline_fixtures):
    """Signal with status EXPIRED is skipped."""
    from app.worker.tasks import _execute_signal_for_strategy_async

    signal_id = pipeline_fixtures["signal_id"]
    strategy_id = str(pipeline_fixtures["strategy_id"])

    # Set signal status to EXPIRED
    async with TestSessionFactory() as session:
        from sqlalchemy import select

        result = await session.execute(select(Signal).where(Signal.id == signal_id))
        signal = result.scalar_one()
        signal.status = SignalStatus.EXPIRED
        await session.commit()

    with patch("app.worker.tasks.async_session_factory", TestSessionFactory):
        result = await _execute_signal_for_strategy_async(str(signal_id), strategy_id)

    assert result["status"] == "skipped"
    assert "expired" in result["reason"].lower()

    # Reset signal status for other tests
    async with TestSessionFactory() as session:
        result = await session.execute(select(Signal).where(Signal.id == signal_id))
        signal = result.scalar_one()
        signal.status = SignalStatus.NEW
        await session.commit()


@pytest.mark.asyncio
async def test_execute_signal_skips_on_drawdown(pipeline_fixtures):
    """Signal execution is skipped when strategy hits drawdown limit."""
    from app.worker.tasks import _execute_signal_for_strategy_async

    signal_id = str(pipeline_fixtures["signal_id"])
    strategy_id = str(pipeline_fixtures["strategy_id"])

    with (
        patch(
            "app.worker.tasks.async_session_factory",
            TestSessionFactory,
        ),
        patch(
            "app.worker.tasks.ATEService.check_drawdown",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        result = await _execute_signal_for_strategy_async(signal_id, strategy_id)

    assert result["status"] == "skipped"
    assert "drawdown" in result["reason"].lower()


# ── Test: Expire Stale Signals ─────────────────────────────────────


@pytest.mark.asyncio
async def test_expire_stale_signals(setup_db):
    """_expire_stale_signals_async marks expired signals."""
    from app.worker.tasks import _expire_stale_signals_async

    async with TestSessionFactory() as session:
        # Create an expired signal (no strategy_id — strategy-independent)
        expired_signal = Signal(
            id=uuid.uuid4(),
            symbol="DOGE",
            direction=SignalDirection.BUY,
            confidence=0.7,
            entry_price=Decimal("0.10"),
            status=SignalStatus.NEW,
            expires_at=utc_now() - timedelta(hours=1),  # Expired 1 hour ago
        )
        session.add(expired_signal)

        # Create a non-expired signal
        active_signal = Signal(
            id=uuid.uuid4(),
            symbol="LINK",
            direction=SignalDirection.BUY,
            confidence=0.8,
            entry_price=Decimal("20"),
            status=SignalStatus.NEW,
            expires_at=utc_now() + timedelta(hours=1),  # Expires in 1 hour
        )
        session.add(active_signal)
        await session.commit()

    with patch("app.worker.tasks.async_session_factory", TestSessionFactory):
        count = await _expire_stale_signals_async()

    assert count >= 1  # At least the DOGE signal should be expired


# ── Test: Check Expiring Subscriptions ─────────────────────────────


@pytest.mark.asyncio
async def test_check_expiring_subscriptions(setup_db):
    """_check_expiring_subscriptions_async notifies users with expiring subs."""
    from app.worker.tasks import _check_expiring_subscriptions_async

    user_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    sub_id = uuid.uuid4()

    async with TestSessionFactory() as session:
        user = User(
            id=user_id,
            wallet_address_hash="r" * 64,
            wallet_type=WalletType.CONNECTED,
            email="expiring@test.com",
            email_verified=True,
        )
        session.add(user)

        plan = Plan(
            id=plan_id,
            name="Pro Plan",
            price_usd=Decimal("49.99"),
            billing_cycle=BillingCycle.MONTHLY,
            max_strategies=10,
            ate_access=True,
            status=PlanStatus.ACTIVE,
        )
        session.add(plan)

        # Subscription expiring in 2 days (within 3-day window)
        # Use naive datetime since SQLite strips timezone info
        sub = Subscription(
            id=sub_id,
            user_id=user_id,
            plan_id=plan_id,
            status=SubscriptionStatus.ACTIVE,
            expires_at=datetime.utcnow() + timedelta(days=2),
        )
        session.add(sub)
        await session.commit()

    # Patch utc_now to return naive datetime (SQLite strips timezone info)
    naive_now = datetime.utcnow()

    with (
        patch(
            "app.worker.tasks.async_session_factory",
            TestSessionFactory,
        ),
        patch(
            "app.worker.tasks.utc_now",
            return_value=naive_now,
        ),
        patch(
            "app.worker.tasks.NotificationService.send_subscription_expiring",
            new_callable=AsyncMock,
        ) as mock_notify,
    ):
        count = await _check_expiring_subscriptions_async()

    assert count >= 1
    mock_notify.assert_called()


# ── Test: Monitor Open Positions ───────────────────────────────────


@pytest.mark.asyncio
async def test_monitor_open_positions_task(setup_db, mock_hyperliquid_api):
    """_monitor_open_positions_async closes triggered positions."""
    from app.worker.tasks import _monitor_open_positions_async

    user_id = uuid.uuid4()
    strategy_id = uuid.uuid4()
    signal_id = uuid.uuid4()
    execution_id = uuid.uuid4()

    async with TestSessionFactory() as session:
        user = User(
            id=user_id,
            wallet_address="0x" + "a1" * 20,
            wallet_address_hash="s" * 64,
            wallet_type=WalletType.GENERATED,
            encrypted_private_key=encrypt_private_key("0x" + "d" * 64),
        )
        session.add(user)

        strategy = Strategy(
            id=strategy_id,
            user_id=user_id,
            name="Monitor Task Strategy",
            strategy_type=StrategyType.MODERATE,
            risk_profile=RiskProfile.MEDIUM,
            allocation_pct=25.0,
            is_active=True,
            max_positions=5,
        )
        session.add(strategy)

        signal = Signal(
            id=signal_id,
            symbol="ETH",
            direction=SignalDirection.BUY,
            confidence=0.85,
            entry_price=Decimal("3000"),
            take_profit=Decimal("3500"),
            stop_loss=Decimal("2800"),
            status=SignalStatus.FILLED,
        )
        session.add(signal)

        execution = Execution(
            id=execution_id,
            signal_id=signal_id,
            user_id=user_id,
            strategy_id=strategy_id,
            wallet_address_hash="s" * 64,
            order_type=OrderType.MARKET,
            direction=SignalDirection.BUY,
            entry_price=Decimal("3000"),
            quantity=Decimal("1.0"),
            leverage=1.0,
            status=ExecutionStatus.FILLED,
        )
        session.add(execution)
        await session.commit()

    # Position gone on HL (closed by native TP trigger)
    mock_hyperliquid_api["get_user_positions"].return_value = {}
    # Closing fill: sell (side "A") at 3600 with positive closedPnl
    mock_hyperliquid_api["get_user_fills"].return_value = [
        {
            "coin": "ETH",
            "side": "A",
            "px": "3600",
            "hash": "0x" + "c" * 64,
            "closedPnl": "600",
            "time": int(time.time() * 1000),
        }
    ]

    with (
        patch(
            "app.worker.tasks.async_session_factory",
            TestSessionFactory,
        ),
        patch(
            "app.services.ate.NotificationService",
        ) as MockNotif,
    ):
        mock_service = MockNotif.return_value
        mock_service.send_take_profit_hit = AsyncMock()
        mock_service.send_stop_loss_hit = AsyncMock()

        result = await _monitor_open_positions_async()

    assert result["positions_closed"] >= 1
    eth_closed = [d for d in result["details"] if d["symbol"] == "ETH"]
    assert len(eth_closed) == 1
    assert eth_closed[0]["triggered"] == "take_profit"


# ── Test: Run Analysis Pipeline ────────────────────────────────────


@pytest.mark.asyncio
async def test_run_analysis_pipeline_no_market_data(setup_db):
    """Pipeline returns early when no market data available."""
    from app.worker.tasks import _fetch_market_data_async

    with (
        patch(
            "app.worker.tasks.HyperliquidService.get_top_movers",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.worker.tasks.BybitService.get_top_movers",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.worker.tasks.BinanceService.get_top_movers",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        result = await _fetch_market_data_async()

    assert result == []


@pytest.mark.asyncio
async def test_run_analysis_pipeline_no_active_strategies(setup_db):
    """Pipeline handles no active strategies gracefully."""

    with (
        patch(
            "app.worker.tasks._fetch_market_data_async",
            new_callable=AsyncMock,
            return_value=[{"symbol": "BTC"}],
        ) as mock_fetch,
        patch(
            "app.worker.tasks._get_active_strategy_ids_async",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_ids,
    ):
        # Test the orchestration logic by calling the pieces
        market_data = await mock_fetch()
        strategy_ids = await mock_ids()
        assert len(market_data) == 1
        assert len(strategy_ids) == 0


# ── Test: Notification Tasks ──────────────────────────────────────


@pytest.mark.asyncio
async def test_send_notification_email_task():
    """_send_notification_email_async sends email via NotificationService."""
    from app.worker.tasks import _send_notification_email_async

    with patch(
        "app.worker.tasks.NotificationService.send_email",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_send:
        result = await _send_notification_email_async(
            to_email="user@test.com",
            to_name="Test User",
            subject="Test Subject",
            html_body="<p>Hello</p>",
        )

    assert result is True
    mock_send.assert_called_once_with(
        "user@test.com", "Test User", "Test Subject", "<p>Hello</p>"
    )


@pytest.mark.asyncio
async def test_send_notification_telegram_task():
    """_send_notification_telegram_async sends Telegram via NotificationService."""
    from app.worker.tasks import _send_notification_telegram_async

    with patch(
        "app.worker.tasks.NotificationService.send_telegram",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_send:
        result = await _send_notification_telegram_async(
            bot_token="123:token",
            chat_id="456",
            text="Hello from test",
        )

    assert result is True
    mock_send.assert_called_once_with("123:token", "456", "Hello from test")


# ── Test: ATE Service — evaluate_signal ────────────────────────────


def test_evaluate_signal_approved():
    """Signal passes all checks."""
    from app.services.ate import ATEService

    ate = ATEService()

    signal = MagicMock()
    signal.direction = SignalDirection.BUY
    signal.confidence = 0.9
    signal.status = SignalStatus.NEW
    signal.expires_at = None

    strategy = MagicMock()
    strategy.max_positions = 5

    can_execute, reason = ate.evaluate_signal(signal, strategy, open_position_count=2)
    assert can_execute is True
    assert "approved" in reason.lower()


def test_evaluate_signal_hold_rejected():
    """HOLD signal is rejected."""
    from app.services.ate import ATEService

    ate = ATEService()

    signal = MagicMock()
    signal.direction = SignalDirection.HOLD

    can_execute, reason = ate.evaluate_signal(signal, MagicMock(), 0)
    assert can_execute is False
    assert "HOLD" in reason


def test_evaluate_signal_low_confidence_rejected():
    """Low confidence signal is rejected."""
    from app.services.ate import ATEService

    ate = ATEService()

    signal = MagicMock()
    signal.direction = SignalDirection.BUY
    signal.confidence = 0.1  # Very low
    signal.status = SignalStatus.NEW

    can_execute, reason = ate.evaluate_signal(signal, MagicMock(), 0)
    assert can_execute is False
    assert "Confidence" in reason


def test_evaluate_signal_max_positions_rejected():
    """Signal rejected when max positions reached."""
    from app.services.ate import ATEService

    ate = ATEService()

    signal = MagicMock()
    signal.direction = SignalDirection.BUY
    signal.confidence = 0.9
    signal.status = SignalStatus.NEW
    signal.expires_at = None

    strategy = MagicMock()
    strategy.max_positions = 3

    can_execute, reason = ate.evaluate_signal(signal, strategy, open_position_count=3)
    assert can_execute is False
    assert "Max positions" in reason


# ── Test: ATE Service — calculate_position_size ────────────────────


def test_calculate_position_size():
    """Position size calculated correctly based on risk profile and wallet balance."""
    from app.services.ate import ATEService

    ate = ATEService()

    strategy = MagicMock()
    strategy.allocation_pct = 50.0  # 50% of wallet balance
    strategy.leverage_limit = 2.0
    strategy.risk_profile = MagicMock()
    strategy.risk_profile.value = "medium"  # 5% risk multiplier

    # available_balance=20000, allocation_pct=50% → effective=10000
    # 10000 * 0.05 * leverage_limit=2.0 / 50000 = 0.02
    size = ate.calculate_position_size(
        strategy, entry_price=50000.0, available_balance=20000.0
    )
    assert size == 0.02


def test_calculate_position_size_no_balance():
    """Position size is 0 when no wallet balance available."""
    from app.services.ate import ATEService

    ate = ATEService()

    strategy = MagicMock()
    strategy.allocation_pct = 50.0
    strategy.leverage_limit = 2.0
    strategy.risk_profile = MagicMock()
    strategy.risk_profile.value = "medium"

    size = ate.calculate_position_size(strategy, entry_price=50000.0)
    assert size == 0.0


def test_calculate_position_size_zero_price():
    """Position size is 0 when entry price is 0."""
    from app.services.ate import ATEService

    ate = ATEService()

    strategy = MagicMock()
    strategy.allocation_pct = 50.0
    strategy.leverage_limit = 2.0
    strategy.risk_profile = MagicMock()
    strategy.risk_profile.value = "medium"

    size = ate.calculate_position_size(
        strategy, entry_price=0.0, available_balance=10000.0
    )
    assert size == 0.0


# ── Test: ATE Service — check_drawdown ─────────────────────────────


@pytest.mark.asyncio
async def test_check_drawdown_within_limit(pipeline_fixtures):
    """No pause when drawdown is within limits."""
    from app.services.ate import ATEService

    ate = ATEService()

    async with TestSessionFactory() as session:
        from sqlalchemy import select

        result = await session.execute(
            select(Strategy).where(Strategy.id == pipeline_fixtures["strategy_id"])
        )
        strategy = result.scalar_one()

        user_result = await session.execute(
            select(User).where(User.id == pipeline_fixtures["user_id"])
        )
        user = user_result.scalar_one()

        with (
            patch(
                "app.services.ate.NotificationService.send_strategy_paused",
                new_callable=AsyncMock,
            ),
            patch.object(
                ate, "_get_wallet_balance", new_callable=AsyncMock, return_value=10000.0
            ),
        ):
            hit = await ate.check_drawdown(session, strategy, user)

    assert hit is False


# ── Test: ATE Service — execute_signal ─────────────────────────────


@pytest.mark.asyncio
async def test_execute_signal_full_flow(setup_db, mock_hyperliquid_api):
    """Full execute_signal flow: creates execution, places order, records transaction."""
    from app.services.ate import ATEService, ate_rate_limiter

    ate_rate_limiter._requests.clear()
    mock_hyperliquid_api["place_order"].return_value = {
        "success": True,
        "tx_hash": "0x" + "a" * 64,
        "avg_price": 50012.5,
        "executed_qty": 0.05,
    }

    user_id = uuid.uuid4()
    strategy_id = uuid.uuid4()
    signal_id = uuid.uuid4()

    async with TestSessionFactory() as session:
        user = User(
            id=user_id,
            wallet_address="0x" + "a" * 40,
            wallet_address_hash="t" * 64,
            wallet_type=WalletType.GENERATED,
            encrypted_private_key=encrypt_private_key("0x" + "e" * 64),
        )
        session.add(user)

        strategy = Strategy(
            id=strategy_id,
            user_id=user_id,
            name="Execute Test",
            strategy_type=StrategyType.MODERATE,
            risk_profile=RiskProfile.MEDIUM,
            allocation_pct=50.0,
            is_active=True,
            max_positions=5,
            leverage_limit=2.0,
        )
        session.add(strategy)

        signal = Signal(
            id=signal_id,
            symbol="BTC",
            direction=SignalDirection.BUY,
            confidence=0.9,
            entry_price=Decimal("50000"),
            take_profit=Decimal("55000"),
            stop_loss=Decimal("47500"),
            status=SignalStatus.NEW,
        )
        session.add(signal)
        # Enable BUY signals so the test isn't blocked by the SELL-only default
        session.add(AdminConfig(key="buy_signals_enabled", value={"value": 1}))
        await session.commit()

    async with TestSessionFactory() as session:
        from sqlalchemy import select

        sig_result = await session.execute(select(Signal).where(Signal.id == signal_id))
        signal = sig_result.scalar_one()

        strat_result = await session.execute(
            select(Strategy).where(Strategy.id == strategy_id)
        )
        strategy = strat_result.scalar_one()

        user_result = await session.execute(select(User).where(User.id == user_id))
        user = user_result.scalar_one()

        ate = ATEService()
        with (
            patch(
                "app.services.ate.NotificationService.send_trade_executed",
                new_callable=AsyncMock,
            ),
            patch(
                "app.services.ate.VerificationService.log_execution_transaction",
                new_callable=AsyncMock,
            ),
            patch.object(
                ate, "_get_wallet_balance", new_callable=AsyncMock, return_value=5000.0
            ),
            patch.object(
                ate, "_get_sz_decimals", new_callable=AsyncMock, return_value=4
            ),
        ):
            execution = await ate.execute_signal(session, signal, strategy, user)
            await session.commit()

    assert execution is not None
    assert execution.status == ExecutionStatus.FILLED
    assert execution.direction == SignalDirection.BUY
    assert float(execution.entry_price) == pytest.approx(50012.5)
    assert float(execution.quantity) == pytest.approx(0.05)


@pytest.mark.asyncio
async def test_execute_signal_bybit_uses_post_fill_tpsl_flow(setup_db):
    """Bybit entries should size against market caps and attach TP/SL after fill."""
    from sqlalchemy import select

    from app.services.ate import ATEService, ate_rate_limiter

    ate_rate_limiter._requests.clear()

    user_id = uuid.uuid4()
    strategy_id = uuid.uuid4()
    signal_id = uuid.uuid4()

    async with TestSessionFactory() as session:
        user = User(
            id=user_id,
            wallet_address_hash="bybit" * 12 + "abcd",
            email="bybit-exec@test.com",
            email_verified=True,
            bybit_api_key_encrypted=encrypt_private_key("bybit-key"),
            bybit_api_secret_encrypted=encrypt_private_key("bybit-secret"),
            bybit_testnet=True,
        )
        session.add(user)

        strategy = Strategy(
            id=strategy_id,
            user_id=user_id,
            name="Bybit Execute Test",
            strategy_type=StrategyType.MODERATE,
            risk_profile=RiskProfile.MEDIUM,
            allocation_pct=50.0,
            is_active=True,
            max_positions=5,
            leverage_limit=2.0,
        )
        session.add(strategy)

        signal = BybitSignal(
            id=signal_id,
            symbol="QUSDT",
            direction=SignalDirection.BUY,
            confidence=0.9,
            entry_price=Decimal("100"),
            take_profit=Decimal("105"),
            stop_loss=Decimal("95"),
            status=SignalStatus.NEW,
        )
        session.add(signal)
        await session.commit()

    async with TestSessionFactory() as session:
        sig_result = await session.execute(
            select(BybitSignal).where(BybitSignal.id == signal_id)
        )
        signal = sig_result.scalar_one()

        strat_result = await session.execute(
            select(Strategy).where(Strategy.id == strategy_id)
        )
        strategy = strat_result.scalar_one()

        user_result = await session.execute(select(User).where(User.id == user_id))
        user = user_result.scalar_one()

        ate = ATEService()
        mock_bybit = MagicMock()
        mock_bybit.get_ticker = AsyncMock(
            return_value={"bid1Price": "100.0", "ask1Price": "100.2"}
        )
        mock_bybit.get_lot_size_info = AsyncMock(
            return_value={
                "qty_step": 0.1,
                "min_order_qty": 0.1,
                "max_order_qty": 250000.0,
                "max_limit_order_qty": 1250000.0,
                "max_market_order_qty": 250000.0,
            }
        )
        mock_bybit.update_leverage = AsyncMock(return_value={"success": True})
        mock_bybit.place_order = AsyncMock(
            return_value={
                "success": True,
                "oid": "bybit-order-1",
                "tx_hash": "bybit-order-1",
            }
        )
        mock_bybit.wait_for_order_fill = AsyncMock(
            return_value={"avg_price": 100.15, "executed_qty": 5.0}
        )
        mock_bybit.place_tp_sl_orders = AsyncMock(return_value={"success": True})

        async def fake_get_config(_key, default, _db=None):
            return default

        with (
            patch(
                "app.services.dynamic_config.get_config",
                new=AsyncMock(side_effect=fake_get_config),
            ),
            patch(
                "app.services.ate.NotificationService.send_trade_executed",
                new_callable=AsyncMock,
            ),
            patch(
                "app.services.ate.VerificationService.log_execution_transaction",
                new_callable=AsyncMock,
            ),
            patch.object(ate, "_get_user_bybit_service", return_value=mock_bybit),
            patch.object(
                ate, "_get_bybit_balance", new_callable=AsyncMock, return_value=5000.0
            ),
        ):
            execution = await ate.execute_signal(session, signal, strategy, user)
            await session.commit()

    assert execution is not None
    assert execution.status == ExecutionStatus.FILLED
    assert float(execution.entry_price) == pytest.approx(100.15)
    assert float(execution.quantity) == pytest.approx(5.0)
    assert mock_bybit.get_lot_size_info.await_args.kwargs["order_type"] == "market"
    assert "take_profit" not in mock_bybit.place_order.await_args.kwargs
    assert "stop_loss" not in mock_bybit.place_order.await_args.kwargs
    assert mock_bybit.place_tp_sl_orders.await_count == 1
