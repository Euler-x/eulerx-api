import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.base import get_db
from app.middleware.permissions import RequireAuth, UserPermissions
from app.models.enums import TicketStatus
from app.models.schemas.common import PaginatedResponse
from app.models.schemas.support import (
    MessageCreate,
    SupportMessageResponse,
    SupportTicketResponse,
    TicketCreate,
    TicketDetailResponse,
)
from app.models.support import SupportMessage, SupportTicket

router = APIRouter(prefix="/support", tags=["Support"])


@router.post("/tickets", response_model=SupportTicketResponse, status_code=201)
async def create_ticket(
    data: TicketCreate,
    perms: UserPermissions = RequireAuth,
    db: AsyncSession = Depends(get_db),
):
    ticket = SupportTicket(
        user_id=perms.id,
        subject=data.subject,
        description=data.description,
        priority=data.priority,
    )
    db.add(ticket)
    await db.flush()
    return SupportTicketResponse.model_validate(ticket)


@router.get("/tickets", response_model=PaginatedResponse)
async def list_tickets(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: TicketStatus | None = None,
    perms: UserPermissions = RequireAuth,
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(SupportTicket)
        .where(SupportTicket.user_id == perms.id)
        .order_by(SupportTicket.created_at.desc())
    )
    count_query = select(func.count(SupportTicket.id)).where(
        SupportTicket.user_id == perms.id
    )

    if status:
        query = query.where(SupportTicket.status == status)
        count_query = count_query.where(SupportTicket.status == status)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    tickets = result.scalars().all()

    return PaginatedResponse(
        items=[SupportTicketResponse.model_validate(t) for t in tickets],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


@router.get("/tickets/{ticket_id}", response_model=TicketDetailResponse)
async def get_ticket(
    ticket_id: uuid.UUID,
    perms: UserPermissions = RequireAuth,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SupportTicket)
        .options(selectinload(SupportTicket.messages))
        .where(
            SupportTicket.id == ticket_id,
            SupportTicket.user_id == perms.id,
        )
    )
    ticket = result.scalar_one_or_none()
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return TicketDetailResponse.model_validate(ticket)


@router.post(
    "/tickets/{ticket_id}/messages",
    response_model=SupportMessageResponse,
    status_code=201,
)
async def add_message(
    ticket_id: uuid.UUID,
    data: MessageCreate,
    perms: UserPermissions = RequireAuth,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SupportTicket).where(
            SupportTicket.id == ticket_id,
            SupportTicket.user_id == perms.id,
        )
    )
    ticket = result.scalar_one_or_none()
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    if ticket.status == TicketStatus.CLOSED:
        raise HTTPException(
            status_code=400, detail="Cannot add messages to a closed ticket"
        )

    message = SupportMessage(
        ticket_id=ticket.id,
        user_id=perms.id,
        message=data.message,
        is_admin=perms.is_admin,
    )
    db.add(message)
    await db.flush()
    return SupportMessageResponse.model_validate(message)
