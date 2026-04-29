import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ExecutionStatus
from app.models.execution import Execution
from app.models.portfolio_snapshot import PortfolioSnapshot
from app.utils.helpers import utc_now

logger = logging.getLogger(__name__)


class AnalyticsService:
    """Compute quant analytics from execution history."""

    @staticmethod
    async def get_closed_executions(
        db: AsyncSession,
        user_id: uuid.UUID,
        strategy_id: uuid.UUID | None = None,
        days: int = 30,
        exchange: str | None = None,
    ) -> list[Execution]:
        query = (
            select(Execution)
            .where(
                Execution.user_id == user_id,
                Execution.status == ExecutionStatus.CLOSED,
                Execution.pnl != None,  # noqa: E711
                Execution.created_at >= utc_now() - timedelta(days=days),
            )
            .order_by(Execution.executed_at.asc(), Execution.created_at.asc())
        )
        if strategy_id:
            query = query.where(Execution.strategy_id == strategy_id)
        if exchange:
            query = query.where(Execution.exchange == exchange)

        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    def win_rate(executions: list[Execution]) -> float:
        gross_profit = sum(float(e.pnl) for e in executions if float(e.pnl) > 0)
        gross_loss = abs(sum(float(e.pnl) for e in executions if float(e.pnl) < 0))
        total = gross_profit + gross_loss
        return round(gross_profit / total * 100, 2) if total > 0 else 0.0

    @staticmethod
    def profit_factor(executions: list[Execution]) -> float:
        gross_profit = sum(float(e.pnl) for e in executions if float(e.pnl) > 0)
        gross_loss = abs(sum(float(e.pnl) for e in executions if float(e.pnl) < 0))
        if gross_loss == 0:
            return round(gross_profit, 2) if gross_profit > 0 else 0.0
        return round(gross_profit / gross_loss, 2)

    @staticmethod
    def sharpe_ratio(executions: list[Execution]) -> float:
        if len(executions) < 2:
            return 0.0

        daily_returns: dict[str, float] = defaultdict(float)
        for e in executions:
            day = (e.executed_at or e.created_at).strftime("%Y-%m-%d")
            daily_returns[day] += float(e.pnl)

        returns = list(daily_returns.values())
        if len(returns) < 2:
            return 0.0

        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1)
        stdev = variance**0.5

        if stdev == 0:
            return 0.0

        risk_free_daily = 0.05 / 365
        annualized = (mean_return - risk_free_daily) / stdev * (365**0.5)
        return round(annualized, 2)

    @staticmethod
    def max_drawdown(executions: list[Execution]) -> float:
        if not executions:
            return 0.0

        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0

        for e in executions:
            cumulative += float(e.pnl)
            if cumulative > peak:
                peak = cumulative
            drawdown = peak - cumulative
            if drawdown > max_dd:
                max_dd = drawdown

        if peak <= 0:
            return 0.0
        return round(max_dd / peak * 100, 2)

    @staticmethod
    def equity_curve(executions: list[Execution]) -> list[dict]:
        curve = []
        cumulative = 0.0
        for e in executions:
            pnl = float(e.pnl)
            cumulative += pnl
            curve.append(
                {
                    "timestamp": (e.executed_at or e.created_at).isoformat(),
                    "cumulative_pnl": round(cumulative, 2),
                    "trade_pnl": round(pnl, 2),
                }
            )
        return curve

    @staticmethod
    def total_pnl(executions: list[Execution]) -> float:
        return round(sum(float(e.pnl) for e in executions), 2)

    @staticmethod
    def avg_trade_pnl(executions: list[Execution]) -> float:
        if not executions:
            return 0.0
        return round(sum(float(e.pnl) for e in executions) / len(executions), 2)

    @staticmethod
    def best_trade(executions: list[Execution]) -> float:
        if not executions:
            return 0.0
        return round(max(float(e.pnl) for e in executions), 2)

    @staticmethod
    def worst_trade(executions: list[Execution]) -> float:
        if not executions:
            return 0.0
        return round(min(float(e.pnl) for e in executions), 2)

    @staticmethod
    def trade_volume(executions: list[Execution]) -> float:
        """Sum of (quantity × entry_price) for all closed executions."""
        total = 0.0
        for e in executions:
            qty = float(e.quantity) if e.quantity else 0.0
            entry = float(e.entry_price) if e.entry_price else 0.0
            total += qty * entry
        return round(total, 2)

    # ── Portfolio Return Helpers ─────────────────────────────────────

    @staticmethod
    async def _get_snapshot_balance(
        db: AsyncSession,
        user_id: uuid.UUID,
        at_or_before: datetime,
    ) -> float | None:
        """Get the closest portfolio snapshot balance at or before a given time."""

        result = await db.execute(
            select(PortfolioSnapshot.total_balance)
            .where(
                PortfolioSnapshot.user_id == user_id,
                PortfolioSnapshot.captured_at <= at_or_before,
            )
            .order_by(PortfolioSnapshot.captured_at.desc())
            .limit(1)
        )
        val = result.scalar_one_or_none()
        return float(val) if val is not None else None

    @staticmethod
    async def _get_latest_snapshot_balance(
        db: AsyncSession,
        user_id: uuid.UUID,
    ) -> float | None:
        """Get the most recent portfolio snapshot balance."""
        result = await db.execute(
            select(PortfolioSnapshot.total_balance)
            .where(PortfolioSnapshot.user_id == user_id)
            .order_by(PortfolioSnapshot.captured_at.desc())
            .limit(1)
        )
        val = result.scalar_one_or_none()
        return float(val) if val is not None else None

    @classmethod
    async def _compute_return_pct(
        cls,
        db: AsyncSession,
        user_id: uuid.UUID,
        days: int,
    ) -> float:
        """Compute portfolio return % over a given number of days."""
        now = utc_now()
        start_time = now - timedelta(days=days)

        start_balance = await cls._get_snapshot_balance(db, user_id, start_time)
        end_balance = await cls._get_latest_snapshot_balance(db, user_id)

        if start_balance is None or end_balance is None or start_balance <= 0:
            return 0.0

        return round(((end_balance - start_balance) / start_balance) * 100, 2)

    @classmethod
    async def compute(
        cls,
        db: AsyncSession,
        user_id: uuid.UUID,
        strategy_id: uuid.UUID | None = None,
        days: int = 30,
        exchange: str | None = None,
    ) -> dict:
        executions = await cls.get_closed_executions(
            db, user_id, strategy_id, days, exchange=exchange
        )

        # Portfolio return data from snapshots
        now = utc_now()
        period_start = now - timedelta(days=days)

        starting_balance = await cls._get_snapshot_balance(db, user_id, period_start)
        ending_balance = await cls._get_latest_snapshot_balance(db, user_id)
        has_portfolio_history = (
            starting_balance is not None and ending_balance is not None
        )

        # Compute period-specific returns
        period_return_pct = 0.0
        day_return_pct = 0.0
        week_return_pct = 0.0
        month_return_pct = 0.0

        if has_portfolio_history:
            period_return_pct = await cls._compute_return_pct(db, user_id, days)
            day_return_pct = await cls._compute_return_pct(db, user_id, 1)
            week_return_pct = await cls._compute_return_pct(db, user_id, 7)
            month_return_pct = await cls._compute_return_pct(db, user_id, 30)

        return {
            "total_trades": len(executions),
            "win_rate": cls.win_rate(executions),
            "profit_factor": cls.profit_factor(executions),
            "sharpe_ratio": cls.sharpe_ratio(executions),
            "max_drawdown": cls.max_drawdown(executions),
            "total_pnl": cls.total_pnl(executions),
            "trade_volume": cls.trade_volume(executions),
            "avg_trade_pnl": cls.avg_trade_pnl(executions),
            "best_trade": cls.best_trade(executions),
            "worst_trade": cls.worst_trade(executions),
            "portfolio_balance": ending_balance or 0.0,
            "starting_balance": starting_balance or 0.0,
            "ending_balance": ending_balance or 0.0,
            "period_return_pct": period_return_pct,
            "day_return_pct": day_return_pct,
            "week_return_pct": week_return_pct,
            "month_return_pct": month_return_pct,
            "has_portfolio_history": has_portfolio_history,
            "period_days": days,
        }

    @classmethod
    async def compute_equity_curve(
        cls,
        db: AsyncSession,
        user_id: uuid.UUID,
        strategy_id: uuid.UUID | None = None,
        days: int = 30,
        exchange: str | None = None,
    ) -> list[dict]:
        executions = await cls.get_closed_executions(
            db, user_id, strategy_id, days, exchange=exchange
        )
        return cls.equity_curve(executions)

    # ── Public System Performance (landing page) ─────────────────────

    @classmethod
    async def compute_system_performance(
        cls,
        db: AsyncSession,
        days: int = 30,
    ) -> dict:
        """Compute aggregated system-wide performance across all users.

        Returns data for the public landing page: total volume, PnL,
        return %, winning/losing days, and daily breakdown.
        This endpoint is rate-limited and exposes only aggregate data.
        """
        cutoff = utc_now() - timedelta(days=days)

        result = await db.execute(
            select(Execution)
            .where(
                Execution.status == ExecutionStatus.CLOSED,
                Execution.pnl != None,  # noqa: E711
                Execution.created_at >= cutoff,
            )
            .order_by(Execution.executed_at.asc(), Execution.created_at.asc())
        )
        executions = list(result.scalars().all())

        if not executions:
            return {
                "period_days": days,
                "total_trade_volume": 0.0,
                "total_pnl": 0.0,
                "total_return_pct": 0.0,
                "winning_days": 0,
                "losing_days": 0,
                "daily_performance": [],
            }

        # Group by date
        daily_data: dict[str, dict] = defaultdict(
            lambda: {"volume": 0.0, "pnl": 0.0, "trades": 0}
        )
        total_volume = 0.0

        for e in executions:
            day = (e.executed_at or e.created_at).strftime("%Y-%m-%d")
            pnl = float(e.pnl)
            qty = float(e.quantity) if e.quantity else 0.0
            entry = float(e.entry_price) if e.entry_price else 0.0
            vol = qty * entry
            daily_data[day]["volume"] += vol
            daily_data[day]["pnl"] += pnl
            daily_data[day]["trades"] += 1
            total_volume += vol

        total_pnl = sum(d["pnl"] for d in daily_data.values())
        total_return_pct = (
            round((total_pnl / total_volume) * 100, 2) if total_volume > 0 else 0.0
        )

        # Build daily performance list
        sorted_days = sorted(daily_data.keys())
        cumulative_pnl = 0.0
        cumulative_volume = 0.0
        winning_days = 0
        losing_days = 0
        daily_performance = []

        for day in sorted_days:
            d = daily_data[day]
            cumulative_pnl += d["pnl"]
            cumulative_volume += d["volume"]
            pnl_pct = (
                round((d["pnl"] / d["volume"]) * 100, 2) if d["volume"] > 0 else 0.0
            )
            cumulative_pnl_pct = (
                round((cumulative_pnl / cumulative_volume) * 100, 2)
                if cumulative_volume > 0
                else 0.0
            )

            if d["pnl"] > 0:
                winning_days += 1
            elif d["pnl"] < 0:
                losing_days += 1

            daily_performance.append(
                {
                    "date": day,
                    "trade_volume": round(d["volume"], 2),
                    "pnl": round(d["pnl"], 2),
                    "pnl_pct": pnl_pct,
                    "cumulative_pnl": round(cumulative_pnl, 2),
                    "cumulative_pnl_pct": cumulative_pnl_pct,
                    "trades_count": d["trades"],
                }
            )

        return {
            "period_days": days,
            "total_trade_volume": round(total_volume, 2),
            "total_pnl": round(total_pnl, 2),
            "total_return_pct": total_return_pct,
            "winning_days": winning_days,
            "losing_days": losing_days,
            "daily_performance": daily_performance,
        }
