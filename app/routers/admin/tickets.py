import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.base import get_db
from app.middleware.auth import get_admin_user
from app.models.enums import TicketStatus
from app.models.support import SupportMessage, SupportTicket
from app.models.user import User
from app.services.notifications import NotificationService
from app.models.schemas.common import PaginatedResponse
from app.models.schemas.support import (
    MessageCreate,
    SupportMessageResponse,
    SupportTicketResponse,
    TicketDetailResponse,
    TicketUpdateStatus,
)

router = APIRouter()


@router.get("/tickets", response_model=PaginatedResponse)
async def admin_list_tickets(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: TicketStatus | None = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(SupportTicket).order_by(SupportTicket.created_at.desc())
    count_query = select(func.count(SupportTicket.id))

    if status_filter:
        query = query.where(SupportTicket.status == status_filter)
        count_query = count_query.where(SupportTicket.status == status_filter)

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
async def admin_get_ticket(ticket_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SupportTicket)
        .options(selectinload(SupportTicket.messages))
        .where(SupportTicket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return TicketDetailResponse.model_validate(ticket)


@router.put("/tickets/{ticket_id}/status", response_model=SupportTicketResponse)
async def admin_update_ticket_status(
    ticket_id: uuid.UUID,
    data: TicketUpdateStatus,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SupportTicket).where(SupportTicket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    ticket.status = data.status
    await db.flush()
    return SupportTicketResponse.model_validate(ticket)


@router.post(
    "/tickets/{ticket_id}/reply",
    response_model=SupportMessageResponse,
    status_code=201,
)
async def admin_reply_to_ticket(
    ticket_id: uuid.UUID,
    data: MessageCreate,
    admin_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SupportTicket).where(SupportTicket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    message = SupportMessage(
        ticket_id=ticket.id,
        user_id=admin_user.id,
        message=data.message,
        is_admin=True,
    )
    db.add(message)

    if ticket.status == TicketStatus.OPEN:
        ticket.status = TicketStatus.IN_PROGRESS

    await db.flush()

    # Notify ticket owner of admin reply
    ticket_user_result = await db.execute(select(User).where(User.id == ticket.user_id))
    ticket_user = ticket_user_result.scalar_one_or_none()
    if ticket_user:
        notification_service = NotificationService()
        await notification_service.send_support_ticket_update(
            user=ticket_user,
            ticket_subject=ticket.subject,
            new_status=ticket.status.value,
            admin_reply=data.message,
        )

    return SupportMessageResponse.model_validate(message)
