import uuid
from typing import Optional

from pydantic import BaseModel


class AnalyticsOverviewResponse(BaseModel):
    total_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    total_pnl: float = 0.0
    avg_trade_pnl: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    period_days: int = 30


class StrategyAnalyticsResponse(AnalyticsOverviewResponse):
    strategy_id: uuid.UUID
    strategy_name: str


class EquityCurvePoint(BaseModel):
    timestamp: str
    cumulative_pnl: float
    trade_pnl: float
