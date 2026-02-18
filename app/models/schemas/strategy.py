import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.enums import RiskProfile, StrategyType


class StrategyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    strategy_type: StrategyType
    risk_profile: RiskProfile
    leverage_limit: float = Field(default=1.0, ge=1.0, le=100.0)
    max_positions: int = Field(default=5, ge=1, le=50)
    capital_allocation: float = Field(..., gt=0)
    max_drawdown_percent: float = Field(default=10.0, ge=1.0, le=100.0)


class StrategyUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    risk_profile: Optional[RiskProfile] = None
    leverage_limit: Optional[float] = Field(None, ge=1.0, le=100.0)
    max_positions: Optional[int] = Field(None, ge=1, le=50)
    capital_allocation: Optional[float] = Field(None, gt=0)
    max_drawdown_percent: Optional[float] = Field(None, ge=1.0, le=100.0)


class StrategyResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    strategy_type: StrategyType
    risk_profile: RiskProfile
    leverage_limit: float
    max_positions: int
    capital_allocation: float
    max_drawdown_percent: float
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
