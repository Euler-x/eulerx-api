"""Test Telegram configuration and notification preferences (Improvements #1, #2, #3)."""

import pytest
from unittest.mock import AsyncMock, patch

from app.utils.security import decrypt_telegram_token


# ── Telegram Config (Improvements #1 + #2) ────────────────────────


@pytest.mark.asyncio
async def test_save_telegram_config_valid(client, test_user):
    """PUT /telegram/config with valid token: validates, tests, encrypts, saves."""
    with patch(
        "app.routers.telegram.NotificationService.validate_telegram_bot",
        new_callable=AsyncMock,
        return_value=(True, "eulerx_bot"),
    ), patch(
        "app.routers.telegram.NotificationService.send_telegram",
        new_callable=AsyncMock,
        return_value=True,
    ):
        response = await client.put(
            "/api/v1/telegram/config",
            headers=test_user["headers"],
            json={"bot_token": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11", "chat_id": "987654321"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["telegram_configured"] is True
    assert "@eulerx_bot" in data["message"]


@pytest.mark.asyncio
async def test_save_telegram_config_invalid_token(client, test_user):
    """PUT /telegram/config with invalid bot token returns 400."""
    with patch(
        "app.routers.telegram.NotificationService.validate_telegram_bot",
        new_callable=AsyncMock,
        return_value=(False, "Unauthorized"),
    ):
        response = await client.put(
            "/api/v1/telegram/config",
            headers=test_user["headers"],
            json={"bot_token": "invalid-token-that-is-at-least-30-chars", "chat_id": "123"},
        )

    assert response.status_code == 400
    assert "Invalid Telegram bot token" in response.json()["detail"]


@pytest.mark.asyncio
async def test_save_telegram_config_bad_chat_id(client, test_user):
    """PUT /telegram/config: valid token but unreachable chat_id returns 400."""
    with patch(
        "app.routers.telegram.NotificationService.validate_telegram_bot",
        new_callable=AsyncMock,
        return_value=(True, "test_bot"),
    ), patch(
        "app.routers.telegram.NotificationService.send_telegram",
        new_callable=AsyncMock,
        return_value=False,
    ):
        response = await client.put(
            "/api/v1/telegram/config",
            headers=test_user["headers"],
            json={"bot_token": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11", "chat_id": "000"},
        )

    assert response.status_code == 400
    assert "failed to send a message" in response.json()["detail"]


@pytest.mark.asyncio
async def test_token_encrypted_at_rest(client, test_user):
    """After saving config, the stored token is Fernet-encrypted (not plaintext)."""
    plain_token = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"

    with patch(
        "app.routers.telegram.NotificationService.validate_telegram_bot",
        new_callable=AsyncMock,
        return_value=(True, "test_bot"),
    ), patch(
        "app.routers.telegram.NotificationService.send_telegram",
        new_callable=AsyncMock,
        return_value=True,
    ):
        response = await client.put(
            "/api/v1/telegram/config",
            headers=test_user["headers"],
            json={"bot_token": plain_token, "chat_id": "987654321"},
        )
    assert response.status_code == 200

    # Read the user from DB and check the stored token is encrypted
    from tests.conftest import TestSessionFactory
    from sqlalchemy import select
    from app.models.user import User

    async with TestSessionFactory() as session:
        result = await session.execute(
            select(User).where(User.id == test_user["id"])
        )
        user = result.scalar_one()
        stored_token = user.telegram_bot_token

    # Stored token must NOT be the plaintext
    assert stored_token != plain_token
    # But decrypting it should return the original
    assert decrypt_telegram_token(stored_token) == plain_token


@pytest.mark.asyncio
async def test_test_telegram(client, test_user):
    """POST /telegram/test sends a test message."""
    with patch(
        "app.routers.telegram.NotificationService.send_telegram",
        new_callable=AsyncMock,
        return_value=True,
    ):
        response = await client.post(
            "/api/v1/telegram/test",
            headers=test_user["headers"],
        )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True


@pytest.mark.asyncio
async def test_delete_telegram_config(client, test_user):
    """DELETE /telegram/config clears the config."""
    response = await client.delete(
        "/api/v1/telegram/config",
        headers=test_user["headers"],
    )
    assert response.status_code == 200
    assert response.json()["telegram_configured"] is False


# ── Notification Preferences (Improvement #3) ────────────────────


@pytest.mark.asyncio
async def test_get_preferences_default(client, test_user):
    """GET preferences returns all-true defaults when no prefs saved."""
    response = await client.get(
        "/api/v1/telegram/notifications/preferences",
        headers=test_user["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["trades_email"] is True
    assert data["trades_telegram"] is True
    assert data["signals_email"] is True
    assert data["billing_telegram"] is True


@pytest.mark.asyncio
async def test_update_preferences(client, test_user):
    """PUT preferences persists changes."""
    response = await client.put(
        "/api/v1/telegram/notifications/preferences",
        headers=test_user["headers"],
        json={
            "trades_email": True,
            "trades_telegram": False,
            "signals_email": False,
            "signals_telegram": True,
            "billing_email": True,
            "billing_telegram": True,
            "support_email": True,
            "support_telegram": True,
            "referrals_email": True,
            "referrals_telegram": True,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["trades_telegram"] is False
    assert data["signals_email"] is False

    # Verify persistence
    response2 = await client.get(
        "/api/v1/telegram/notifications/preferences",
        headers=test_user["headers"],
    )
    data2 = response2.json()
    assert data2["trades_telegram"] is False
    assert data2["signals_email"] is False
