"""Admin subscriptions router — list, create, override, and cancel subscriptions."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.middleware.audit import log_audit
from app.middleware.permissions import RequireAdmin, UserPermissions
from app.models.billing import Plan, Subscription
from app.models.enums import SubscriptionStatus
from app.models.schemas.admin import AdminSubscriptionCreate
from app.models.schemas.billing import SubscriptionOverride, SubscriptionResponse
from app.models.schemas.common import MessageResponse, PaginatedResponse
from app.models.user import User

router = APIRouter()


@router.get("/subscriptions", response_model=PaginatedResponse)
async def admin_list_subscriptions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[SubscriptionStatus] = None,
    user_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(Subscription).order_by(Subscription.created_at.desc())
    count_query = select(func.count(Subscription.id))

    if status_filter:
        query = query.where(Subscription.status == status_filter)
        count_query = count_query.where(Subscription.status == status_filter)
    if user_id:
        query = query.where(Subscription.user_id == user_id)
        count_query = count_query.where(Subscription.user_id == user_id)

    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    subscriptions = result.scalars().all()

    return PaginatedResponse(
        items=[SubscriptionResponse.model_validate(s) for s in subscriptions],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


@router.post("/subscriptions", response_model=SubscriptionResponse, status_code=201)
async def admin_create_subscription(
    data: AdminSubscriptionCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    """Manually grant a subscription to a user."""
    # Validate user exists
    user_result = await db.execute(select(User).where(User.id == data.user_id))
    target_user = user_result.scalar_one_or_none()
    if target_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Validate plan exists
    plan_result = await db.execute(select(Plan).where(Plan.id == data.plan_id))
    if plan_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Plan not found")

    subscription = Subscription(
        user_id=data.user_id,
        plan_id=data.plan_id,
        status=data.status,
        expires_at=data.expires_at,
    )
    db.add(subscription)
    await db.flush()

    # Sync denormalized flag on the target user
    _active_statuses = {SubscriptionStatus.ACTIVE, SubscriptionStatus.EXPIRING_SOON}
    if target_user is not None:
        target_user.is_subscribed = data.status in _active_statuses

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_create_subscription",
        resource_type="subscription",
        resource_id=str(subscription.id),
        details={
            "target_user_id": str(data.user_id),
            "plan_id": str(data.plan_id),
            "status": data.status.value,
        },
        ip_address=request.client.host if request.client else None,
    )

    await db.refresh(subscription, ["plan"])
    return SubscriptionResponse.model_validate(subscription)


@router.put("/subscriptions/{subscription_id}", response_model=SubscriptionResponse)
async def admin_override_subscription(
    subscription_id: uuid.UUID,
    data: SubscriptionOverride,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    result = await db.execute(
        select(Subscription).where(Subscription.id == subscription_id)
    )
    subscription = result.scalar_one_or_none()
    if subscription is None:
        raise HTTPException(status_code=404, detail="Subscription not found")

    old_status = subscription.status.value
    subscription.status = data.status
    if data.expires_at is not None:
        subscription.expires_at = data.expires_at
    if data.grace_until is not None:
        subscription.grace_until = data.grace_until

    # Sync denormalized is_subscribed flag on the user
    _active_statuses = {SubscriptionStatus.ACTIVE, SubscriptionStatus.EXPIRING_SOON}
    user_r = await db.execute(select(User).where(User.id == subscription.user_id))
    sub_user = user_r.scalar_one_or_none()
    if sub_user is not None:
        sub_user.is_subscribed = data.status in _active_statuses

    await db.flush()

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_override_subscription",
        resource_type="subscription",
        resource_id=str(subscription_id),
        details={"old_status": old_status, "new_status": data.status.value},
        ip_address=request.client.host if request.client else None,
    )

    await db.refresh(subscription, ["plan"])
    return SubscriptionResponse.model_validate(subscription)


@router.delete("/subscriptions/{subscription_id}", response_model=MessageResponse)
async def admin_cancel_subscription(
    subscription_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    result = await db.execute(
        select(Subscription).where(Subscription.id == subscription_id)
    )
    subscription = result.scalar_one_or_none()
    if subscription is None:
        raise HTTPException(status_code=404, detail="Subscription not found")

    if subscription.status == SubscriptionStatus.CANCELLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Subscription is already cancelled",
        )

    old_status = subscription.status.value
    subscription.status = SubscriptionStatus.CANCELLED

    # Clear the denormalized flag on the user
    user_r = await db.execute(select(User).where(User.id == subscription.user_id))
    sub_user = user_r.scalar_one_or_none()
    if sub_user is not None:
        sub_user.is_subscribed = False

    await db.flush()

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_cancel_subscription",
        resource_type="subscription",
        resource_id=str(subscription_id),
        details={"old_status": old_status},
        ip_address=request.client.host if request.client else None,
    )

    return MessageResponse(message="Subscription cancelled successfully")
