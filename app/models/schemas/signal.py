import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.models.enums import SignalDirection, SignalStatus
from app.models.schemas.execution import ExecutionResponse


class SignalResponse(BaseModel):
    id: uuid.UUID
    strategy_id: Optional[uuid.UUID]
    symbol: str
    direction: SignalDirection
    confidence: float
    entry_price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    risk_reward_ratio: Optional[float]
    indicators: Optional[dict]
    status: SignalStatus
    expires_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class SignalDetailResponse(SignalResponse):
    model_responses: Optional[dict] = None
    executions: list[ExecutionResponse] = []
