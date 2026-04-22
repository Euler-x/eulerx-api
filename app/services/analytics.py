import uuid
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ExecutionStatus
from app.models.execution import Execution
from app.utils.helpers import utc_now


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
        return {
            "total_trades": len(executions),
            "win_rate": cls.win_rate(executions),
            "profit_factor": cls.profit_factor(executions),
            "sharpe_ratio": cls.sharpe_ratio(executions),
            "max_drawdown": cls.max_drawdown(executions),
            "total_pnl": cls.total_pnl(executions),
            "avg_trade_pnl": cls.avg_trade_pnl(executions),
            "best_trade": cls.best_trade(executions),
            "worst_trade": cls.worst_trade(executions),
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
