"""Admin users router — user management with search, detail, and field overrides."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.middleware.audit import log_audit
from app.middleware.auth import get_admin_user
from app.models.billing import Subscription
from app.models.execution import Execution
from app.models.schemas.admin import AdminUserDetailResponse, AdminUserUpdate
from app.models.schemas.auth import UserResponse
from app.models.schemas.common import PaginatedResponse
from app.models.strategy import Strategy
from app.models.transaction import Transaction
from app.models.user import User

router = APIRouter()


@router.get("/users", response_model=PaginatedResponse)
async def admin_list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    is_admin: Optional[bool] = None,
    is_active: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(User).order_by(User.created_at.desc())
    count_query = select(func.count(User.id))

    if search:
        like_term = f"%{search}%"
        search_filter = or_(
            User.wallet_address_hash.ilike(like_term),
            User.email.ilike(like_term),
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)
    if is_admin is not None:
        query = query.where(User.is_admin == is_admin)
        count_query = count_query.where(User.is_admin == is_admin)
    if is_active is not None:
        query = query.where(User.is_active == is_active)
        count_query = count_query.where(User.is_active == is_active)

    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    users = result.scalars().all()

    return PaginatedResponse(
        items=[UserResponse.model_validate(u) for u in users],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


@router.get("/users/{user_id}", response_model=AdminUserDetailResponse)
async def admin_get_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    strategy_count = (
        await db.execute(
            select(func.count(Strategy.id)).where(Strategy.user_id == user_id)
        )
    ).scalar() or 0
    execution_count = (
        await db.execute(
            select(func.count(Execution.id)).where(Execution.user_id == user_id)
        )
    ).scalar() or 0
    subscription_count = (
        await db.execute(
            select(func.count(Subscription.id)).where(Subscription.user_id == user_id)
        )
    ).scalar() or 0
    transaction_count = (
        await db.execute(
            select(func.count(Transaction.id)).where(Transaction.user_id == user_id)
        )
    ).scalar() or 0

    resp = AdminUserDetailResponse.model_validate(user)
    resp.strategy_count = strategy_count
    resp.execution_count = execution_count
    resp.subscription_count = subscription_count
    resp.transaction_count = transaction_count
    return resp


@router.put("/users/{user_id}", response_model=UserResponse)
async def admin_update_user(
    user_id: uuid.UUID,
    data: AdminUserUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(get_admin_user),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(user, key, value)

    await db.flush()

    await log_audit(
        db=db,
        user_id=admin_user.id,
        action="admin_update_user",
        resource_type="user",
        resource_id=str(user_id),
        details=update_data,
        ip_address=request.client.host if request.client else None,
    )

    return UserResponse.model_validate(user)


@router.post("/users/{user_id}/toggle-admin", response_model=UserResponse)
async def admin_toggle_admin(
    user_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(get_admin_user),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_admin = not user.is_admin
    await db.flush()

    await log_audit(
        db,
        user_id=admin_user.id,
        action="toggle_admin",
        resource_type="user",
        resource_id=str(user_id),
        details={"is_admin": user.is_admin},
        ip_address=request.client.host if request.client else None,
    )

    return UserResponse.model_validate(user)


@router.post("/users/{user_id}/toggle-active", response_model=UserResponse)
async def admin_toggle_active(
    user_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(get_admin_user),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = not user.is_active
    await db.flush()

    action = "unban_user" if user.is_active else "ban_user"
    await log_audit(
        db,
        user_id=admin_user.id,
        action=action,
        resource_type="user",
        resource_id=str(user_id),
        details={"is_active": user.is_active},
        ip_address=request.client.host if request.client else None,
    )

    return UserResponse.model_validate(user)
