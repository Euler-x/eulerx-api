"""Admin strategies router — cross-user strategy management with full CRUD."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.middleware.audit import log_audit
from app.middleware.permissions import RequireAdmin, UserPermissions
from app.models.enums import RiskProfile, StrategyType
from app.models.schemas.admin import AdminStrategyUpdate
from app.models.schemas.common import MessageResponse, PaginatedResponse
from app.models.schemas.strategy import StrategyResponse
from app.models.strategy import Strategy

router = APIRouter()


@router.get("/strategies", response_model=PaginatedResponse)
async def admin_list_strategies(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_id: Optional[uuid.UUID] = None,
    strategy_type: Optional[StrategyType] = None,
    risk_profile: Optional[RiskProfile] = None,
    is_active: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(Strategy).order_by(Strategy.created_at.desc())
    count_query = select(func.count(Strategy.id))

    if user_id:
        query = query.where(Strategy.user_id == user_id)
        count_query = count_query.where(Strategy.user_id == user_id)
    if strategy_type:
        query = query.where(Strategy.strategy_type == strategy_type)
        count_query = count_query.where(Strategy.strategy_type == strategy_type)
    if risk_profile:
        query = query.where(Strategy.risk_profile == risk_profile)
        count_query = count_query.where(Strategy.risk_profile == risk_profile)
    if is_active is not None:
        query = query.where(Strategy.is_active == is_active)
        count_query = count_query.where(Strategy.is_active == is_active)

    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    strategies = result.scalars().all()

    return PaginatedResponse(
        items=[StrategyResponse.model_validate(s) for s in strategies],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


@router.get("/strategies/{strategy_id}", response_model=StrategyResponse)
async def admin_get_strategy(
    strategy_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Strategy).where(Strategy.id == strategy_id))
    strategy = result.scalar_one_or_none()
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return StrategyResponse.model_validate(strategy)


@router.put("/strategies/{strategy_id}", response_model=StrategyResponse)
async def admin_update_strategy(
    strategy_id: uuid.UUID,
    data: AdminStrategyUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    result = await db.execute(select(Strategy).where(Strategy.id == strategy_id))
    strategy = result.scalar_one_or_none()
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(strategy, key, value)

    await db.flush()
    await db.refresh(strategy)

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_update_strategy",
        resource_type="strategy",
        resource_id=str(strategy_id),
        details=update_data,
        ip_address=request.client.host if request.client else None,
    )

    return StrategyResponse.model_validate(strategy)


@router.post("/strategies/{strategy_id}/deactivate", response_model=StrategyResponse)
async def admin_deactivate_strategy(
    strategy_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    result = await db.execute(select(Strategy).where(Strategy.id == strategy_id))
    strategy = result.scalar_one_or_none()
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found")

    strategy.is_active = False
    await db.flush()

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_deactivate_strategy",
        resource_type="strategy",
        resource_id=str(strategy_id),
        ip_address=request.client.host if request.client else None,
    )

    return StrategyResponse.model_validate(strategy)


@router.delete("/strategies/{strategy_id}", response_model=MessageResponse)
async def admin_delete_strategy(
    strategy_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    result = await db.execute(select(Strategy).where(Strategy.id == strategy_id))
    strategy = result.scalar_one_or_none()
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found")

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_delete_strategy",
        resource_type="strategy",
        resource_id=str(strategy_id),
        details={"name": strategy.name, "user_id": str(strategy.user_id)},
        ip_address=request.client.host if request.client else None,
    )

    await db.delete(strategy)
    return MessageResponse(message="Strategy deleted successfully")
