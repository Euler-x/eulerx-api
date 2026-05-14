"""Test signal API endpoints."""

import uuid
from decimal import Decimal

import pytest

from app.models.billing import Plan, Subscription
from app.models.binance_signal import BinanceSignal
from app.models.enums import (
    BillingCycle,
    SignalDirection,
    SignalStatus,
    SubscriptionStatus,
)
from app.models.user import User
from app.utils.helpers import utc_now
from app.utils.security import create_access_token
from tests.conftest import TestSessionFactory


@pytest.mark.asyncio
async def test_binance_signal_endpoints_return_signals(client, setup_db):
    user_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    signal_id = uuid.uuid4()

    async with TestSessionFactory() as session:
        user = User(
            id=user_id,
            email=f"binance-signals-{uuid.uuid4().hex[:8]}@example.com",
            email_verified=True,
            is_subscribed=True,
        )
        plan = Plan(
            id=plan_id,
            name=f"Signals Test {uuid.uuid4().hex[:8]}",
            price_usd=Decimal("250.00"),
            billing_cycle=BillingCycle.MONTHLY,
            ate_access=True,
        )
        subscription = Subscription(
            user_id=user_id,
            plan_id=plan_id,
            status=SubscriptionStatus.ACTIVE,
            started_at=utc_now(),
            expires_at=None,
        )
        signal = BinanceSignal(
            id=signal_id,
            symbol="BTCUSDT",
            direction=SignalDirection.BUY,
            confidence=0.91,
            entry_price=Decimal("65000"),
            stop_loss=Decimal("63000"),
            take_profit=Decimal("69000"),
            risk_reward_ratio=2.0,
            indicators={"exchange": "binance"},
            status=SignalStatus.NEW,
        )
        session.add_all([user, plan, subscription, signal])
        await session.commit()

    headers = {"Authorization": f"Bearer {create_access_token(str(user_id))}"}

    live_response = await client.get("/api/v1/binance-signals/live", headers=headers)
    assert live_response.status_code == 200
    live_items = live_response.json()
    assert any(item["id"] == str(signal_id) for item in live_items)
    assert live_items[0]["exchange"] == "binance"

    list_response = await client.get("/api/v1/binance-signals", headers=headers)
    assert list_response.status_code == 200
    list_data = list_response.json()
    assert list_data["total"] >= 1
    assert any(item["id"] == str(signal_id) for item in list_data["items"])

    detail_response = await client.get(
        f"/api/v1/binance-signals/{signal_id}", headers=headers
    )
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["id"] == str(signal_id)
    assert detail["exchange"] == "binance"
