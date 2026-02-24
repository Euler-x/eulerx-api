import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.middleware.auth import require_verified_email
from app.models.schemas.analytics import (
    AnalyticsOverviewResponse,
    EquityCurvePoint,
    StrategyAnalyticsResponse,
)
from app.models.strategy import Strategy
from app.models.user import User
from app.services.analytics import AnalyticsService

from sqlalchemy import select

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/overview", response_model=AnalyticsOverviewResponse)
async def get_analytics_overview(
    days: int = Query(default=30, ge=1, le=365),
    current_user: User = Depends(require_verified_email),
    db: AsyncSession = Depends(get_db),
):
    data = await AnalyticsService.compute(db, current_user.id, days=days)
    return AnalyticsOverviewResponse(**data)


@router.get("/strategy/{strategy_id}", response_model=StrategyAnalyticsResponse)
async def get_strategy_analytics(
    strategy_id: uuid.UUID,
    days: int = Query(default=30, ge=1, le=365),
    current_user: User = Depends(require_verified_email),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Strategy).where(
            Strategy.id == strategy_id,
            Strategy.user_id == current_user.id,
        )
    )
    strategy = result.scalar_one_or_none()
    if strategy is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Strategy not found")

    data = await AnalyticsService.compute(
        db, current_user.id, strategy_id=strategy_id, days=days
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
    current_user: User = Depends(require_verified_email),
    db: AsyncSession = Depends(get_db),
):
    curve = await AnalyticsService.compute_equity_curve(
        db, current_user.id, strategy_id=strategy_id, days=days
    )
    return [EquityCurvePoint(**point) for point in curve]
