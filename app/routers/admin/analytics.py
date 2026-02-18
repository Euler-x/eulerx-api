"""Admin analytics router — revenue, user growth, and execution statistics."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.models.billing import Payment, Plan, Subscription
from app.models.enums import ExecutionStatus, SubscriptionStatus
from app.models.execution import Execution
from app.models.schemas.admin import (
    ExecutionStatsResponse,
    RevenueAnalyticsResponse,
    UserGrowthResponse,
)
from app.models.user import User

router = APIRouter()


@router.get("/analytics/revenue", response_model=RevenueAnalyticsResponse)
async def admin_revenue_analytics(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db),
):
    # Total revenue (all time)
    total_rev_query = select(func.sum(Payment.amount_usd)).where(
        Payment.status.in_(["finished", "confirmed"])
    )
    total_revenue = (await db.execute(total_rev_query)).scalar() or 0

    # Period revenue (if date filters provided)
    period_revenue = None
    if start_date or end_date:
        period_query = select(func.sum(Payment.amount_usd)).where(
            Payment.status.in_(["finished", "confirmed"])
        )
        if start_date:
            period_query = period_query.where(Payment.created_at >= start_date)
        if end_date:
            period_query = period_query.where(Payment.created_at <= end_date)
        period_revenue = float((await db.execute(period_query)).scalar() or 0)

    # Active subscriptions
    active_subs = (await db.execute(
        select(func.count(Subscription.id)).where(
            Subscription.status == SubscriptionStatus.ACTIVE
        )
    )).scalar() or 0

    # Total users
    total_users = (await db.execute(select(func.count(User.id)))).scalar() or 0

    # Revenue by plan
    revenue_by_plan = []
    plan_query = (
        select(
            Plan.name,
            func.count(Subscription.id).label("count"),
            func.coalesce(func.sum(Payment.amount_usd), 0).label("revenue"),
        )
        .join(Subscription, Subscription.plan_id == Plan.id)
        .outerjoin(
            Payment,
            (Payment.subscription_id == Subscription.id)
            & Payment.status.in_(["finished", "confirmed"]),
        )
        .group_by(Plan.name)
    )
    plan_result = await db.execute(plan_query)
    for row in plan_result.all():
        revenue_by_plan.append({
            "plan_name": row[0],
            "subscriptions": row[1],
            "revenue_usd": float(row[2]),
        })

    return RevenueAnalyticsResponse(
        total_revenue_usd=float(total_revenue),
        active_subscriptions=active_subs,
        total_users=total_users,
        revenue_by_plan=revenue_by_plan,
        period_revenue_usd=period_revenue,
    )


@router.get("/analytics/users", response_model=UserGrowthResponse)
async def admin_user_growth(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db),
):
    total_users = (await db.execute(select(func.count(User.id)))).scalar() or 0
    active_users = (await db.execute(
        select(func.count(User.id)).where(User.is_active == True)  # noqa: E712
    )).scalar() or 0
    admin_users = (await db.execute(
        select(func.count(User.id)).where(User.is_admin == True)  # noqa: E712
    )).scalar() or 0

    # New users in period
    new_query = select(func.count(User.id))
    if start_date:
        new_query = new_query.where(User.created_at >= start_date)
    if end_date:
        new_query = new_query.where(User.created_at <= end_date)
    new_users_period = (await db.execute(new_query)).scalar() or 0

    return UserGrowthResponse(
        total_users=total_users,
        new_users_period=new_users_period,
        active_users=active_users,
        admin_users=admin_users,
    )


@router.get("/analytics/executions", response_model=ExecutionStatsResponse)
async def admin_execution_stats(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db),
):
    base_filter = []
    if start_date:
        base_filter.append(Execution.created_at >= start_date)
    if end_date:
        base_filter.append(Execution.created_at <= end_date)

    total_query = select(func.count(Execution.id)).where(*base_filter) if base_filter else select(func.count(Execution.id))
    total = (await db.execute(total_query)).scalar() or 0

    # Counts by status
    status_query = select(
        func.count(case((Execution.status == ExecutionStatus.PENDING, 1))).label("pending"),
        func.count(case((Execution.status == ExecutionStatus.FILLED, 1))).label("filled"),
        func.count(case((Execution.status == ExecutionStatus.FAILED, 1))).label("failed"),
        func.coalesce(func.sum(Execution.pnl), 0).label("total_pnl"),
    )
    if base_filter:
        status_query = status_query.where(*base_filter)

    row = (await db.execute(status_query)).one()

    return ExecutionStatsResponse(
        total_executions=total,
        pending=row.pending,
        filled=row.filled,
        failed=row.failed,
        total_pnl=float(row.total_pnl),
    )
