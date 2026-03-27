import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.enums import RiskProfile, StrategyTimeframe, StrategyType


class StrategyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    strategy_type: StrategyType
    risk_profile: RiskProfile
    leverage_limit: float = Field(default=1.0, ge=1.0, le=100.0)
    max_positions: int = Field(default=5, ge=1, le=50)
    allocation_pct: float = Field(default=100.0, ge=1.0, le=100.0)
    max_drawdown_percent: float = Field(default=10.0, ge=1.0, le=100.0)
    daily_loss_cap_percent: Optional[float] = Field(None, ge=0.1, le=100.0)
    target_volatility: Optional[float] = Field(None, ge=0)
    expected_volatility: Optional[float] = Field(None, ge=0)
    timeframe: Optional[StrategyTimeframe] = None
    target_return_min: Optional[float] = Field(None, ge=0)
    target_return_max: Optional[float] = Field(None, ge=0)
    target_exchange: str = Field(default="both", pattern=r"^(hyperliquid|bybit|both)$")


class StrategyUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    risk_profile: Optional[RiskProfile] = None
    leverage_limit: Optional[float] = Field(None, ge=1.0, le=100.0)
    max_positions: Optional[int] = Field(None, ge=1, le=50)
    allocation_pct: Optional[float] = Field(None, gt=0)
    max_drawdown_percent: Optional[float] = Field(None, ge=1.0, le=100.0)
    daily_loss_cap_percent: Optional[float] = Field(None, ge=0.1, le=100.0)
    target_volatility: Optional[float] = Field(None, ge=0)
    expected_volatility: Optional[float] = Field(None, ge=0)
    timeframe: Optional[StrategyTimeframe] = None
    target_return_min: Optional[float] = Field(None, ge=0)
    target_return_max: Optional[float] = Field(None, ge=0)
    target_exchange: Optional[str] = Field(None, pattern=r"^(hyperliquid|bybit|both)$")


class StrategyResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    strategy_type: StrategyType
    risk_profile: RiskProfile
    leverage_limit: float
    max_positions: int
    allocation_pct: float
    max_drawdown_percent: float
    is_active: bool
    target_exchange: str = "both"
    daily_loss_cap_percent: Optional[float] = None
    target_volatility: Optional[float] = None
    expected_volatility: Optional[float] = None
    timeframe: Optional[StrategyTimeframe] = None
    target_return_min: Optional[float] = None
    target_return_max: Optional[float] = None
    paused_reason: Optional[str] = None
    paused_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
