"""Test authentication endpoints."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.models.ambassador import Ambassador
from app.models.user import User
from tests.conftest import TestSessionFactory


@pytest.mark.asyncio
async def test_connect_wallet_invalid_agent_key(client):
    """POST /auth/connect with invalid agent key returns 400."""
    with patch(
        "app.routers.auth.WalletService.validate_private_key", return_value=None
    ):
        response = await client.post(
            "/api/v1/auth/connect",
            json={
                "wallet_address": "0x" + "a" * 40,
                "agent_private_key": "invalid_key",
            },
        )
        assert response.status_code == 400


@pytest.mark.asyncio
async def test_connect_wallet_valid_agent_key(client):
    """POST /auth/connect with valid agent key returns tokens."""
    agent_addr = "0x" + "b" * 40
    with patch(
        "app.routers.auth.WalletService.validate_private_key", return_value=agent_addr
    ):
        response = await client.post(
            "/api/v1/auth/connect",
            json={
                "wallet_address": "0x" + "c" * 40,
                "agent_private_key": "0x" + "a" * 64,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["user"]["wallet_type"] == "connected"


@pytest.mark.asyncio
async def test_get_me(client, test_user):
    """GET /auth/me returns current user."""
    response = await client.get("/api/v1/auth/me", headers=test_user["headers"])
    assert response.status_code == 200
    data = response.json()
    assert data["wallet_address_hash"] == test_user["wallet_hash"]
    assert data["email"] == "test@example.com"
    assert data["email_verified"] is True


@pytest.mark.asyncio
async def test_get_me_unauthorized(client):
    """GET /auth/me without token returns 401."""
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_refresh_token(client, test_user):
    """POST /auth/refresh returns new tokens."""
    from app.utils.security import create_refresh_token

    refresh = create_refresh_token(str(test_user["id"]))
    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data


@pytest.mark.asyncio
async def test_register_with_referral_code_creates_referred_ambassador(
    client, setup_db
):
    """POST /auth/register with a referral code attributes the new user."""
    referrer_user_id = uuid.uuid4()
    referrer_ambassador_id = uuid.uuid4()
    referral_code = "IPKW96GV"

    async with TestSessionFactory() as session:
        referrer_user = User(
            id=referrer_user_id,
            email=f"referrer-{uuid.uuid4().hex[:8]}@example.com",
            email_verified=True,
        )
        session.add(referrer_user)
        session.add(
            Ambassador(
                id=referrer_ambassador_id,
                user_id=referrer_user_id,
                referral_code=referral_code,
            )
        )
        await session.commit()

    with patch(
        "app.services.notifications.NotificationService.send_email",
        new_callable=AsyncMock,
    ):
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": f"referred-{uuid.uuid4().hex[:8]}@example.com",
                "password": "strong-password",
                "referral_code": referral_code.lower(),
                "cf_turnstile_token": "",
            },
        )

    assert response.status_code == 200

    async with TestSessionFactory() as session:
        referrer = await session.get(Ambassador, referrer_ambassador_id)
        assert referrer.total_referrals == 1
        assert referrer.team_size == 1
