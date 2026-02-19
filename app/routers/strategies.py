import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.middleware.auth import require_verified_email
from app.middleware.subscription import SubscriptionInfo, get_subscription_info, require_active_subscription
from app.models.schemas.common import MessageResponse
from app.models.schemas.strategy import StrategyCreate, StrategyResponse, StrategyUpdate
from app.models.strategy import Strategy
from app.models.user import User

router = APIRouter(prefix="/strategies", tags=["Strategies"])


@router.get("", response_model=list[StrategyResponse])
async def list_strategies(
    current_user: User = Depends(require_verified_email),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Strategy)
        .where(Strategy.user_id == current_user.id)
        .order_by(Strategy.created_at.desc())
    )
    strategies = result.scalars().all()
    return [StrategyResponse.model_validate(s) for s in strategies]


@router.post("", response_model=StrategyResponse, status_code=status.HTTP_201_CREATED)
async def create_strategy(
    data: StrategyCreate,
    current_user: User = Depends(require_verified_email),
    sub_info: SubscriptionInfo = Depends(get_subscription_info),
    db: AsyncSession = Depends(get_db),
):
    # Enforce plan limits if subscription exists
    if sub_info.is_active and sub_info.max_strategies > 0:
        count_result = await db.execute(
            select(func.count(Strategy.id)).where(
                Strategy.user_id == current_user.id
            )
        )
        current_count = count_result.scalar() or 0
        if current_count >= sub_info.max_strategies:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Maximum strategies ({sub_info.max_strategies}) reached for your plan.",
            )

    if sub_info.is_active and sub_info.max_allocation > 0:
        if data.capital_allocation > sub_info.max_allocation:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Capital allocation exceeds plan limit of {sub_info.max_allocation}.",
            )

    strategy = Strategy(
        user_id=current_user.id,
        name=data.name,
        strategy_type=data.strategy_type,
        risk_profile=data.risk_profile,
        leverage_limit=data.leverage_limit,
        max_positions=data.max_positions,
        capital_allocation=data.capital_allocation,
        max_drawdown_percent=data.max_drawdown_percent,
    )
    db.add(strategy)
    await db.flush()
    return StrategyResponse.model_validate(strategy)


@router.get("/{strategy_id}", response_model=StrategyResponse)
async def get_strategy(
    strategy_id: uuid.UUID,
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
        raise HTTPException(status_code=404, detail="Strategy not found")
    return StrategyResponse.model_validate(strategy)


@router.put("/{strategy_id}", response_model=StrategyResponse)
async def update_strategy(
    strategy_id: uuid.UUID,
    data: StrategyUpdate,
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
        raise HTTPException(status_code=404, detail="Strategy not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(strategy, key, value)

    await db.flush()
    await db.refresh(strategy)
    return StrategyResponse.model_validate(strategy)


@router.delete("/{strategy_id}", response_model=MessageResponse)
async def delete_strategy(
    strategy_id: uuid.UUID,
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
    current_user: User = Depends(require_verified_email),
    sub_info: SubscriptionInfo = Depends(require_active_subscription),
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
        raise HTTPException(status_code=404, detail="Strategy not found")

    strategy.is_active = True
    await db.flush()
    return StrategyResponse.model_validate(strategy)


@router.post("/{strategy_id}/pause", response_model=StrategyResponse)
async def pause_strategy(
    strategy_id: uuid.UUID,
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
        raise HTTPException(status_code=404, detail="Strategy not found")

    strategy.is_active = False
    await db.flush()
    return StrategyResponse.model_validate(strategy)
