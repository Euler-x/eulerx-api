"""Admin analytics router — revenue, user growth, execution statistics, and comprehensive dashboard."""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.models.billing import Payment, Plan, Subscription
from app.models.enums import (
    ExecutionStatus,
    SignalStatus,
    SubscriptionStatus,
    TicketStatus,
)
from app.models.execution import Execution
from app.models.schemas.admin import (
    ExecutionStatsResponse,
    RevenueAnalyticsResponse,
    UserGrowthResponse,
)
from app.models.signal import Signal
from app.models.strategy import Strategy
from app.models.support import SupportTicket
from app.models.user import User
from app.utils.helpers import utc_now

router = APIRouter()


# ── Response Schemas ──────────────────────────────────────────


class DashboardResponse(BaseModel):
    total_users: int
    new_users_24h: int
    new_users_7d: int
    total_revenue_usd: float
    revenue_30d: float
    active_subscriptions: int
    total_executions: int
    executions_24h: int
    filled_executions: int
    failed_executions: int
    total_pnl: float
    pnl_today: float
    win_rate: float
    active_strategies: int
    active_signals: int
    open_tickets_count: int


class PnlByDay(BaseModel):
    date: str
    pnl: float
    trades_count: int


class ExecutionsByDay(BaseModel):
    date: str
    filled: int
    failed: int
    total: int


class SymbolTradingStats(BaseModel):
    symbol: str
    trades: int
    wins: int
    losses: int
    total_pnl: float
    avg_pnl: float


class DirectionStats(BaseModel):
    trades: int
    wins: int
    pnl: float


class StrategyTypeStats(BaseModel):
    strategy_type: str
    trades: int
    wins: int
    total_pnl: float


class TradingAnalyticsResponse(BaseModel):
    win_rate: float
    loss_rate: float
    total_trades: int
    avg_pnl: float
    best_trade: float
    worst_trade: float
    pnl_by_day: list[PnlByDay]
    executions_by_day: list[ExecutionsByDay]
    by_symbol: list[SymbolTradingStats]
    by_direction: dict[str, DirectionStats]
    by_strategy_type: list[StrategyTypeStats]


class SignalStatusBreakdown(BaseModel):
    new: int
    executing: int
    filled: int
    expired: int
    cancelled: int


class SignalBySymbol(BaseModel):
    symbol: str
    total: int
    filled: int
    expired: int
    avg_confidence: float


class SignalsByDay(BaseModel):
    date: str
    count: int
    filled: int
    expired: int


class SignalAnalyticsResponse(BaseModel):
    total_signals: int
    by_status: SignalStatusBreakdown
    accuracy_rate: float
    avg_confidence: float
    avg_rr_ratio: float
    by_symbol: list[SignalBySymbol]
    signals_by_day: list[SignalsByDay]


class UserChartPoint(BaseModel):
    date: str
    new_users: int
    cumulative_total: int


class RevenueChartPoint(BaseModel):
    date: str
    revenue_usd: float
    payments_count: int


# ── Existing Endpoints (unchanged) ───────────────────────────


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
    active_subs = (
        await db.execute(
            select(func.count(Subscription.id)).where(
                Subscription.status == SubscriptionStatus.ACTIVE
            )
        )
    ).scalar() or 0

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
        revenue_by_plan.append(
            {
                "plan_name": row[0],
                "subscriptions": row[1],
                "revenue_usd": float(row[2]),
            }
        )

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
    active_users = (
        await db.execute(
            select(func.count(User.id)).where(User.is_active == True)  # noqa: E712
        )
    ).scalar() or 0
    admin_users = (
        await db.execute(
            select(func.count(User.id)).where(User.is_admin == True)  # noqa: E712
        )
    ).scalar() or 0

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

    total_query = (
        select(func.count(Execution.id)).where(*base_filter)
        if base_filter
        else select(func.count(Execution.id))
    )
    total = (await db.execute(total_query)).scalar() or 0

    # Counts by status
    status_query = select(
        func.count(case((Execution.status == ExecutionStatus.PENDING, 1))).label(
            "pending"
        ),
        func.count(case((Execution.status == ExecutionStatus.FILLED, 1))).label(
            "filled"
        ),
        func.count(case((Execution.status == ExecutionStatus.FAILED, 1))).label(
            "failed"
        ),
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


# ── New Endpoints ─────────────────────────────────────────────


@router.get("/analytics/dashboard", response_model=DashboardResponse)
async def admin_dashboard(db: AsyncSession = Depends(get_db)):
    """Single comprehensive dashboard call with all key metrics."""
    now = utc_now()
    ago_24h = now - timedelta(hours=24)
    ago_7d = now - timedelta(days=7)
    ago_30d = now - timedelta(days=30)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # -- Users --
    total_users = (await db.execute(select(func.count(User.id)))).scalar() or 0
    new_users_24h = (
        await db.execute(select(func.count(User.id)).where(User.created_at >= ago_24h))
    ).scalar() or 0
    new_users_7d = (
        await db.execute(select(func.count(User.id)).where(User.created_at >= ago_7d))
    ).scalar() or 0

    # -- Revenue --
    total_revenue_usd = float(
        (
            await db.execute(
                select(func.coalesce(func.sum(Payment.amount_usd), 0)).where(
                    Payment.status.in_(["finished", "confirmed"])
                )
            )
        ).scalar()
        or 0
    )
    revenue_30d = float(
        (
            await db.execute(
                select(func.coalesce(func.sum(Payment.amount_usd), 0)).where(
                    Payment.status.in_(["finished", "confirmed"]),
                    Payment.created_at >= ago_30d,
                )
            )
        ).scalar()
        or 0
    )

    # -- Subscriptions --
    active_subscriptions = (
        await db.execute(
            select(func.count(Subscription.id)).where(
                Subscription.status == SubscriptionStatus.ACTIVE
            )
        )
    ).scalar() or 0

    # -- Executions --
    exec_stats = (
        await db.execute(
            select(
                func.count(Execution.id).label("total"),
                func.count(case((Execution.created_at >= ago_24h, 1))).label(
                    "last_24h"
                ),
                func.count(case((Execution.status == ExecutionStatus.FILLED, 1))).label(
                    "filled"
                ),
                func.count(case((Execution.status == ExecutionStatus.FAILED, 1))).label(
                    "failed"
                ),
                func.coalesce(func.sum(Execution.pnl), 0).label("total_pnl"),
            )
        )
    ).one()
    total_executions = exec_stats.total
    executions_24h = exec_stats.last_24h
    filled_executions = exec_stats.filled
    failed_executions = exec_stats.failed
    total_pnl = float(exec_stats.total_pnl)

    # PnL today
    pnl_today = float(
        (
            await db.execute(
                select(func.coalesce(func.sum(Execution.pnl), 0)).where(
                    Execution.status.in_(
                        [ExecutionStatus.CLOSED, ExecutionStatus.FILLED]
                    ),
                    Execution.created_at >= today_start,
                )
            )
        ).scalar()
        or 0
    )

    # Win rate: % of closed executions with positive PnL
    closed_statuses = [ExecutionStatus.CLOSED, ExecutionStatus.FILLED]
    closed_total = (
        await db.execute(
            select(func.count(Execution.id)).where(
                Execution.status.in_(closed_statuses),
                Execution.pnl.isnot(None),
            )
        )
    ).scalar() or 0
    wins = (
        await db.execute(
            select(func.count(Execution.id)).where(
                Execution.status.in_(closed_statuses),
                Execution.pnl > 0,
            )
        )
    ).scalar() or 0
    win_rate = round((wins / closed_total * 100) if closed_total > 0 else 0.0, 2)

    # -- Strategies --
    active_strategies = (
        await db.execute(
            select(func.count(Strategy.id)).where(
                Strategy.is_active == True  # noqa: E712
            )
        )
    ).scalar() or 0

    # -- Signals --
    active_signals = (
        await db.execute(
            select(func.count(Signal.id)).where(
                Signal.status.in_([SignalStatus.NEW, SignalStatus.EXECUTING])
            )
        )
    ).scalar() or 0

    # -- Support tickets --
    open_tickets_count = (
        await db.execute(
            select(func.count(SupportTicket.id)).where(
                SupportTicket.status.in_([TicketStatus.OPEN, TicketStatus.IN_PROGRESS])
            )
        )
    ).scalar() or 0

    return DashboardResponse(
        total_users=total_users,
        new_users_24h=new_users_24h,
        new_users_7d=new_users_7d,
        total_revenue_usd=total_revenue_usd,
        revenue_30d=revenue_30d,
        active_subscriptions=active_subscriptions,
        total_executions=total_executions,
        executions_24h=executions_24h,
        filled_executions=filled_executions,
        failed_executions=failed_executions,
        total_pnl=total_pnl,
        pnl_today=pnl_today,
        win_rate=win_rate,
        active_strategies=active_strategies,
        active_signals=active_signals,
        open_tickets_count=open_tickets_count,
    )


@router.get("/analytics/trading", response_model=TradingAnalyticsResponse)
async def admin_trading_analytics(
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """Trading performance analytics over a given period."""
    now = utc_now()
    since = now - timedelta(days=days)

    closed_statuses = [ExecutionStatus.CLOSED, ExecutionStatus.FILLED]

    # -- Aggregate stats (closed/filled only, within period) --
    agg = (
        await db.execute(
            select(
                func.count(Execution.id).label("total_trades"),
                func.count(case((Execution.pnl > 0, 1))).label("wins"),
                func.count(case((Execution.pnl <= 0, 1))).label("losses"),
                func.coalesce(func.avg(Execution.pnl), 0).label("avg_pnl"),
                func.coalesce(func.max(Execution.pnl), 0).label("best_trade"),
                func.coalesce(func.min(Execution.pnl), 0).label("worst_trade"),
            ).where(
                Execution.status.in_(closed_statuses),
                Execution.pnl.isnot(None),
                Execution.created_at >= since,
            )
        )
    ).one()

    total_trades = agg.total_trades
    wins = agg.wins
    losses = agg.losses
    win_rate = round((wins / total_trades * 100) if total_trades > 0 else 0.0, 2)
    loss_rate = round((losses / total_trades * 100) if total_trades > 0 else 0.0, 2)

    # -- PnL by day --
    pnl_by_day_q = (
        select(
            func.date(Execution.created_at).label("day"),
            func.coalesce(func.sum(Execution.pnl), 0).label("pnl"),
            func.count(Execution.id).label("trades_count"),
        )
        .where(
            Execution.status.in_(closed_statuses),
            Execution.pnl.isnot(None),
            Execution.created_at >= since,
        )
        .group_by(func.date(Execution.created_at))
        .order_by(func.date(Execution.created_at))
    )
    pnl_by_day_rows = (await db.execute(pnl_by_day_q)).all()
    pnl_by_day = [
        PnlByDay(date=str(r.day), pnl=float(r.pnl), trades_count=r.trades_count)
        for r in pnl_by_day_rows
    ]

    # -- Executions by day (all statuses) --
    exec_by_day_q = (
        select(
            func.date(Execution.created_at).label("day"),
            func.count(case((Execution.status.in_(closed_statuses), 1))).label(
                "filled"
            ),
            func.count(case((Execution.status == ExecutionStatus.FAILED, 1))).label(
                "failed"
            ),
            func.count(Execution.id).label("total"),
        )
        .where(Execution.created_at >= since)
        .group_by(func.date(Execution.created_at))
        .order_by(func.date(Execution.created_at))
    )
    exec_by_day_rows = (await db.execute(exec_by_day_q)).all()
    executions_by_day = [
        ExecutionsByDay(
            date=str(r.day), filled=r.filled, failed=r.failed, total=r.total
        )
        for r in exec_by_day_rows
    ]

    # -- By symbol (join Signal to get symbol) --
    by_symbol_q = (
        select(
            Signal.symbol,
            func.count(Execution.id).label("trades"),
            func.count(case((Execution.pnl > 0, 1))).label("wins"),
            func.count(case((Execution.pnl <= 0, 1))).label("losses"),
            func.coalesce(func.sum(Execution.pnl), 0).label("total_pnl"),
            func.coalesce(func.avg(Execution.pnl), 0).label("avg_pnl"),
        )
        .join(Signal, Execution.signal_id == Signal.id)
        .where(
            Execution.status.in_(closed_statuses),
            Execution.pnl.isnot(None),
            Execution.created_at >= since,
        )
        .group_by(Signal.symbol)
        .order_by(func.sum(Execution.pnl).desc())
    )
    by_symbol_rows = (await db.execute(by_symbol_q)).all()
    by_symbol = [
        SymbolTradingStats(
            symbol=r.symbol,
            trades=r.trades,
            wins=r.wins,
            losses=r.losses,
            total_pnl=float(r.total_pnl),
            avg_pnl=float(r.avg_pnl),
        )
        for r in by_symbol_rows
    ]

    # -- By direction --
    by_dir_q = (
        select(
            Execution.direction,
            func.count(Execution.id).label("trades"),
            func.count(case((Execution.pnl > 0, 1))).label("wins"),
            func.coalesce(func.sum(Execution.pnl), 0).label("pnl"),
        )
        .where(
            Execution.status.in_(closed_statuses),
            Execution.pnl.isnot(None),
            Execution.created_at >= since,
        )
        .group_by(Execution.direction)
    )
    by_dir_rows = (await db.execute(by_dir_q)).all()
    by_direction: dict[str, DirectionStats] = {}
    for r in by_dir_rows:
        key = r.direction.value if hasattr(r.direction, "value") else str(r.direction)
        by_direction[key.lower()] = DirectionStats(
            trades=r.trades, wins=r.wins, pnl=float(r.pnl)
        )
    # Ensure both keys exist
    for key in ("buy", "sell"):
        if key not in by_direction:
            by_direction[key] = DirectionStats(trades=0, wins=0, pnl=0.0)

    # -- By strategy type --
    by_st_q = (
        select(
            Strategy.strategy_type,
            func.count(Execution.id).label("trades"),
            func.count(case((Execution.pnl > 0, 1))).label("wins"),
            func.coalesce(func.sum(Execution.pnl), 0).label("total_pnl"),
        )
        .join(Strategy, Execution.strategy_id == Strategy.id)
        .where(
            Execution.status.in_(closed_statuses),
            Execution.pnl.isnot(None),
            Execution.created_at >= since,
        )
        .group_by(Strategy.strategy_type)
    )
    by_st_rows = (await db.execute(by_st_q)).all()
    by_strategy_type = [
        StrategyTypeStats(
            strategy_type=(
                r.strategy_type.value
                if hasattr(r.strategy_type, "value")
                else str(r.strategy_type)
            ),
            trades=r.trades,
            wins=r.wins,
            total_pnl=float(r.total_pnl),
        )
        for r in by_st_rows
    ]

    return TradingAnalyticsResponse(
        win_rate=win_rate,
        loss_rate=loss_rate,
        total_trades=total_trades,
        avg_pnl=float(agg.avg_pnl),
        best_trade=float(agg.best_trade),
        worst_trade=float(agg.worst_trade),
        pnl_by_day=pnl_by_day,
        executions_by_day=executions_by_day,
        by_symbol=by_symbol,
        by_direction=by_direction,
        by_strategy_type=by_strategy_type,
    )


@router.get("/analytics/signals", response_model=SignalAnalyticsResponse)
async def admin_signal_analytics(
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """Signal quality analytics over a given period."""
    now = utc_now()
    since = now - timedelta(days=days)

    # -- Total signals & status breakdown --
    status_q = (
        await db.execute(
            select(
                func.count(Signal.id).label("total"),
                func.count(case((Signal.status == SignalStatus.NEW, 1))).label("new"),
                func.count(case((Signal.status == SignalStatus.EXECUTING, 1))).label(
                    "executing"
                ),
                func.count(case((Signal.status == SignalStatus.FILLED, 1))).label(
                    "filled"
                ),
                func.count(case((Signal.status == SignalStatus.EXPIRED, 1))).label(
                    "expired"
                ),
                func.count(case((Signal.status == SignalStatus.CANCELLED, 1))).label(
                    "cancelled"
                ),
                func.coalesce(func.avg(Signal.confidence), 0).label("avg_confidence"),
                func.coalesce(func.avg(Signal.risk_reward_ratio), 0).label(
                    "avg_rr_ratio"
                ),
            ).where(Signal.created_at >= since)
        )
    ).one()

    total_signals = status_q.total

    # -- Accuracy rate: filled signals that led to positive-PnL executions / total filled --
    filled_signal_count = status_q.filled
    if filled_signal_count > 0:
        positive_pnl_signals = (
            await db.execute(
                select(func.count(func.distinct(Signal.id)))
                .join(Execution, Execution.signal_id == Signal.id)
                .where(
                    Signal.status == SignalStatus.FILLED,
                    Signal.created_at >= since,
                    Execution.pnl > 0,
                )
            )
        ).scalar() or 0
        accuracy_rate = round(positive_pnl_signals / filled_signal_count * 100, 2)
    else:
        accuracy_rate = 0.0

    # -- By symbol --
    by_symbol_q = (
        select(
            Signal.symbol,
            func.count(Signal.id).label("total"),
            func.count(case((Signal.status == SignalStatus.FILLED, 1))).label("filled"),
            func.count(case((Signal.status == SignalStatus.EXPIRED, 1))).label(
                "expired"
            ),
            func.coalesce(func.avg(Signal.confidence), 0).label("avg_confidence"),
        )
        .where(Signal.created_at >= since)
        .group_by(Signal.symbol)
        .order_by(func.count(Signal.id).desc())
    )
    by_symbol_rows = (await db.execute(by_symbol_q)).all()
    by_symbol = [
        SignalBySymbol(
            symbol=r.symbol,
            total=r.total,
            filled=r.filled,
            expired=r.expired,
            avg_confidence=round(float(r.avg_confidence), 4),
        )
        for r in by_symbol_rows
    ]

    # -- Signals by day --
    by_day_q = (
        select(
            func.date(Signal.created_at).label("day"),
            func.count(Signal.id).label("count"),
            func.count(case((Signal.status == SignalStatus.FILLED, 1))).label("filled"),
            func.count(case((Signal.status == SignalStatus.EXPIRED, 1))).label(
                "expired"
            ),
        )
        .where(Signal.created_at >= since)
        .group_by(func.date(Signal.created_at))
        .order_by(func.date(Signal.created_at))
    )
    by_day_rows = (await db.execute(by_day_q)).all()
    signals_by_day = [
        SignalsByDay(date=str(r.day), count=r.count, filled=r.filled, expired=r.expired)
        for r in by_day_rows
    ]

    return SignalAnalyticsResponse(
        total_signals=total_signals,
        by_status=SignalStatusBreakdown(
            new=status_q.new,
            executing=status_q.executing,
            filled=status_q.filled,
            expired=status_q.expired,
            cancelled=status_q.cancelled,
        ),
        accuracy_rate=accuracy_rate,
        avg_confidence=round(float(status_q.avg_confidence), 4),
        avg_rr_ratio=round(float(status_q.avg_rr_ratio), 4),
        by_symbol=by_symbol,
        signals_by_day=signals_by_day,
    )


@router.get("/analytics/users-chart", response_model=list[UserChartPoint])
async def admin_users_chart(
    days: int = Query(default=90, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """User growth time series with cumulative totals."""
    now = utc_now()
    since = now - timedelta(days=days)

    # Count of users created before the period (baseline)
    baseline = (
        await db.execute(select(func.count(User.id)).where(User.created_at < since))
    ).scalar() or 0

    # New users per day within period
    daily_q = (
        select(
            func.date(User.created_at).label("day"),
            func.count(User.id).label("new_users"),
        )
        .where(User.created_at >= since)
        .group_by(func.date(User.created_at))
        .order_by(func.date(User.created_at))
    )
    daily_rows = (await db.execute(daily_q)).all()

    # Build time series with cumulative total
    result: list[UserChartPoint] = []
    cumulative = baseline
    for r in daily_rows:
        cumulative += r.new_users
        result.append(
            UserChartPoint(
                date=str(r.day),
                new_users=r.new_users,
                cumulative_total=cumulative,
            )
        )

    return result


@router.get("/analytics/revenue-chart", response_model=list[RevenueChartPoint])
async def admin_revenue_chart(
    days: int = Query(default=90, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """Revenue time series with daily totals."""
    now = utc_now()
    since = now - timedelta(days=days)

    daily_q = (
        select(
            func.date(Payment.created_at).label("day"),
            func.coalesce(func.sum(Payment.amount_usd), 0).label("revenue_usd"),
            func.count(Payment.id).label("payments_count"),
        )
        .where(
            Payment.status.in_(["finished", "confirmed"]),
            Payment.created_at >= since,
        )
        .group_by(func.date(Payment.created_at))
        .order_by(func.date(Payment.created_at))
    )
    daily_rows = (await db.execute(daily_q)).all()

    return [
        RevenueChartPoint(
            date=str(r.day),
            revenue_usd=float(r.revenue_usd),
            payments_count=r.payments_count,
        )
        for r in daily_rows
    ]
