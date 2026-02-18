"""Test authentication endpoints."""

import pytest
from unittest.mock import patch


@pytest.mark.asyncio
async def test_get_sign_message(client):
    """GET /auth/sign-message returns a message for the wallet."""
    wallet = "0x" + "a" * 40
    response = await client.get(f"/api/v1/auth/sign-message?wallet_address={wallet}")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert wallet in data["message"]


@pytest.mark.asyncio
async def test_sign_message_invalid_address(client):
    """Short wallet address rejected."""
    response = await client.get("/api/v1/auth/sign-message?wallet_address=0x123")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_connect_wallet_invalid_signature(client):
    """POST /auth/connect with bad signature returns 401."""
    with patch("app.routers.auth.WalletService.verify_signature", return_value=False):
        response = await client.post(
            "/api/v1/auth/connect",
            json={
                "wallet_address": "0x" + "a" * 40,
                "message": "Sign this message...",
                "signature": "0x" + "f" * 130,
            },
        )
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_connect_wallet_valid_signature(client):
    """POST /auth/connect with valid signature returns tokens."""
    with patch("app.routers.auth.WalletService.verify_signature", return_value=True), \
         patch("app.routers.auth.WalletService.hash_address", return_value="c" * 64), \
         patch("app.services.notifications.NotificationService.send_welcome_email"):
        response = await client.post(
            "/api/v1/auth/connect",
            json={
                "wallet_address": "0x" + "c" * 40,
                "message": "Sign this message...",
                "signature": "0x" + "d" * 130,
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
