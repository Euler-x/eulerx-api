import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.models.enums import SignalDirection, SignalStatus
from app.models.schemas.execution import ExecutionResponse


class SignalResponse(BaseModel):
    id: uuid.UUID
    strategy_id: Optional[uuid.UUID] = None
    symbol: str
    direction: SignalDirection
    confidence: float
    entry_price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    risk_reward_ratio: Optional[float]
    indicators: Optional[dict]
    status: SignalStatus
    exchange: str = "hyperliquid"
    expires_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class SignalDetailResponse(SignalResponse):
    model_responses: Optional[dict] = None
    executions: list[ExecutionResponse] = []


class BybitSignalResponse(BaseModel):
    id: uuid.UUID
    symbol: str
    direction: SignalDirection
    confidence: float
    entry_price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    risk_reward_ratio: Optional[float]
    indicators: Optional[dict]
    status: SignalStatus
    exchange: str = "bybit"
    expires_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class BybitSignalDetailResponse(BybitSignalResponse):
    model_responses: Optional[dict] = None
    executions: list[ExecutionResponse] = []
