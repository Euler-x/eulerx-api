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
