import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.middleware.permissions import RequireVerified, UserPermissions
from app.models.schemas.analytics import (
    AnalyticsOverviewResponse,
    EquityCurvePoint,
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
