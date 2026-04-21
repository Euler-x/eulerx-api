import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.models.enums import Exchange, ExecutionStatus, OrderType, SignalDirection


class ExecutionResponse(BaseModel):
    id: uuid.UUID
    signal_id: Optional[uuid.UUID] = None
    bybit_signal_id: Optional[uuid.UUID] = None
    user_id: uuid.UUID
    strategy_id: uuid.UUID
    order_type: OrderType
    direction: SignalDirection
    entry_price: float
    exit_price: Optional[float]
    quantity: float
    leverage: float
    pnl: Optional[float]
    tx_hash: Optional[str]
    exchange_order_id: Optional[str] = None
    exchange: Exchange = Exchange.HYPERLIQUID
    user_email: Optional[str] = None
    error_message: Optional[str] = None
    status: ExecutionStatus
    executed_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class ExecutionVerifyResponse(BaseModel):
    execution_id: uuid.UUID
    tx_hash: Optional[str]
    verified: bool
    verification_link: Optional[str]


class CloseExecutionResponse(BaseModel):
    execution: ExecutionResponse
    message: str
    already_closed_on_exchange: bool
