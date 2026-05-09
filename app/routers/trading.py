"""Trading endpoints — user-facing trading configuration status and controls."""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.models.admin_config import AdminConfig
from app.services.dynamic_config import CONFIG_DEFAULTS, get_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trading", tags=["Trading"])


class SetTrendRequest(BaseModel):
    market_trend: str  # "bullish" | "bearish" | "neutral"


class TrendConfigRequest(BaseModel):
    trend_filter_enabled: bool | None = None
    trend_counter_allow_high_confidence: bool | None = None
    trend_counter_confidence_penalty: float | None = None
    trend_counter_min_confidence: float | None = None
    trend_with_confidence_boost: float | None = None


async def _upsert_config(db: AsyncSession, key: str, value: object) -> None:
    result = await db.execute(select(AdminConfig).where(AdminConfig.key == key))
    row = result.scalar_one_or_none()
    if row is None:
        desc = CONFIG_DEFAULTS.get(key, {}).get("description", "")
        row = AdminConfig(key=key, value={"value": value}, description=desc)
        db.add(row)
    else:
        row.value = {"value": value}


@router.get("/trend-status")
async def get_trend_status(db: AsyncSession = Depends(get_db)):
    """Return the current market trend filter status.

    This is a read-only endpoint available to all authenticated users
    so they can see the active trend bias before placing trades.
    """
    trend_filter_enabled = bool(int(await get_config("trend_filter_enabled", 1, db)))
    market_trend = str(await get_config("market_trend", "neutral", db)).lower().strip()

    allowed_direction_map = {
        "bullish": "BUY only",
        "bearish": "SELL only",
        "neutral": "BUY and SELL",
    }

    # Read advanced config for status display
    counter_allow = bool(
        int(await get_config("trend_counter_allow_high_confidence", 0, db))
    )
    counter_min_conf = float(await get_config("trend_counter_min_confidence", 0.95, db))
    counter_penalty = float(
        await get_config("trend_counter_confidence_penalty", 0.10, db)
    )
    with_boost = float(await get_config("trend_with_confidence_boost", 0.0, db))

    return {
        "trend_filter_enabled": trend_filter_enabled,
        "market_trend": market_trend,
        "allowed_direction": allowed_direction_map.get(market_trend, "BUY and SELL"),
        "counter_trend_override_enabled": counter_allow,
        "counter_trend_min_confidence": counter_min_conf,
        "counter_trend_penalty": counter_penalty,
        "with_trend_boost": with_boost,
    }


@router.put("/set-trend")
async def set_market_trend(
    body: SetTrendRequest,
    db: AsyncSession = Depends(get_db),
):
    """Set the active market trend bias (bullish / bearish / neutral)."""
    trend = body.market_trend.lower().strip()
    if trend not in ("bullish", "bearish", "neutral"):
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400,
            detail="market_trend must be 'bullish', 'bearish', or 'neutral'",
        )
    await _upsert_config(db, "market_trend", trend)
    await db.commit()
    logger.info("Market trend set to: %s", trend)
    return {"market_trend": trend}


@router.put("/trend-config")
async def update_trend_config(
    body: TrendConfigRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update advanced trend filter configuration."""
    if body.trend_filter_enabled is not None:
        await _upsert_config(db, "trend_filter_enabled", int(body.trend_filter_enabled))
    if body.trend_counter_allow_high_confidence is not None:
        await _upsert_config(
            db,
            "trend_counter_allow_high_confidence",
            int(body.trend_counter_allow_high_confidence),
        )
    if body.trend_counter_confidence_penalty is not None:
        await _upsert_config(
            db,
            "trend_counter_confidence_penalty",
            body.trend_counter_confidence_penalty,
        )
    if body.trend_counter_min_confidence is not None:
        await _upsert_config(
            db,
            "trend_counter_min_confidence",
            body.trend_counter_min_confidence,
        )
    if body.trend_with_confidence_boost is not None:
        await _upsert_config(
            db,
            "trend_with_confidence_boost",
            body.trend_with_confidence_boost,
        )
    await db.commit()
    return {"status": "ok"}
