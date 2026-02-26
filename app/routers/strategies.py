import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.middleware.permissions import (
    RequireSubscribed,
    RequireVerified,
    UserPermissions,
)
from app.models.schemas.common import MessageResponse
from app.models.schemas.strategy import StrategyCreate, StrategyResponse, StrategyUpdate
from app.models.strategy import Strategy
from app.services.plan_enforcement import PlanEnforcer

router = APIRouter(prefix="/strategies", tags=["Strategies"])


@router.get("", response_model=list[StrategyResponse])
async def list_strategies(
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Strategy)
        .where(Strategy.user_id == perms.id)
        .order_by(Strategy.created_at.desc())
    )
    strategies = result.scalars().all()
    return [StrategyResponse.model_validate(s) for s in strategies]


@router.post("", response_model=StrategyResponse, status_code=status.HTTP_201_CREATED)
async def create_strategy(
    data: StrategyCreate,
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    await PlanEnforcer.strategy_count(db, perms)
    await PlanEnforcer.allocation(db, perms, data.capital_allocation)

    strategy = Strategy(
        user_id=perms.id,
        name=data.name,
        strategy_type=data.strategy_type,
        risk_profile=data.risk_profile,
        leverage_limit=data.leverage_limit,
        max_positions=data.max_positions,
        capital_allocation=data.capital_allocation,
        max_drawdown_percent=data.max_drawdown_percent,
        daily_loss_cap_percent=data.daily_loss_cap_percent,
        target_volatility=data.target_volatility,
        expected_volatility=data.expected_volatility,
        timeframe=data.timeframe,
        target_return_min=data.target_return_min,
        target_return_max=data.target_return_max,
    )
    db.add(strategy)
    await db.flush()
    await db.refresh(strategy)
    return StrategyResponse.model_validate(strategy)


@router.get("/{strategy_id}", response_model=StrategyResponse)
async def get_strategy(
    strategy_id: uuid.UUID,
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
        raise HTTPException(status_code=404, detail="Strategy not found")
    return StrategyResponse.model_validate(strategy)


@router.put("/{strategy_id}", response_model=StrategyResponse)
async def update_strategy(
    strategy_id: uuid.UUID,
    data: StrategyUpdate,
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
        raise HTTPException(status_code=404, detail="Strategy not found")

    # If capital_allocation is being changed, re-validate against plan limits,
    # excluding this strategy's current value from the cumulative sum.
    if data.capital_allocation is not None:
        await PlanEnforcer.allocation(
            db,
            perms,
            data.capital_allocation,
            exclude_strategy_id=strategy_id,
        )

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(strategy, key, value)

    await db.flush()
    await db.refresh(strategy)
    return StrategyResponse.model_validate(strategy)


@router.delete("/{strategy_id}", response_model=MessageResponse)
async def delete_strategy(
    strategy_id: uuid.UUID,
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
        raise HTTPException(status_code=404, detail="Strategy not found")

    if strategy.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete an active strategy. Pause it first.",
        )

    await db.delete(strategy)
    return MessageResponse(message="Strategy deleted successfully")


@router.post("/{strategy_id}/activate", response_model=StrategyResponse)
async def activate_strategy(
    strategy_id: uuid.UUID,
    perms: UserPermissions = RequireSubscribed,
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
        raise HTTPException(status_code=404, detail="Strategy not found")

    strategy.is_active = True
    await db.flush()
    await db.refresh(strategy)
    return StrategyResponse.model_validate(strategy)


@router.post("/{strategy_id}/pause", response_model=StrategyResponse)
async def pause_strategy(
    strategy_id: uuid.UUID,
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
        raise HTTPException(status_code=404, detail="Strategy not found")

    strategy.is_active = False
    await db.flush()
    await db.refresh(strategy)
    return StrategyResponse.model_validate(strategy)
