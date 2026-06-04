"""Dynamic configuration service — reads from admin_config DB with env fallbacks.

Usage in any async context:
    from app.services.dynamic_config import get_config
    value = await get_config("ate_confidence_threshold", 0.70)

All pipeline/ATE settings should use this instead of hardcoded settings.X
so admins can tune them at runtime without redeployment.
"""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.admin_config import AdminConfig

logger = logging.getLogger(__name__)
settings = get_settings()

# Default values matching config.py — used when no DB override exists.
# Grouped by category for the admin UI.
CONFIG_DEFAULTS: dict[str, dict[str, Any]] = {
    # ── Trading Engine ─────────────────────────────────────────
    "ate_confidence_threshold": {
        "value": settings.ate_confidence_threshold,
        "type": "float",
        "category": "Trading",
        "label": "Confidence Threshold",
        "description": "Minimum AI confidence (0-1) to execute a signal. Signals below this are skipped.",
    },
    "ate_max_executions_per_hour": {
        "value": settings.ate_max_executions_per_hour,
        "type": "int",
        "category": "Trading",
        "label": "Max Executions / Hour",
        "description": "Rate limit: max executions per user per hour.",
    },
    "ate_default_leverage": {
        "value": settings.ate_default_leverage,
        "type": "float",
        "category": "Trading",
        "label": "Default Leverage",
        "description": "Leverage used when a strategy doesn't specify one.",
    },
    "ate_max_drawdown_percent": {
        "value": settings.ate_max_drawdown_percent,
        "type": "float",
        "category": "Trading",
        "label": "Max Drawdown %",
        "description": "Default max daily drawdown % before strategy auto-pause.",
    },
    "buy_signals_enabled": {
        "value": 0,
        "type": "int",
        "category": "Trading",
        "label": "BUY Signals Enabled",
        "description": "Set to 0 to disable all BUY execution (SELL-only mode). Set to 1 to re-enable BUY signals.",
    },
    "buy_confidence_threshold": {
        "value": 0.79,
        "type": "float",
        "category": "Trading",
        "label": "BUY Confidence Threshold",
        "description": "Minimum AI confidence required to execute a BUY signal. Overrides the general threshold for BUY direction only.",
    },
    "buy_symbol_blocklist": {
        "value": "RESOLV,CHILLGUY,SUPER,CFX,IP,BABY,GRIFFAIN,BIO",
        "type": "str",
        "category": "Trading",
        "label": "BUY Symbol Blocklist",
        "description": "Comma-separated symbols never allowed to execute as BUY. Pre-loaded with historically high SL-rate assets.",
    },
    # ── Analysis Pipeline ──────────────────────────────────────
    "analysis_schedule_hours": {
        "value": settings.analysis_schedule_hours,
        "type": "int",
        "category": "Pipeline",
        "label": "Pipeline Frequency (hours)",
        "description": "How often the signal generation pipeline runs.",
    },
    "hl_symbols_limit": {
        "value": 4,
        "type": "int",
        "category": "Pipeline",
        "label": "HyperLiquid Symbols",
        "description": "Number of top movers to analyze from HyperLiquid per run.",
    },
    "bybit_symbols_limit": {
        "value": 6,
        "type": "int",
        "category": "Pipeline",
        "label": "Bybit Symbols",
        "description": "Number of top movers to analyze from Bybit per run.",
    },
    "binance_symbols_limit": {
        "value": 6,
        "type": "int",
        "category": "Pipeline",
        "label": "Binance Symbols",
        "description": "Number of top movers to analyze from Binance per run.",
    },
    "binance_performance_guard_enabled": {
        "value": 1,
        "type": "int",
        "category": "Trading",
        "label": "Binance Performance Guard",
        "description": "Reject Binance setups when recent closed-trade history shows more SL than TP and negative PnL.",
    },
    "binance_performance_guard_min_trades": {
        "value": 6,
        "type": "int",
        "category": "Trading",
        "label": "Binance Guard Min Trades",
        "description": "Minimum matching Binance closed trades before the performance guard can block a setup.",
    },
    "binance_performance_guard_lookback": {
        "value": 30,
        "type": "int",
        "category": "Trading",
        "label": "Binance Guard Lookback",
        "description": "Maximum number of recent matching Binance closed trades used by the performance guard.",
    },
    "binance_performance_guard_min_win_rate": {
        "value": 0.45,
        "type": "float",
        "category": "Trading",
        "label": "Binance Guard Min Win Rate",
        "description": "Minimum acceptable win rate for recent Binance history when net PnL is negative.",
    },
    "binance_performance_guard_min_profit_factor": {
        "value": 1.10,
        "type": "float",
        "category": "Trading",
        "label": "Binance Guard Min Profit Factor",
        "description": "Minimum acceptable profit factor for recent Binance history when net PnL is negative.",
    },
    # ── Subscription ───────────────────────────────────────────
    "subscription_grace_period_days": {
        "value": settings.subscription_grace_period_days,
        "type": "int",
        "category": "Billing",
        "label": "Grace Period (days)",
        "description": "Days after subscription expires before access is revoked.",
    },
    # ── Email ──────────────────────────────────────────────────
    "email_verification_expiry_minutes": {
        "value": settings.email_verification_expiry_minutes,
        "type": "int",
        "category": "Email",
        "label": "Verification Code Expiry (min)",
        "description": "How long email verification codes remain valid.",
    },
    # ── AI Models ──────────────────────────────────────────────
    "openrouter_models": {
        "value": settings.openrouter_models,
        "type": "str",
        "category": "AI",
        "label": "AI Models (comma-separated)",
        "description": "OpenRouter model IDs used for signal analysis consensus.",
    },
    # ── Trend Filter ──────────────────────────────────────────
    "market_trend": {
        "value": "neutral",
        "type": "str",
        "category": "Trend Filter",
        "label": "Market Trend",
        "description": (
            "Active market bias: 'bullish' (only BUY allowed), "
            "'bearish' (only SELL allowed), or 'neutral' (no filter)."
        ),
    },
    "trend_filter_enabled": {
        "value": 1,
        "type": "int",
        "category": "Trend Filter",
        "label": "Trend Filter Enabled",
        "description": (
            "Master switch for the trend filter. "
            "Set to 0 to disable trend-based signal filtering entirely."
        ),
    },
    "trend_with_confidence_boost": {
        "value": 0.0,
        "type": "float",
        "category": "Trend Filter",
        "label": "With-Trend Confidence Boost",
        "description": (
            "Confidence boost added to signals that align with the active trend "
            "(e.g. SELL signals in a bearish market get +X confidence)."
        ),
    },
    "trend_counter_confidence_penalty": {
        "value": 0.10,
        "type": "float",
        "category": "Trend Filter",
        "label": "Counter-Trend Confidence Penalty",
        "description": (
            "Confidence penalty applied to signals that go against the active trend "
            "before the rejection check."
        ),
    },
    "trend_counter_allow_high_confidence": {
        "value": 0,
        "type": "int",
        "category": "Trend Filter",
        "label": "Allow High-Confidence Counter-Trend",
        "description": (
            "If 1, allows counter-trend signals that exceed the counter-trend "
            "minimum confidence threshold instead of blocking them outright."
        ),
    },
    "trend_counter_min_confidence": {
        "value": 0.95,
        "type": "float",
        "category": "Trend Filter",
        "label": "Counter-Trend Min Confidence",
        "description": (
            "Minimum confidence required for a counter-trend trade to be allowed "
            "(only effective when high-confidence override is enabled)."
        ),
    },
}


async def get_config(
    key: str, default: Any = None, db: AsyncSession | None = None
) -> Any:
    """Read a config value from DB, falling back to CONFIG_DEFAULTS then default param."""
    try:
        if db is not None:
            return await _read_from_session(key, default, db)

        # Create a fresh session if none provided
        from app.db.base import async_session_factory

        async with async_session_factory() as session:
            return await _read_from_session(key, default, session)
    except Exception as e:
        logger.warning("Failed to read config '%s' from DB: %s", key, e)

    # Final fallback
    if key in CONFIG_DEFAULTS:
        return CONFIG_DEFAULTS[key]["value"]
    return default


async def _read_from_session(key: str, default: Any, db: AsyncSession) -> Any:
    """Read a single config key from the DB session."""
    result = await db.execute(select(AdminConfig).where(AdminConfig.key == key))
    config = result.scalar_one_or_none()
    if config is not None:
        # admin_config stores JSON — extract the "value" key
        raw = config.value
        if isinstance(raw, dict) and "value" in raw:
            return raw["value"]
        return raw

    # Fallback to defaults
    if key in CONFIG_DEFAULTS:
        return CONFIG_DEFAULTS[key]["value"]
    return default


async def get_all_config(db: AsyncSession) -> dict[str, Any]:
    """Return all config values merged: DB overrides + defaults."""
    # Start with defaults
    merged: dict[str, Any] = {}
    for key, meta in CONFIG_DEFAULTS.items():
        merged[key] = {
            "value": meta["value"],
            "type": meta["type"],
            "category": meta["category"],
            "label": meta["label"],
            "description": meta["description"],
            "source": "default",
        }

    # Override with DB values
    result = await db.execute(select(AdminConfig))
    db_configs = result.scalars().all()
    for config in db_configs:
        if config.key in merged:
            raw = config.value
            val = raw.get("value", raw) if isinstance(raw, dict) else raw
            merged[config.key]["value"] = val
            merged[config.key]["source"] = "database"

    return merged
