"""Test NotificationService improvements (#3 preferences, #5 rate limiting)."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.enums import WalletType
from app.models.user import User
from app.services.notifications import NotificationService, _notification_limiter
from app.utils.security import encrypt_telegram_token


def _make_user(**kwargs) -> User:
    """Create a User object (not DB-persisted) for unit testing."""
    defaults = {
        "id": uuid.uuid4(),
        "wallet_address_hash": "x" * 64,
        "wallet_type": WalletType.CONNECTED,
    }
    defaults.update(kwargs)
    # Use normal constructor — SQLAlchemy needs proper initialization
    user = User(**defaults)
    return user


class TestPreferences:
    """Test _is_enabled with various preference combinations."""

    def test_none_prefs_all_enabled(self):
        user = _make_user(notification_preferences=None)
        assert NotificationService._is_enabled(user, "trades", "email") is True
        assert NotificationService._is_enabled(user, "signals", "telegram") is True

    def test_explicit_false_disabled(self):
        user = _make_user(notification_preferences={"trades_email": False})
        assert NotificationService._is_enabled(user, "trades", "email") is False

    def test_explicit_true_enabled(self):
        user = _make_user(notification_preferences={"trades_email": True})
        assert NotificationService._is_enabled(user, "trades", "email") is True

    def test_missing_key_defaults_true(self):
        user = _make_user(notification_preferences={"trades_email": False})
        # signals_telegram is not in prefs — should default to True
        assert NotificationService._is_enabled(user, "signals", "telegram") is True


class TestRateLimiting:
    """Test _check_notification_rate."""

    def setup_method(self):
        # Reset the limiter state between tests
        _notification_limiter._requests.clear()

    def test_allowed_within_limit(self):
        user = _make_user()
        for _ in range(10):
            assert (
                NotificationService._check_notification_rate(user, "trade_executed")
                is True
            )

    def test_blocked_over_limit(self):
        user = _make_user()
        for _ in range(10):
            NotificationService._check_notification_rate(user, "signal_generated")
        # 11th should be blocked
        assert (
            NotificationService._check_notification_rate(user, "signal_generated")
            is False
        )

    def test_different_types_independent(self):
        user = _make_user()
        for _ in range(10):
            NotificationService._check_notification_rate(user, "trade_executed")
        # Different type should still be allowed
        assert (
            NotificationService._check_notification_rate(user, "signal_generated")
            is True
        )

    def test_different_users_independent(self):
        user1 = _make_user()
        user2 = _make_user()
        for _ in range(10):
            NotificationService._check_notification_rate(user1, "trade_executed")
        # Different user should still be allowed
        assert (
            NotificationService._check_notification_rate(user2, "trade_executed")
            is True
        )


class TestTelegramTokenDecryption:
    """Test _get_telegram_token decryption."""

    def test_decrypt_valid_token(self):
        plain = "123456:ABC-DEF1234"
        encrypted = encrypt_telegram_token(plain)
        user = _make_user(telegram_bot_token=encrypted, telegram_chat_id="123")
        result = NotificationService._get_telegram_token(user)
        assert result == plain

    def test_decrypt_none_token(self):
        user = _make_user(telegram_bot_token=None, telegram_chat_id="123")
        assert NotificationService._get_telegram_token(user) is None

    def test_decrypt_invalid_token_returns_none(self):
        user = _make_user(telegram_bot_token="not-encrypted", telegram_chat_id="123")
        assert NotificationService._get_telegram_token(user) is None


class TestValidateTelegramBot:
    """Test validate_telegram_bot with mocked Telegram API."""

    @pytest.mark.asyncio
    async def test_valid_bot(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True, "result": {"username": "my_bot"}}

        with patch(
            "httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp
        ):
            is_valid, info = await NotificationService.validate_telegram_bot(
                "123:token"
            )

        assert is_valid is True
        assert info == "my_bot"

    @pytest.mark.asyncio
    async def test_invalid_bot(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {"ok": False, "description": "Unauthorized"}

        with patch(
            "httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp
        ):
            is_valid, info = await NotificationService.validate_telegram_bot(
                "bad:token"
            )

        assert is_valid is False
        assert "Unauthorized" in info


class TestDispatchWithPreferences:
    """Test that send methods respect preferences."""

    @pytest.mark.asyncio
    async def test_trade_executed_skips_disabled_channel(self):
        """If trades_telegram=False, Telegram dispatch is skipped."""
        service = NotificationService()
        _notification_limiter._requests.clear()

        user = _make_user(
            email="user@test.com",
            email_verified=True,
            telegram_bot_token=encrypt_telegram_token("123:token"),
            telegram_chat_id="456",
            notification_preferences={"trades_telegram": False},
        )

        with (
            patch.object(service, "send_email", new_callable=AsyncMock) as mock_email,
            patch.object(service, "send_telegram", new_callable=AsyncMock) as mock_tg,
        ):
            mock_email.return_value = True
            mock_tg.return_value = True

            await service.send_trade_executed(
                user=user,
                symbol="BTC",
                direction="buy",
                quantity="0.5",
                entry_price="50000",
                strategy_name="Test Strategy",
            )

            # Email should be called (trades_email defaults to True)
            mock_email.assert_called_once()
            # Telegram should NOT be called
            mock_tg.assert_not_called()

    @pytest.mark.asyncio
    async def test_welcome_always_sent(self):
        """Welcome notifications are always sent regardless of preferences."""
        service = NotificationService()

        user = _make_user(
            email="user@test.com",
            email_verified=True,
            telegram_bot_token=encrypt_telegram_token("123:token"),
            telegram_chat_id="456",
            notification_preferences={
                "trades_email": False,
                "trades_telegram": False,
            },
        )

        with (
            patch.object(service, "send_email", new_callable=AsyncMock) as mock_email,
            patch.object(service, "send_telegram", new_callable=AsyncMock) as mock_tg,
        ):
            mock_email.return_value = True
            mock_tg.return_value = True

            await service.send_welcome_email(user)

            # Both channels should be called (welcome bypasses prefs)
            mock_email.assert_called_once()
            mock_tg.assert_called_once()
