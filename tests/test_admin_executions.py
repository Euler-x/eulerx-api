import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

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
from app.utils.security import encrypt_private_key
from tests.conftest import TestSessionFactory


@pytest.mark.asyncio
async def test_admin_executions_include_live_position_values(
    client, setup_db, admin_user
):
    user_id = uuid.uuid4()
    strategy_id = uuid.uuid4()
    signal_id = uuid.uuid4()
    execution_id = uuid.uuid4()

    async with TestSessionFactory() as session:
        user = User(
            id=user_id,
            wallet_address="0x" + "1" * 40,
            wallet_address_hash="live" * 16,
            wallet_type=WalletType.GENERATED,
            encrypted_private_key=encrypt_private_key("0x" + "a" * 64),
            email="live-exec@test.com",
            email_verified=True,
        )
        session.add(user)

        strategy = Strategy(
            id=strategy_id,
            user_id=user_id,
            name="Live Execution Strategy",
            strategy_type=StrategyType.MODERATE,
            risk_profile=RiskProfile.MEDIUM,
            allocation_pct=25.0,
            is_active=True,
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
            status=SignalStatus.FILLED,
        )
        session.add(signal)

        execution = Execution(
            id=execution_id,
            signal_id=signal_id,
            user_id=user_id,
            strategy_id=strategy_id,
            wallet_address_hash="live" * 16,
            order_type=OrderType.MARKET,
            direction=SignalDirection.BUY,
            entry_price=Decimal("50000"),
            quantity=Decimal("0.1"),
            leverage=1.0,
            status=ExecutionStatus.FILLED,
        )
        session.add(execution)
        await session.commit()

    with (
        patch(
            "app.routers.admin.executions.HyperliquidService.get_user_positions",
            new_callable=AsyncMock,
            return_value={
                "BTC": {
                    "size": 0.1,
                    "entry_px": 50123.45,
                    "unrealized_pnl": 12.34,
                }
            },
        ),
        patch(
            "app.routers.admin.executions.HyperliquidService.get_all_mids",
            new_callable=AsyncMock,
            return_value={"BTC": "50246.85"},
        ),
    ):
        response = await client.get(
            "/api/v1/admin/executions",
            params={"status": "filled", "page_size": 100},
            headers=admin_user["headers"],
        )

    assert response.status_code == 200
    item = next(i for i in response.json()["items"] if i["id"] == str(execution_id))
    assert item["symbol"] == "BTC"
    assert item["live_entry_price"] == pytest.approx(50123.45)
    assert item["mark_price"] == pytest.approx(50246.85)
    assert item["live_pnl"] == pytest.approx(12.34)
