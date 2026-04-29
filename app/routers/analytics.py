import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.middleware.permissions import RequireVerified, UserPermissions
from app.models.schemas.analytics import (
    AnalyticsOverviewResponse,
    EquityCurvePoint,
    PublicSystemPerformanceResponse,
    StrategyAnalyticsResponse,
)
from app.models.strategy import Strategy
from app.services.analytics import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/overview", response_model=AnalyticsOverviewResponse)
async def get_analytics_overview(
    days: int = Query(default=30, ge=1, le=365),
    exchange: Optional[str] = Query(default=None),
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    data = await AnalyticsService.compute(db, perms.id, days=days, exchange=exchange)
    return AnalyticsOverviewResponse(**data)


@router.get("/strategy/{strategy_id}", response_model=StrategyAnalyticsResponse)
async def get_strategy_analytics(
    strategy_id: uuid.UUID,
    days: int = Query(default=30, ge=1, le=365),
    exchange: Optional[str] = Query(default=None),
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Strategy).where(
            Strategy.id == strategy_id,
            Strategy.user_id == perms.id,
        )
    )
    strategy = result.scalar_one_or_none()
    if strategy is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Strategy not found")

    data = await AnalyticsService.compute(
        db, perms.id, strategy_id=strategy_id, days=days, exchange=exchange
    )
    return StrategyAnalyticsResponse(
        **data,
        strategy_id=strategy.id,
        strategy_name=strategy.name,
    )


@router.get("/equity-curve", response_model=list[EquityCurvePoint])
async def get_equity_curve(
    days: int = Query(default=30, ge=1, le=365),
    strategy_id: Optional[uuid.UUID] = Query(default=None),
    exchange: Optional[str] = Query(default=None),
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    curve = await AnalyticsService.compute_equity_curve(
        db, perms.id, strategy_id=strategy_id, days=days, exchange=exchange
    )
    return [EquityCurvePoint(**point) for point in curve]


# ── Public endpoint (no auth, rate-limited) ──────────────────────────

# Simple in-memory rate limiter for the public endpoint
_rate_limit_cache: dict[str, list[float]] = {}
_RATE_LIMIT_MAX = 10  # max requests per window
_RATE_LIMIT_WINDOW = 60  # seconds


def _check_rate_limit(client_ip: str) -> bool:
    """Return True if the request is within rate limits."""
    import time

    now = time.time()
    hits = _rate_limit_cache.get(client_ip, [])
    # Prune old entries
    hits = [t for t in hits if now - t < _RATE_LIMIT_WINDOW]
    if len(hits) >= _RATE_LIMIT_MAX:
        _rate_limit_cache[client_ip] = hits
        return False
    hits.append(now)
    _rate_limit_cache[client_ip] = hits
    return True


@router.get(
    "/system-performance",
    response_model=PublicSystemPerformanceResponse,
    summary="Public system performance (last 30 days)",
    description=(
        "Aggregated trading performance across all users. "
        "Public endpoint — no authentication required. "
        "Rate limited to 10 requests per minute per IP."
    ),
)
async def get_system_performance(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint for landing page performance statistics.

    Returns aggregate system-wide performance over the last 30 days
    including total trading volume, profit/loss %, and daily breakdown.
    No user-level data is exposed.
    """
    from fastapi import HTTPException

    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        raise HTTPException(
            status_code=429, detail="Rate limit exceeded. Please try again later."
        )

    data = await AnalyticsService.compute_system_performance(db, days=30)
    return PublicSystemPerformanceResponse(**data)
