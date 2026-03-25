"""Notification service for sending transactional notifications via email and Telegram.

Improvements included:
- #1: Telegram bot token validation (validate_telegram_bot)
- #2: Telegram token decryption helper (_get_telegram_token)
- #3: Notification preference checks (_is_enabled)
- #5: Notification rate limiting (_check_notification_rate)
"""

import logging
import secrets
import string
from datetime import timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.user import User
from app.services import email_templates, telegram_templates
from app.utils.helpers import utc_now
from app.utils.rate_limiter import InMemoryRateLimiter
from app.utils.security import decrypt_telegram_token

logger = logging.getLogger(__name__)
settings = get_settings()

TELEGRAM_API_BASE = "https://api.telegram.org"

# Module-level rate limiter for high-volume notification types
_notification_limiter = InMemoryRateLimiter(
    max_requests=settings.notification_rate_limit_per_user,
    window_seconds=settings.notification_rate_limit_window_seconds,
)


class NotificationService:
    """Sends transactional notifications via email (ZeptoMail) and Telegram."""

    def __init__(self):
        self.api_url = settings.zeptomail_api_url
        self.token = settings.zeptomail_token
        self.from_email = settings.zeptomail_from_email
        self.from_name = settings.zeptomail_from_name

    # ── Low-Level Senders ─────────────────────────────────────────

    async def send_email(
        self,
        to_email: str,
        to_name: str,
        subject: str,
        html_body: str,
    ) -> bool:
        """Send a single transactional email via ZeptoMail.

        Returns True on success, False on failure. Never raises.
        """
        if not self.token:
            logger.warning("ZeptoMail token not configured, skipping email")
            return False

        payload = {
            "from": {
                "address": self.from_email,
                "name": self.from_name,
            },
            "to": [
                {
                    "email_address": {
                        "address": to_email,
                        "name": to_name,
                    }
                }
            ],
            "subject": subject,
            "htmlbody": html_body,
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    self.api_url,
                    headers={
                        "Authorization": f"Zoho-enczapikey {self.token}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                if response.status_code in (200, 201):
                    logger.info("Email sent to %s: %s", to_email, subject)
                    return True

                logger.error(
                    "ZeptoMail API error %d: %s",
                    response.status_code,
                    response.text,
                )
                return False
        except Exception as e:
            logger.error("Failed to send email to %s: %s", to_email, e)
            return False

    @staticmethod
    async def send_telegram(bot_token: str, chat_id: str, text: str) -> bool:
        """Send a Telegram message via Bot API.

        Returns True on success, False on failure. Never raises.
        """
        url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(url, json=payload)
                if response.status_code == 200 and response.json().get("ok"):
                    logger.info("Telegram sent to chat_id=%s", chat_id)
                    return True
                logger.error(
                    "Telegram API error %d: %s",
                    response.status_code,
                    response.text,
                )
                return False
        except Exception as e:
            logger.error("Telegram send failed: %s", e)
            return False

    # ── Improvement #1: Bot Token Validation ──────────────────────

    @staticmethod
    async def validate_telegram_bot(bot_token: str) -> tuple[bool, str]:
        """Validate a Telegram bot token via getMe API.

        Returns (is_valid, bot_username_or_error).
        """
        url = f"{TELEGRAM_API_BASE}/bot{bot_token}/getMe"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url)
                data = response.json()
                if response.status_code == 200 and data.get("ok"):
                    username = data["result"].get("username", "unknown")
                    return True, username
                error_desc = data.get("description", "Unknown error")
                return False, error_desc
        except Exception as e:
            return False, str(e)

    # ── Helpers ────────────────────────────────────────────────────

    @staticmethod
    def _has_email(user: User) -> bool:
        return bool(user.email and user.email_verified)

    @staticmethod
    def _has_telegram(user: User) -> bool:
        return bool(user.telegram_bot_token and user.telegram_chat_id)

    @staticmethod
    def _generate_code() -> str:
        """Generate a cryptographically secure 6-digit numeric verification code."""
        return "".join(secrets.choice(string.digits) for _ in range(6))

    # ── Improvement #2: Decrypt Telegram Token ────────────────────

    @staticmethod
    def _get_telegram_token(user: User) -> str | None:
        """Decrypt the user's stored Telegram bot token.

        Returns the plain-text token, or None if decryption fails or
        the token is not configured.
        """
        if not user.telegram_bot_token:
            return None
        try:
            return decrypt_telegram_token(user.telegram_bot_token)
        except (ValueError, Exception) as e:
            logger.warning(
                "Failed to decrypt Telegram token for user %s: %s", user.id, e
            )
            return None

    # ── Improvement #3: Notification Preferences ──────────────────

    @staticmethod
    def _is_enabled(user: User, category: str, channel: str) -> bool:
        """Check if a notification category+channel is enabled for the user.

        If notification_preferences is None (no preferences saved), all
        notifications are enabled by default (backward-compatible).
        """
        prefs = user.notification_preferences
        if prefs is None:
            return True
        key = f"{category}_{channel}"
        return prefs.get(key, True)

    # ── Improvement #5: Notification Rate Limiting ────────────────

    @staticmethod
    def _check_notification_rate(user: User, notification_type: str) -> bool:
        """Check if this notification should be sent (rate limit not exceeded).

        Returns True if allowed, False if rate-limited.
        """
        key = f"notif:{user.id}:{notification_type}"
        if not _notification_limiter.check(key):
            logger.warning(
                "Notification rate limit hit for user %s type %s",
                user.id,
                notification_type,
            )
            return False
        _notification_limiter.record(key)
        return True

    # ── Internal Dispatch Helpers ─────────────────────────────────

    async def _dispatch_email(
        self, user: User, category: str, subject: str, html: str
    ) -> None:
        """Send email if user has email and preference is enabled."""
        if self._has_email(user) and self._is_enabled(user, category, "email"):
            try:
                await self.send_email(user.email, user.email, subject, html)
            except Exception as e:
                logger.error("Email dispatch failed (%s): %s", category, e)

    async def _dispatch_telegram(self, user: User, category: str, text: str) -> None:
        """Send Telegram if user has Telegram and preference is enabled."""
        if self._has_telegram(user) and self._is_enabled(user, category, "telegram"):
            token = self._get_telegram_token(user)
            if token:
                try:
                    await self.send_telegram(token, user.telegram_chat_id, text)
                except Exception as e:
                    logger.error("Telegram dispatch failed (%s): %s", category, e)

    # ── Convenience Methods (dual-channel) ────────────────────────

    async def send_verification_email(
        self,
        db: AsyncSession,
        user: User,
    ) -> str:
        """Generate verification code, save to user, and send email.

        Email-only — not sent via Telegram (code is for email verification).
        Always sent (no preference check, no rate limiting).
        Returns the generated code.
        """
        code = self._generate_code()
        user.email_verification_code = code
        user.email_verification_expires_at = utc_now() + timedelta(
            minutes=settings.email_verification_expiry_minutes
        )
        await db.flush()

        subject, html = email_templates.email_verification(code)
        await self.send_email(
            to_email=user.email,
            to_name=user.email or "User",
            subject=subject,
            html_body=html,
        )
        return code

    async def send_password_reset_email(self, db: AsyncSession, user: User) -> None:
        """Generate reset token, save to user, and send password reset email.

        Email-only — not sent via Telegram.
        Always sent (no preference check, no rate limiting).
        Token expires in 30 minutes.
        """
        token = secrets.token_urlsafe(32)
        user.password_reset_token = token
        user.password_reset_expires_at = utc_now() + timedelta(minutes=30)
        await db.flush()

        reset_link = f"{settings.frontend_url}/reset-password?token={token}"
        subject, html = email_templates.password_reset(reset_link)
        await self.send_email(
            to_email=user.email,
            to_name=user.email,
            subject=subject,
            html_body=html,
        )

    async def send_welcome_email(self, user: User) -> None:
        """Always sent (no preference check, no rate limiting)."""
        # Email
        if self._has_email(user):
            try:
                subject, html = email_templates.welcome()
                await self.send_email(user.email, user.email, subject, html)
            except Exception as e:
                logger.error("Email send_welcome failed: %s", e)

        # Telegram
        if self._has_telegram(user):
            token = self._get_telegram_token(user)
            if token:
                try:
                    text = telegram_templates.welcome()
                    await self.send_telegram(token, user.telegram_chat_id, text)
                except Exception as e:
                    logger.error("Telegram send_welcome failed: %s", e)

    async def send_subscription_activated(
        self,
        user: User,
        plan_name: str,
        billing_cycle: str,
        expires_at: str,
    ) -> None:
        """Category: billing. Not rate-limited."""
        subject, html = email_templates.subscription_activated(
            plan_name, billing_cycle, expires_at
        )
        await self._dispatch_email(user, "billing", subject, html)

        text = telegram_templates.subscription_activated(
            plan_name, billing_cycle, expires_at
        )
        await self._dispatch_telegram(user, "billing", text)

    async def send_subscription_expiring(
        self,
        user: User,
        plan_name: str,
        days_remaining: int,
        expires_at: str,
    ) -> None:
        """Category: billing. Not rate-limited."""
        subject, html = email_templates.subscription_expiring(
            plan_name, days_remaining, expires_at
        )
        await self._dispatch_email(user, "billing", subject, html)

        text = telegram_templates.subscription_expiring(
            plan_name, days_remaining, expires_at
        )
        await self._dispatch_telegram(user, "billing", text)

    async def send_trade_executed(
        self,
        user: User,
        symbol: str,
        direction: str,
        quantity: str,
        entry_price: str,
        strategy_name: str,
        exchange: str = "hyperliquid",
    ) -> None:
        """Category: trades. Rate-limited."""
        if not self._check_notification_rate(user, "trade_executed"):
            return

        subject, html = email_templates.trade_executed(
            symbol, direction, quantity, entry_price, strategy_name, exchange=exchange
        )
        await self._dispatch_email(user, "trades", subject, html)

        text = telegram_templates.trade_executed(
            symbol, direction, quantity, entry_price, strategy_name, exchange=exchange
        )
        await self._dispatch_telegram(user, "trades", text)

    async def send_take_profit_hit(
        self,
        user: User,
        symbol: str,
        direction: str,
        entry_price: str,
        exit_price: str,
        pnl: str,
        strategy_name: str,
        exchange: str = "hyperliquid",
    ) -> None:
        """Category: trades. Rate-limited."""
        if not self._check_notification_rate(user, "take_profit_hit"):
            return

        subject, html = email_templates.take_profit_hit(
            symbol,
            direction,
            entry_price,
            exit_price,
            pnl,
            strategy_name,
            exchange=exchange,
        )
        await self._dispatch_email(user, "trades", subject, html)

        text = telegram_templates.take_profit_hit(
            symbol,
            direction,
            entry_price,
            exit_price,
            pnl,
            strategy_name,
            exchange=exchange,
        )
        await self._dispatch_telegram(user, "trades", text)

    async def send_stop_loss_hit(
        self,
        user: User,
        symbol: str,
        direction: str,
        entry_price: str,
        exit_price: str,
        pnl: str,
        strategy_name: str,
        exchange: str = "hyperliquid",
    ) -> None:
        """Category: trades. Rate-limited."""
        if not self._check_notification_rate(user, "stop_loss_hit"):
            return

        subject, html = email_templates.stop_loss_hit(
            symbol,
            direction,
            entry_price,
            exit_price,
            pnl,
            strategy_name,
            exchange=exchange,
        )
        await self._dispatch_email(user, "trades", subject, html)

        text = telegram_templates.stop_loss_hit(
            symbol,
            direction,
            entry_price,
            exit_price,
            pnl,
            strategy_name,
            exchange=exchange,
        )
        await self._dispatch_telegram(user, "trades", text)

    async def send_strategy_paused(
        self,
        user: User,
        strategy_name: str,
        reason: str,
    ) -> None:
        """Category: trades. Not rate-limited (important alert)."""
        subject, html = email_templates.strategy_paused(strategy_name, reason)
        await self._dispatch_email(user, "trades", subject, html)

        text = telegram_templates.strategy_paused(strategy_name, reason)
        await self._dispatch_telegram(user, "trades", text)

    async def send_signal_generated(
        self,
        user: User,
        strategy_name: str,
        signal_count: int,
    ) -> None:
        """Category: signals. Rate-limited."""
        if not self._check_notification_rate(user, "signal_generated"):
            return

        subject, html = email_templates.signal_generated(strategy_name, signal_count)
        await self._dispatch_email(user, "signals", subject, html)

        text = telegram_templates.signal_generated(strategy_name, signal_count)
        await self._dispatch_telegram(user, "signals", text)

    async def send_support_ticket_update(
        self,
        user: User,
        ticket_subject: str,
        new_status: str,
        admin_reply: str | None = None,
    ) -> None:
        """Category: support. Not rate-limited."""
        subject, html = email_templates.support_ticket_update(
            ticket_subject, new_status, admin_reply
        )
        await self._dispatch_email(user, "support", subject, html)

        text = telegram_templates.support_ticket_update(
            ticket_subject, new_status, admin_reply
        )
        await self._dispatch_telegram(user, "support", text)

    async def send_referral_signup(
        self,
        ambassador_user: User,
        referred_wallet_hash: str,
    ) -> None:
        """Category: referrals. Not rate-limited."""
        subject, html = email_templates.referral_signup(referred_wallet_hash)
        await self._dispatch_email(ambassador_user, "referrals", subject, html)

        text = telegram_templates.referral_signup(referred_wallet_hash)
        await self._dispatch_telegram(ambassador_user, "referrals", text)
