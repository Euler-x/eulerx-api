"""Test ATE service — TP/SL position monitoring (Improvement #4)."""

import time
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from app.models.enums import (
    Exchange,
    ExecutionStatus,
    OrderType,
    RiskProfile,
    SignalDirection,
    SignalStatus,
    StrategyType,
    WalletType,
)
from app.models.binance_signal import BinanceSignal
from app.models.execution import Execution
from app.models.signal import Signal
from app.models.strategy import Strategy
from app.models.user import User
from app.services.ate import ATEService
from app.utils.security import encrypt_private_key
from tests.conftest import TestSessionFactory


@pytest_asyncio.fixture(loop_scope="session")
async def ate_fixtures(setup_db):
    """Create user, strategy, signal, and execution for ATE tests."""
    user_id = uuid.uuid4()
    strategy_id = uuid.uuid4()
    signal_id = uuid.uuid4()
    execution_id = uuid.uuid4()

    async with TestSessionFactory() as session:
        user = User(
            id=user_id,
            wallet_address="0x" + "d" * 40,
            wallet_address_hash="d" * 64,
            wallet_type=WalletType.GENERATED,
            encrypted_private_key=encrypt_private_key("0x" + "a" * 64),
        )
        session.add(user)

        strategy = Strategy(
            id=strategy_id,
            user_id=user_id,
            name="Test Strategy",
            strategy_type=StrategyType.MODERATE,
            risk_profile=RiskProfile.MEDIUM,
            allocation_pct=50.0,
            is_active=True,
            max_positions=5,
        )
        session.add(strategy)

        signal = Signal(
            id=signal_id,
            strategy_id=strategy_id,
            symbol="BTC",
            direction=SignalDirection.BUY,
            confidence=0.85,
            entry_price=Decimal("50000"),
            take_profit=Decimal("55000"),
            stop_loss=Decimal("48000"),
            status=SignalStatus.FILLED,
        )
        session.add(signal)

        execution = Execution(
            id=execution_id,
            signal_id=signal_id,
            user_id=user_id,
            strategy_id=strategy_id,
            wallet_address_hash="d" * 64,
            order_type=OrderType.MARKET,
            direction=SignalDirection.BUY,
            entry_price=Decimal("50000"),
            quantity=Decimal("0.1"),
            leverage=1.0,
            status=ExecutionStatus.FILLED,
        )
        session.add(execution)
        await session.commit()

    return {
        "user_id": user_id,
        "strategy_id": strategy_id,
        "signal_id": signal_id,
        "execution_id": execution_id,
    }


@pytest.mark.asyncio
async def test_monitor_positions_tp_hit(ate_fixtures, mock_hyperliquid_api):
    """BUY position: TP hit — position gone on HL, closing fill at TP price."""
    # Position no longer exists on HL (closed by native TP trigger)
    mock_hyperliquid_api["get_user_positions"].return_value = {}
    # Closing fill: sell (side "A") at 56000 with positive closedPnl
    mock_hyperliquid_api["get_user_fills"].return_value = [
        {
            "coin": "BTC",
            "side": "A",
            "px": "56000",
            "hash": "0x" + "c" * 64,
            "closedPnl": "600",
            "time": int(time.time() * 1000),
        }
    ]

    ate = ATEService()

    async with TestSessionFactory() as session:
        with patch("app.services.ate.NotificationService") as MockNotif:
            mock_service = MockNotif.return_value
            mock_service.send_take_profit_hit = AsyncMock()
            mock_service.send_stop_loss_hit = AsyncMock()

            results = await ate.monitor_positions(session)
            await session.commit()

    assert len(results) == 1
    assert results[0]["triggered"] == "take_profit"
    assert results[0]["symbol"] == "BTC"
    assert results[0]["pnl"] > 0  # Profit on BUY when price goes up


@pytest.mark.asyncio
async def test_monitor_positions_sl_hit(setup_db, mock_hyperliquid_api):
    """SELL position: SL hit — position gone on HL, closing fill at SL price."""
    user_id = uuid.uuid4()
    strategy_id = uuid.uuid4()
    signal_id = uuid.uuid4()
    execution_id = uuid.uuid4()

    async with TestSessionFactory() as session:
        user = User(
            id=user_id,
            wallet_address="0x" + "e" * 40,
            wallet_address_hash="e" * 64,
            wallet_type=WalletType.GENERATED,
            encrypted_private_key=encrypt_private_key("0x" + "b" * 64),
        )
        session.add(user)

        strategy = Strategy(
            id=strategy_id,
            user_id=user_id,
            name="SL Test Strategy",
            strategy_type=StrategyType.AGGRESSIVE,
            risk_profile=RiskProfile.HIGH,
            allocation_pct=25.0,
            is_active=True,
            max_positions=3,
        )
        session.add(strategy)

        signal = Signal(
            id=signal_id,
            strategy_id=strategy_id,
            symbol="ETH",
            direction=SignalDirection.SELL,
            confidence=0.9,
            entry_price=Decimal("3000"),
            take_profit=Decimal("2800"),
            stop_loss=Decimal("3200"),
            status=SignalStatus.FILLED,
        )
        session.add(signal)

        execution = Execution(
            id=execution_id,
            signal_id=signal_id,
            user_id=user_id,
            strategy_id=strategy_id,
            wallet_address_hash="e" * 64,
            order_type=OrderType.MARKET,
            direction=SignalDirection.SELL,
            entry_price=Decimal("3000"),
            quantity=Decimal("1.0"),
            leverage=1.0,
            status=ExecutionStatus.FILLED,
        )
        session.add(execution)
        await session.commit()

    # Position gone on HL (closed by native SL trigger)
    mock_hyperliquid_api["get_user_positions"].return_value = {}
    # Closing fill: buy (side "B") at 3300 with negative closedPnl
    mock_hyperliquid_api["get_user_fills"].return_value = [
        {
            "coin": "ETH",
            "side": "B",
            "px": "3300",
            "hash": "0x" + "c" * 64,
            "closedPnl": "-300",
            "time": int(time.time() * 1000),
        }
    ]

    ate = ATEService()

    async with TestSessionFactory() as session:
        with patch("app.services.ate.NotificationService") as MockNotif:
            mock_service = MockNotif.return_value
            mock_service.send_take_profit_hit = AsyncMock()
            mock_service.send_stop_loss_hit = AsyncMock()

            results = await ate.monitor_positions(session)
            await session.commit()

    assert len(results) >= 1
    sl_results = [
        r for r in results if r["triggered"] == "stop_loss" and r["symbol"] == "ETH"
    ]
    assert len(sl_results) == 1
    assert sl_results[0]["pnl"] < 0  # Loss on SELL when price goes up


@pytest.mark.asyncio
async def test_monitor_positions_no_trigger(setup_db, mock_hyperliquid_api):
    """Position still open on HL — no reconciliation triggered."""
    user_id = uuid.uuid4()
    strategy_id = uuid.uuid4()
    signal_id = uuid.uuid4()
    execution_id = uuid.uuid4()

    async with TestSessionFactory() as session:
        user = User(
            id=user_id,
            wallet_address="0x" + "f" * 40,
            wallet_address_hash="f" * 64,
            wallet_type=WalletType.GENERATED,
            encrypted_private_key=encrypt_private_key("0x" + "c" * 64),
        )
        session.add(user)

        strategy = Strategy(
            id=strategy_id,
            user_id=user_id,
            name="No Trigger Strategy",
            strategy_type=StrategyType.CONSERVATIVE,
            risk_profile=RiskProfile.LOW,
            allocation_pct=100.0,
            is_active=True,
            max_positions=10,
        )
        session.add(strategy)

        signal = Signal(
            id=signal_id,
            strategy_id=strategy_id,
            symbol="SOL",
            direction=SignalDirection.BUY,
            confidence=0.75,
            entry_price=Decimal("150"),
            take_profit=Decimal("180"),
            stop_loss=Decimal("130"),
            status=SignalStatus.FILLED,
        )
        session.add(signal)

        execution = Execution(
            id=execution_id,
            signal_id=signal_id,
            user_id=user_id,
            strategy_id=strategy_id,
            wallet_address_hash="f" * 64,
            order_type=OrderType.MARKET,
            direction=SignalDirection.BUY,
            entry_price=Decimal("150"),
            quantity=Decimal("10"),
            leverage=1.0,
            status=ExecutionStatus.FILLED,
        )
        session.add(execution)
        await session.commit()

    # Position still open on HL — should be skipped
    mock_hyperliquid_api["get_user_positions"].return_value = {
        "SOL": {"size": 10, "entry_px": 150, "unrealized_pnl": 100},
    }
    mock_hyperliquid_api["get_user_fills"].return_value = []

    ate = ATEService()

    async with TestSessionFactory() as session:
        with patch("app.services.ate.NotificationService") as MockNotif:
            mock_service = MockNotif.return_value
            mock_service.send_take_profit_hit = AsyncMock()
            mock_service.send_stop_loss_hit = AsyncMock()

            results = await ate.monitor_positions(session)

    sol_results = [r for r in results if r["symbol"] == "SOL"]
    assert len(sol_results) == 0


def test_validate_tpsl_prices_blocks_immediate_buy_trigger():
    signal = Signal(
        symbol="BTC",
        direction=SignalDirection.BUY,
        confidence=0.9,
        entry_price=Decimal("50000"),
        take_profit=Decimal("49900"),
        stop_loss=Decimal("48000"),
        status=SignalStatus.NEW,
    )

    valid, reason = ATEService._validate_tpsl_prices(signal, 50000)

    assert valid is False
    assert "BUY take_profit" in reason


def test_validate_tpsl_prices_blocks_immediate_sell_trigger():
    signal = Signal(
        symbol="ETH",
        direction=SignalDirection.SELL,
        confidence=0.9,
        entry_price=Decimal("3000"),
        take_profit=Decimal("2800"),
        stop_loss=Decimal("2900"),
        status=SignalStatus.NEW,
    )

    valid, reason = ATEService._validate_tpsl_prices(signal, 3000)

    assert valid is False
    assert "SELL stop_loss" in reason


@pytest.mark.asyncio
async def test_binance_performance_guard_blocks_losing_sl_heavy_history(setup_db):
    user_id = uuid.uuid4()
    strategy_id = uuid.uuid4()
    target_signal_id = uuid.uuid4()

    async with TestSessionFactory() as session:
        user = User(
            id=user_id,
            wallet_address_hash="binanceguard" * 5 + "abcd",
            wallet_type=WalletType.CONNECTED,
        )
        session.add(user)

        strategy = Strategy(
            id=strategy_id,
            user_id=user_id,
            name="Binance Guard Strategy",
            strategy_type=StrategyType.MODERATE,
            risk_profile=RiskProfile.MEDIUM,
            allocation_pct=25.0,
            is_active=True,
            max_positions=5,
        )
        session.add(strategy)

        for idx in range(6):
            signal_id = uuid.uuid4()
            signal = BinanceSignal(
                id=signal_id,
                symbol="ETHUSDT",
                direction=SignalDirection.SELL,
                confidence=0.9,
                entry_price=Decimal("3000"),
                take_profit=Decimal("2850"),
                stop_loss=Decimal("3060"),
                status=SignalStatus.FILLED,
            )
            session.add(signal)
            session.add(
                Execution(
                    id=uuid.uuid4(),
                    binance_signal_id=signal_id,
                    user_id=user_id,
                    strategy_id=strategy_id,
                    wallet_address_hash="binanceguard" * 5 + "abcd",
                    order_type=OrderType.MARKET,
                    direction=SignalDirection.SELL,
                    entry_price=Decimal("3000"),
                    exit_price=Decimal("3060" if idx < 4 else "2850"),
                    quantity=Decimal("1"),
                    leverage=1.0,
                    pnl=Decimal("-60" if idx < 4 else "25"),
                    status=ExecutionStatus.CLOSED,
                    close_reason="stop_loss" if idx < 4 else "take_profit",
                    exchange=Exchange.BINANCE,
                )
            )

        target_signal = BinanceSignal(
            id=target_signal_id,
            symbol="ETHUSDT",
            direction=SignalDirection.SELL,
            confidence=0.92,
            entry_price=Decimal("3000"),
            take_profit=Decimal("2850"),
            stop_loss=Decimal("3060"),
            status=SignalStatus.NEW,
        )
        session.add(target_signal)
        await session.commit()

    ate = ATEService()
    async with TestSessionFactory() as session:
        signal = await session.get(BinanceSignal, target_signal_id)
        allowed, reason = await ate._evaluate_binance_performance_guard(session, signal)

    assert allowed is False
    assert "SL 4 > TP 2" in reason
    assert "total_pnl" in reason


@pytest.mark.asyncio
async def test_binance_performance_guard_allows_insufficient_history(setup_db):
    signal = BinanceSignal(
        id=uuid.uuid4(),
        symbol="NEWUSDT",
        direction=SignalDirection.BUY,
        confidence=0.92,
        entry_price=Decimal("10"),
        take_profit=Decimal("11"),
        stop_loss=Decimal("9.5"),
        status=SignalStatus.NEW,
    )

    ate = ATEService()
    async with TestSessionFactory() as session:
        allowed, reason = await ate._evaluate_binance_performance_guard(session, signal)

    assert allowed is True
    assert "Not enough Binance history" in reason
