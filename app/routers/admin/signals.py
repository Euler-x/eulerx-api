"""Admin signals router — cross-user signal listing and cancellation."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.base import get_db
from app.middleware.audit import log_audit
from app.middleware.permissions import RequireAdmin, UserPermissions
from app.models.enums import SignalDirection, SignalStatus
from app.models.schemas.common import PaginatedResponse
from app.models.schemas.signal import SignalDetailResponse, SignalResponse
from app.models.signal import Signal

router = APIRouter()

TERMINAL_STATUSES = {SignalStatus.FILLED, SignalStatus.EXPIRED, SignalStatus.CANCELLED}


@router.get("/signals", response_model=PaginatedResponse)
async def admin_list_signals(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    symbol: Optional[str] = None,
    direction: Optional[SignalDirection] = None,
    signal_status: Optional[SignalStatus] = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(Signal).order_by(Signal.created_at.desc())
    count_query = select(func.count(Signal.id))

    if symbol:
        query = query.where(Signal.symbol == symbol)
        count_query = count_query.where(Signal.symbol == symbol)
    if direction:
        query = query.where(Signal.direction == direction)
        count_query = count_query.where(Signal.direction == direction)
    if signal_status:
        query = query.where(Signal.status == signal_status)
        count_query = count_query.where(Signal.status == signal_status)

    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    signals = result.scalars().all()

    return PaginatedResponse(
        items=[SignalResponse.model_validate(s) for s in signals],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


@router.get("/signals/{signal_id}", response_model=SignalDetailResponse)
async def admin_get_signal(
    signal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Signal)
        .options(selectinload(Signal.executions))
        .where(Signal.id == signal_id)
    )
    signal = result.scalar_one_or_none()
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    return SignalDetailResponse.model_validate(signal)


@router.put("/signals/{signal_id}/cancel", response_model=SignalResponse)
async def admin_cancel_signal(
    signal_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    result = await db.execute(select(Signal).where(Signal.id == signal_id))
    signal = result.scalar_one_or_none()
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")

    if signal.status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Signal is already in terminal state: {signal.status.value}",
        )

    signal.status = SignalStatus.CANCELLED
    await db.flush()

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_cancel_signal",
        resource_type="signal",
        resource_id=str(signal_id),
        ip_address=request.client.host if request.client else None,
    )

    return SignalResponse.model_validate(signal)
