import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import TicketPriority, TicketStatus


class TicketCreate(BaseModel):
    subject: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1)
    priority: TicketPriority = TicketPriority.MEDIUM


class TicketUpdateStatus(BaseModel):
    status: TicketStatus


class MessageCreate(BaseModel):
    message: str = Field(..., min_length=1)


class SupportTicketResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    subject: str
    description: str
    status: TicketStatus
    priority: TicketPriority
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SupportMessageResponse(BaseModel):
    id: uuid.UUID
    ticket_id: uuid.UUID
    user_id: uuid.UUID
    message: str
    is_admin: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class TicketDetailResponse(SupportTicketResponse):
    messages: list[SupportMessageResponse] = []
