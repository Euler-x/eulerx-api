"""Schemas for notification preferences."""

from pydantic import BaseModel


class NotificationPreferencesRequest(BaseModel):
    """User-settable notification preferences.

    Keys follow the pattern ``{category}_{channel}`` where:
    - category: trades, signals, billing, support, referrals
    - channel: email, telegram

    ``None`` (or missing key) = enabled (backward-compatible default).
    """

    trades_email: bool = True
    trades_telegram: bool = True
    signals_email: bool = True
    signals_telegram: bool = True
    billing_email: bool = True
    billing_telegram: bool = True
    support_email: bool = True
    support_telegram: bool = True
    referrals_email: bool = True
    referrals_telegram: bool = True


class NotificationPreferencesResponse(BaseModel):
    trades_email: bool = True
    trades_telegram: bool = True
    signals_email: bool = True
    signals_telegram: bool = True
    billing_email: bool = True
    billing_telegram: bool = True
    support_email: bool = True
    support_telegram: bool = True
    referrals_email: bool = True
    referrals_telegram: bool = True
