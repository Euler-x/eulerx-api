from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.base import get_db
from app.models.billing import Plan, Subscription
from app.models.enums import SubscriptionStatus
from app.models.user import User
from app.middleware.auth import get_current_user
from app.utils.helpers import utc_now


class SubscriptionInfo:
    def __init__(
        self,
        subscription: Subscription | None,
        plan: Plan | None,
        is_active: bool,
    ):
        self.subscription = subscription
        self.plan = plan
        self.is_active = is_active

    @property
    def max_strategies(self) -> int:
        if self.plan:
            return self.plan.max_strategies
        return 0

    @property
    def max_allocation(self) -> float:
        if self.plan:
            return float(self.plan.max_allocation)
        return 0.0

    @property
    def has_ate_access(self) -> bool:
        if self.plan:
            return self.plan.ate_access
        return False


async def get_subscription_info(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SubscriptionInfo:
    result = await db.execute(
        select(Subscription)
        .options(selectinload(Subscription.plan))
        .where(Subscription.user_id == current_user.id)
        .where(
            Subscription.status.in_([
                SubscriptionStatus.ACTIVE,
                SubscriptionStatus.EXPIRING_SOON,
            ])
        )
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    subscription = result.scalar_one_or_none()

    if subscription is None:
        return SubscriptionInfo(None, None, False)

    plan = subscription.plan

    now = utc_now()
    is_active = True

    if subscription.expires_at and subscription.expires_at < now:
        if subscription.grace_until and subscription.grace_until >= now:
            is_active = True
        else:
            is_active = False
            subscription.status = SubscriptionStatus.EXPIRED

    return SubscriptionInfo(subscription, plan, is_active)


async def require_active_subscription(
    sub_info: SubscriptionInfo = Depends(get_subscription_info),
) -> SubscriptionInfo:
    if not sub_info.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active subscription required. Please upgrade your plan.",
        )
    return sub_info


async def require_ate_access(
    sub_info: SubscriptionInfo = Depends(require_active_subscription),
) -> SubscriptionInfo:
    if not sub_info.has_ate_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your current plan does not include ATE access. Please upgrade.",
        )
    return sub_info
