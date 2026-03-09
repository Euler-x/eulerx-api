"""Test ATE service — TP/SL position monitoring (Improvement #4)."""

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from app.models.enums import (
    ExecutionStatus,
    OrderType,
    RiskProfile,
    SignalDirection,
    SignalStatus,
    StrategyType,
    WalletType,
)
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
    """BUY position: TP hit when current price >= take_profit."""
    # Set current price above TP (55000)
    mock_hyperliquid_api["get_all_mids"].return_value = {"BTC": "56000.0"}

    ate = ATEService()

    with patch.object(ate, "hyperliquid", wraps=ate.hyperliquid):
        ate.hyperliquid.get_all_mids = mock_hyperliquid_api["get_all_mids"]
        ate.hyperliquid.close_position = mock_hyperliquid_api["close_position"]

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
    """SELL position: SL hit when current price >= stop_loss."""
    user_id = uuid.uuid4()
    strategy_id = uuid.uuid4()
    signal_id = uuid.uuid4()
    execution_id = uuid.uuid4()

    async with TestSessionFactory() as session:
        user = User(
            id=user_id,
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

    # Price goes to 3300 — above SL for SELL (SL hit when price >= sl_price)
    mock_hyperliquid_api["get_all_mids"].return_value = {"ETH": "3300.0"}

    ate = ATEService()
    ate.hyperliquid.get_all_mids = mock_hyperliquid_api["get_all_mids"]
    ate.hyperliquid.close_position = mock_hyperliquid_api["close_position"]

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
    """No TP/SL hit when price is between levels."""
    user_id = uuid.uuid4()
    strategy_id = uuid.uuid4()
    signal_id = uuid.uuid4()
    execution_id = uuid.uuid4()

    async with TestSessionFactory() as session:
        user = User(
            id=user_id,
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

    # Price at 160 — between SL(130) and TP(180) — no trigger
    mock_hyperliquid_api["get_all_mids"].return_value = {"SOL": "160.0"}

    ate = ATEService()
    ate.hyperliquid.get_all_mids = mock_hyperliquid_api["get_all_mids"]

    async with TestSessionFactory() as session:
        with patch("app.services.ate.NotificationService") as MockNotif:
            mock_service = MockNotif.return_value
            mock_service.send_take_profit_hit = AsyncMock()
            mock_service.send_stop_loss_hit = AsyncMock()

            results = await ate.monitor_positions(session)

    sol_results = [r for r in results if r["symbol"] == "SOL"]
    assert len(sol_results) == 0
