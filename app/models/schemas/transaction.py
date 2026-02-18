import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.models.enums import TransactionCategory, TransactionStatus


class TransactionResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    category: TransactionCategory
    amount: float
    asset: str
    status: TransactionStatus
    verification_link: Optional[str]
    tx_hash: Optional[str]
    description: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}
