import uuid

from pydantic import BaseModel


class AnalyticsOverviewResponse(BaseModel):
    total_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    total_pnl: float = 0.0
    trade_volume: float = 0.0
    avg_trade_pnl: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    portfolio_balance: float = 0.0
    starting_balance: float = 0.0
    ending_balance: float = 0.0
    period_return_pct: float = 0.0
    day_return_pct: float = 0.0
    week_return_pct: float = 0.0
    month_return_pct: float = 0.0
    has_portfolio_history: bool = False
    period_days: int = 30


class StrategyAnalyticsResponse(AnalyticsOverviewResponse):
    strategy_id: uuid.UUID
    strategy_name: str


class EquityCurvePoint(BaseModel):
    timestamp: str
    cumulative_pnl: float
    trade_pnl: float


class DailyPerformancePoint(BaseModel):
    date: str
    trade_volume: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    cumulative_pnl: float = 0.0
    cumulative_pnl_pct: float = 0.0
    trades_count: int = 0


class PublicSystemPerformanceResponse(BaseModel):
    period_days: int = 30
    total_trade_volume: float = 0.0
    total_pnl: float = 0.0
    total_return_pct: float = 0.0
    winning_days: int = 0
    losing_days: int = 0
    daily_performance: list[DailyPerformancePoint] = []
