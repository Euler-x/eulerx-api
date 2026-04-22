"""Admin analytics router — revenue, user growth, execution statistics, and comprehensive dashboard."""

import math
import statistics
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.models.billing import Payment, Plan, Subscription
from app.models.bybit_signal import BybitSignal
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

    # Win rate: gross profit as % of (gross profit + gross loss)
    closed_statuses = [ExecutionStatus.CLOSED, ExecutionStatus.FILLED]
    pnl_agg = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(case((Execution.pnl > 0, Execution.pnl), else_=0)), 0
                ).label("gross_profit"),
                func.coalesce(
                    func.sum(case((Execution.pnl < 0, Execution.pnl), else_=0)), 0
                ).label("gross_loss"),
            ).where(
                Execution.status.in_(closed_statuses),
                Execution.pnl.isnot(None),
            )
        )
    ).one()
    _gp = float(pnl_agg.gross_profit)
    _gl = abs(float(pnl_agg.gross_loss))
    win_rate = round(_gp / (_gp + _gl) * 100 if (_gp + _gl) > 0 else 0.0, 2)

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
    # Win rate: gross profit as % of (gross profit + gross loss)
    pnl_sums = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(case((Execution.pnl > 0, Execution.pnl), else_=0)), 0
                ).label("gross_profit"),
                func.coalesce(
                    func.sum(case((Execution.pnl < 0, Execution.pnl), else_=0)), 0
                ).label("gross_loss"),
            ).where(
                Execution.status.in_(closed_statuses),
                Execution.pnl.isnot(None),
                Execution.created_at >= since,
            )
        )
    ).one()
    _gp = float(pnl_sums.gross_profit)
    _gl = abs(float(pnl_sums.gross_loss))
    win_rate = round(_gp / (_gp + _gl) * 100 if (_gp + _gl) > 0 else 0.0, 2)
    loss_rate = round(100 - win_rate if total_trades > 0 else 0.0, 2)

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


# ── Performance Analytics (comprehensive) ─────────────────


class ExchangeStats(BaseModel):
    exchange: str
    trades: int
    wins: int
    losses: int
    total_pnl: float
    avg_pnl: float
    volume: float


class SignalFunnel(BaseModel):
    generated: int
    executed: int
    filled: int
    profitable: int
    conversion_rate: float
    profitability_rate: float


class RecentTrade(BaseModel):
    id: str
    symbol: str
    direction: str
    exchange: str
    pnl: Optional[float]
    status: str
    entry_price: float
    exit_price: Optional[float]
    leverage: float
    created_at: str


class PerformanceAnalyticsResponse(BaseModel):
    total_trades: int
    win_rate: float
    loss_rate: float
    total_pnl: float
    avg_pnl: float
    profit_factor: float
    sharpe_ratio: float
    max_drawdown: float
    best_trade: float
    worst_trade: float
    trade_volume: float
    active_users: int

    pnl_by_day: list[PnlByDay]
    cumulative_pnl_by_day: list[dict]
    executions_by_day: list[ExecutionsByDay]

    by_exchange: list[ExchangeStats]
    by_direction: dict[str, DirectionStats]
    by_symbol: list[SymbolTradingStats]
    by_strategy_type: list[StrategyTypeStats]

    signal_funnel: SignalFunnel
    recent_trades: list[RecentTrade]


def _resolve_period(
    period: str,
    start_date: Optional[datetime],
    end_date: Optional[datetime],
) -> tuple[datetime, datetime]:
    now = utc_now()
    if period == "today":
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "7d":
        since = now - timedelta(days=7)
    elif period == "30d":
        since = now - timedelta(days=30)
    elif period == "90d":
        since = now - timedelta(days=90)
    elif period == "custom":
        if not start_date or not end_date:
            raise HTTPException(
                400, "start_date and end_date required for custom period"
            )
        return start_date, end_date
    else:
        since = now - timedelta(days=30)
    return since, now


@router.get("/analytics/performance", response_model=PerformanceAnalyticsResponse)
async def admin_performance_analytics(
    period: str = Query(default="30d"),
    start_date: Optional[datetime] = Query(default=None),
    end_date: Optional[datetime] = Query(default=None),
    exchange: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Comprehensive trading performance analytics with period and exchange filters."""
    since, until = _resolve_period(period, start_date, end_date)

    closed_statuses = [ExecutionStatus.CLOSED, ExecutionStatus.FILLED]

    # Base filters
    base_filters = [
        Execution.status.in_(closed_statuses),
        Execution.pnl.isnot(None),
        Execution.created_at >= since,
        Execution.created_at <= until,
    ]
    if exchange:
        base_filters.append(Execution.exchange == exchange)

    # ── 1. Core aggregates ───────────────────────────────────
    agg = (
        await db.execute(
            select(
                func.count(Execution.id).label("total_trades"),
                func.count(case((Execution.pnl > 0, 1))).label("wins"),
                func.count(case((Execution.pnl <= 0, 1))).label("losses"),
                func.coalesce(func.sum(Execution.pnl), 0).label("total_pnl"),
                func.coalesce(func.avg(Execution.pnl), 0).label("avg_pnl"),
                func.coalesce(func.max(Execution.pnl), 0).label("best_trade"),
                func.coalesce(func.min(Execution.pnl), 0).label("worst_trade"),
                func.coalesce(
                    func.sum(
                        Execution.quantity * Execution.entry_price * Execution.leverage
                    ),
                    0,
                ).label("trade_volume"),
                func.count(func.distinct(Execution.user_id)).label("active_users"),
                func.coalesce(
                    func.sum(case((Execution.pnl > 0, Execution.pnl), else_=0)),
                    0,
                ).label("gross_profit"),
                func.coalesce(
                    func.sum(case((Execution.pnl < 0, Execution.pnl), else_=0)),
                    0,
                ).label("gross_loss"),
            ).where(*base_filters)
        )
    ).one()

    total_trades = agg.total_trades
    gross_profit = float(agg.gross_profit)
    gross_loss = abs(float(agg.gross_loss))
    win_rate = round(
        gross_profit / (gross_profit + gross_loss) * 100
        if (gross_profit + gross_loss) > 0
        else 0.0,
        2,
    )
    loss_rate = round(100 - win_rate if total_trades > 0 else 0.0, 2)
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0.0

    # ── 2. PnL by day + cumulative + Sharpe + max drawdown ──
    pnl_day_q = (
        select(
            func.date(Execution.created_at).label("day"),
            func.coalesce(func.sum(Execution.pnl), 0).label("pnl"),
            func.count(Execution.id).label("trades_count"),
        )
        .where(*base_filters)
        .group_by(func.date(Execution.created_at))
        .order_by(func.date(Execution.created_at))
    )
    pnl_day_rows = (await db.execute(pnl_day_q)).all()

    pnl_by_day = []
    cumulative_pnl_by_day = []
    daily_pnls = []
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0

    for r in pnl_day_rows:
        day_pnl = float(r.pnl)
        daily_pnls.append(day_pnl)
        cumulative += day_pnl
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_drawdown:
            max_drawdown = dd

        pnl_by_day.append(
            PnlByDay(date=str(r.day), pnl=day_pnl, trades_count=r.trades_count)
        )
        cumulative_pnl_by_day.append(
            {"date": str(r.day), "cumulative_pnl": round(cumulative, 2)}
        )

    # Sharpe ratio
    if len(daily_pnls) > 1:
        mean_pnl = statistics.mean(daily_pnls)
        stdev_pnl = statistics.stdev(daily_pnls)
        sharpe_ratio = (
            round((mean_pnl / stdev_pnl) * math.sqrt(365), 2) if stdev_pnl > 0 else 0.0
        )
    else:
        sharpe_ratio = 0.0

    # ── 3. Executions by day (all statuses) ──────────────────
    exec_day_filters = [Execution.created_at >= since, Execution.created_at <= until]
    if exchange:
        exec_day_filters.append(Execution.exchange == exchange)

    exec_day_q = (
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
        .where(*exec_day_filters)
        .group_by(func.date(Execution.created_at))
        .order_by(func.date(Execution.created_at))
    )
    exec_day_rows = (await db.execute(exec_day_q)).all()
    executions_by_day = [
        ExecutionsByDay(
            date=str(r.day), filled=r.filled, failed=r.failed, total=r.total
        )
        for r in exec_day_rows
    ]

    # ── 4. By exchange ───────────────────────────────────────
    ex_filters = [
        Execution.status.in_(closed_statuses),
        Execution.pnl.isnot(None),
        Execution.created_at >= since,
        Execution.created_at <= until,
    ]
    ex_q = (
        select(
            Execution.exchange,
            func.count(Execution.id).label("trades"),
            func.count(case((Execution.pnl > 0, 1))).label("wins"),
            func.count(case((Execution.pnl <= 0, 1))).label("losses"),
            func.coalesce(func.sum(Execution.pnl), 0).label("total_pnl"),
            func.coalesce(func.avg(Execution.pnl), 0).label("avg_pnl"),
            func.coalesce(
                func.sum(
                    Execution.quantity * Execution.entry_price * Execution.leverage
                ),
                0,
            ).label("volume"),
        )
        .where(*ex_filters)
        .group_by(Execution.exchange)
    )
    ex_rows = (await db.execute(ex_q)).all()
    by_exchange = [
        ExchangeStats(
            exchange=r.exchange.value
            if hasattr(r.exchange, "value")
            else str(r.exchange),
            trades=r.trades,
            wins=r.wins,
            losses=r.losses,
            total_pnl=round(float(r.total_pnl), 2),
            avg_pnl=round(float(r.avg_pnl), 2),
            volume=round(float(r.volume), 2),
        )
        for r in ex_rows
    ]

    # ── 5. By direction ──────────────────────────────────────
    dir_q = (
        select(
            Execution.direction,
            func.count(Execution.id).label("trades"),
            func.count(case((Execution.pnl > 0, 1))).label("wins"),
            func.coalesce(func.sum(Execution.pnl), 0).label("pnl"),
        )
        .where(*base_filters)
        .group_by(Execution.direction)
    )
    dir_rows = (await db.execute(dir_q)).all()
    by_direction: dict[str, DirectionStats] = {}
    for r in dir_rows:
        key = r.direction.value if hasattr(r.direction, "value") else str(r.direction)
        by_direction[key.lower()] = DirectionStats(
            trades=r.trades, wins=r.wins, pnl=round(float(r.pnl), 2)
        )
    for key in ("buy", "sell"):
        if key not in by_direction:
            by_direction[key] = DirectionStats(trades=0, wins=0, pnl=0.0)

    # ── 6. By symbol (HL + Bybit combined) ───────────────────
    # HL signals
    hl_sym_q = (
        select(
            Signal.symbol,
            func.count(Execution.id).label("trades"),
            func.count(case((Execution.pnl > 0, 1))).label("wins"),
            func.count(case((Execution.pnl <= 0, 1))).label("losses"),
            func.coalesce(func.sum(Execution.pnl), 0).label("total_pnl"),
            func.coalesce(func.avg(Execution.pnl), 0).label("avg_pnl"),
        )
        .join(Signal, Execution.signal_id == Signal.id)
        .where(*base_filters, Execution.signal_id.isnot(None))
        .group_by(Signal.symbol)
    )
    # Bybit signals
    bb_sym_q = (
        select(
            BybitSignal.symbol,
            func.count(Execution.id).label("trades"),
            func.count(case((Execution.pnl > 0, 1))).label("wins"),
            func.count(case((Execution.pnl <= 0, 1))).label("losses"),
            func.coalesce(func.sum(Execution.pnl), 0).label("total_pnl"),
            func.coalesce(func.avg(Execution.pnl), 0).label("avg_pnl"),
        )
        .join(BybitSignal, Execution.bybit_signal_id == BybitSignal.id)
        .where(*base_filters, Execution.bybit_signal_id.isnot(None))
        .group_by(BybitSignal.symbol)
    )

    hl_sym_rows = (await db.execute(hl_sym_q)).all()
    bb_sym_rows = (await db.execute(bb_sym_q)).all()

    # Merge by symbol name
    sym_map: dict[str, dict] = {}
    for r in list(hl_sym_rows) + list(bb_sym_rows):
        s = r.symbol
        if s not in sym_map:
            sym_map[s] = {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "total_pnl": 0.0,
                "avg_pnl_sum": 0.0,
                "count": 0,
            }
        sym_map[s]["trades"] += r.trades
        sym_map[s]["wins"] += r.wins
        sym_map[s]["losses"] += r.losses
        sym_map[s]["total_pnl"] += float(r.total_pnl)
        sym_map[s]["avg_pnl_sum"] += float(r.avg_pnl) * r.trades
        sym_map[s]["count"] += r.trades

    by_symbol = sorted(
        [
            SymbolTradingStats(
                symbol=s,
                trades=d["trades"],
                wins=d["wins"],
                losses=d["losses"],
                total_pnl=round(d["total_pnl"], 2),
                avg_pnl=round(d["avg_pnl_sum"] / d["count"], 2)
                if d["count"] > 0
                else 0.0,
            )
            for s, d in sym_map.items()
        ],
        key=lambda x: x.total_pnl,
        reverse=True,
    )[:20]

    # ── 7. By strategy type ──────────────────────────────────
    st_q = (
        select(
            Strategy.strategy_type,
            func.count(Execution.id).label("trades"),
            func.count(case((Execution.pnl > 0, 1))).label("wins"),
            func.coalesce(func.sum(Execution.pnl), 0).label("total_pnl"),
        )
        .join(Strategy, Execution.strategy_id == Strategy.id)
        .where(*base_filters)
        .group_by(Strategy.strategy_type)
    )
    st_rows = (await db.execute(st_q)).all()
    by_strategy_type = [
        StrategyTypeStats(
            strategy_type=r.strategy_type.value
            if hasattr(r.strategy_type, "value")
            else str(r.strategy_type),
            trades=r.trades,
            wins=r.wins,
            total_pnl=round(float(r.total_pnl), 2),
        )
        for r in st_rows
    ]

    # ── 8. Signal funnel (HL + Bybit combined) ───────────────
    hl_gen = (
        await db.execute(
            select(func.count(Signal.id)).where(
                Signal.created_at >= since, Signal.created_at <= until
            )
        )
    ).scalar() or 0
    hl_filled = (
        await db.execute(
            select(func.count(Signal.id)).where(
                Signal.status == SignalStatus.FILLED,
                Signal.created_at >= since,
                Signal.created_at <= until,
            )
        )
    ).scalar() or 0

    bb_gen = (
        await db.execute(
            select(func.count(BybitSignal.id)).where(
                BybitSignal.created_at >= since, BybitSignal.created_at <= until
            )
        )
    ).scalar() or 0
    bb_filled = (
        await db.execute(
            select(func.count(BybitSignal.id)).where(
                BybitSignal.status == SignalStatus.FILLED,
                BybitSignal.created_at >= since,
                BybitSignal.created_at <= until,
            )
        )
    ).scalar() or 0

    generated = hl_gen + bb_gen
    filled_signals = hl_filled + bb_filled

    # Executed = signals that have at least one execution
    executed_hl = (
        await db.execute(
            select(func.count(func.distinct(Execution.signal_id))).where(
                Execution.signal_id.isnot(None),
                Execution.created_at >= since,
                Execution.created_at <= until,
            )
        )
    ).scalar() or 0
    executed_bb = (
        await db.execute(
            select(func.count(func.distinct(Execution.bybit_signal_id))).where(
                Execution.bybit_signal_id.isnot(None),
                Execution.created_at >= since,
                Execution.created_at <= until,
            )
        )
    ).scalar() or 0
    executed = executed_hl + executed_bb

    # Profitable = filled executions with pnl > 0
    profitable = (
        await db.execute(
            select(func.count(Execution.id)).where(
                Execution.status.in_(closed_statuses),
                Execution.pnl > 0,
                Execution.created_at >= since,
                Execution.created_at <= until,
            )
        )
    ).scalar() or 0

    signal_funnel = SignalFunnel(
        generated=generated,
        executed=executed,
        filled=filled_signals,
        profitable=profitable,
        conversion_rate=round(
            (filled_signals / generated * 100) if generated > 0 else 0.0, 1
        ),
        profitability_rate=round(
            (profitable / filled_signals * 100) if filled_signals > 0 else 0.0, 1
        ),
    )

    # ── 9. Recent trades (last 20) ───────────────────────────
    recent_filters = [Execution.created_at >= since, Execution.created_at <= until]
    if exchange:
        recent_filters.append(Execution.exchange == exchange)

    recent_q = (
        select(Execution)
        .where(*recent_filters)
        .order_by(Execution.created_at.desc())
        .limit(20)
    )
    recent_rows = (await db.execute(recent_q)).scalars().all()

    # Get symbols for recent trades
    recent_signal_ids = [r.signal_id for r in recent_rows if r.signal_id]
    recent_bb_ids = [r.bybit_signal_id for r in recent_rows if r.bybit_signal_id]

    sig_symbols: dict[str, str] = {}
    if recent_signal_ids:
        sig_q = select(Signal.id, Signal.symbol).where(Signal.id.in_(recent_signal_ids))
        for row in (await db.execute(sig_q)).all():
            sig_symbols[str(row[0])] = row[1]
    if recent_bb_ids:
        bb_q = select(BybitSignal.id, BybitSignal.symbol).where(
            BybitSignal.id.in_(recent_bb_ids)
        )
        for row in (await db.execute(bb_q)).all():
            sig_symbols[str(row[0])] = row[1]

    recent_trades = []
    for r in recent_rows:
        sym = "?"
        if r.signal_id and str(r.signal_id) in sig_symbols:
            sym = sig_symbols[str(r.signal_id)]
        elif r.bybit_signal_id and str(r.bybit_signal_id) in sig_symbols:
            sym = sig_symbols[str(r.bybit_signal_id)]
        recent_trades.append(
            RecentTrade(
                id=str(r.id),
                symbol=sym,
                direction=r.direction.value
                if hasattr(r.direction, "value")
                else str(r.direction),
                exchange=r.exchange.value
                if hasattr(r.exchange, "value")
                else str(r.exchange),
                pnl=round(float(r.pnl), 2) if r.pnl is not None else None,
                status=r.status.value if hasattr(r.status, "value") else str(r.status),
                entry_price=float(r.entry_price),
                exit_price=float(r.exit_price) if r.exit_price else None,
                leverage=float(r.leverage),
                created_at=r.created_at.isoformat(),
            )
        )

    return PerformanceAnalyticsResponse(
        total_trades=total_trades,
        win_rate=win_rate,
        loss_rate=loss_rate,
        total_pnl=round(float(agg.total_pnl), 2),
        avg_pnl=round(float(agg.avg_pnl), 2),
        profit_factor=profit_factor,
        sharpe_ratio=sharpe_ratio,
        max_drawdown=round(max_drawdown, 2),
        best_trade=round(float(agg.best_trade), 2),
        worst_trade=round(float(agg.worst_trade), 2),
        trade_volume=round(float(agg.trade_volume), 2),
        active_users=agg.active_users,
        pnl_by_day=pnl_by_day,
        cumulative_pnl_by_day=cumulative_pnl_by_day,
        executions_by_day=executions_by_day,
        by_exchange=by_exchange,
        by_direction=by_direction,
        by_symbol=by_symbol,
        by_strategy_type=by_strategy_type,
        signal_funnel=signal_funnel,
        recent_trades=recent_trades,
    )
