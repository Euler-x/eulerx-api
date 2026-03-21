import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.base import get_db
from app.middleware.permissions import RequireSubscribed, UserPermissions
from app.models.bybit_signal import BybitSignal
from app.models.enums import SignalDirection, SignalStatus
from app.models.schemas.common import PaginatedResponse
from app.models.schemas.signal import (
    BybitSignalDetailResponse,
    BybitSignalResponse,
    SignalDetailResponse,
    SignalResponse,
)
from app.models.signal import Signal

router = APIRouter(tags=["Signals"])


# ── HyperLiquid Signals (/signals) ────────────────────────────────


@router.get("/signals", response_model=PaginatedResponse)
async def list_signals(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    symbol: Optional[str] = None,
    direction: Optional[SignalDirection] = None,
    status: Optional[SignalStatus] = None,
    perms: UserPermissions = RequireSubscribed,
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
    if status:
        query = query.where(Signal.status == status)
        count_query = count_query.where(Signal.status == status)

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


@router.get("/signals/live", response_model=list[SignalResponse])
async def get_live_signals(
    perms: UserPermissions = RequireSubscribed,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Signal)
        .where(Signal.status.in_([SignalStatus.NEW, SignalStatus.EXECUTING]))
        .order_by(Signal.confidence.desc())
    )
    signals = result.scalars().all()
    return [SignalResponse.model_validate(s) for s in signals]


@router.get("/signals/history", response_model=PaginatedResponse)
async def get_signal_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    perms: UserPermissions = RequireSubscribed,
    db: AsyncSession = Depends(get_db),
):
    status_filter = [SignalStatus.FILLED, SignalStatus.EXPIRED, SignalStatus.CANCELLED]
    query = (
        select(Signal)
        .where(Signal.status.in_(status_filter))
        .order_by(Signal.created_at.desc())
    )
    count_query = select(func.count(Signal.id)).where(Signal.status.in_(status_filter))

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
async def get_signal_detail(
    signal_id: uuid.UUID,
    perms: UserPermissions = RequireSubscribed,
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


# ── Bybit Signals (/bybit-signals) ────────────────────────────────


@router.get("/bybit-signals", response_model=PaginatedResponse)
async def list_bybit_signals(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    symbol: Optional[str] = None,
    direction: Optional[SignalDirection] = None,
    status: Optional[SignalStatus] = None,
    perms: UserPermissions = RequireSubscribed,
    db: AsyncSession = Depends(get_db),
):
    query = select(BybitSignal).order_by(BybitSignal.created_at.desc())
    count_query = select(func.count(BybitSignal.id))

    if symbol:
        query = query.where(BybitSignal.symbol == symbol)
        count_query = count_query.where(BybitSignal.symbol == symbol)
    if direction:
        query = query.where(BybitSignal.direction == direction)
        count_query = count_query.where(BybitSignal.direction == direction)
    if status:
        query = query.where(BybitSignal.status == status)
        count_query = count_query.where(BybitSignal.status == status)

    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    signals = result.scalars().all()

    return PaginatedResponse(
        items=[BybitSignalResponse.model_validate(s) for s in signals],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


@router.get("/bybit-signals/live", response_model=list[BybitSignalResponse])
async def get_live_bybit_signals(
    perms: UserPermissions = RequireSubscribed,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BybitSignal)
        .where(BybitSignal.status.in_([SignalStatus.NEW, SignalStatus.EXECUTING]))
        .order_by(BybitSignal.confidence.desc())
    )
    signals = result.scalars().all()
    return [BybitSignalResponse.model_validate(s) for s in signals]


@router.get("/bybit-signals/history", response_model=PaginatedResponse)
async def get_bybit_signal_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    perms: UserPermissions = RequireSubscribed,
    db: AsyncSession = Depends(get_db),
):
    status_filter = [SignalStatus.FILLED, SignalStatus.EXPIRED, SignalStatus.CANCELLED]
    query = (
        select(BybitSignal)
        .where(BybitSignal.status.in_(status_filter))
        .order_by(BybitSignal.created_at.desc())
    )
    count_query = select(func.count(BybitSignal.id)).where(
        BybitSignal.status.in_(status_filter)
    )

    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    signals = result.scalars().all()

    return PaginatedResponse(
        items=[BybitSignalResponse.model_validate(s) for s in signals],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


@router.get("/bybit-signals/{signal_id}", response_model=BybitSignalDetailResponse)
async def get_bybit_signal_detail(
    signal_id: uuid.UUID,
    perms: UserPermissions = RequireSubscribed,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BybitSignal)
        .options(selectinload(BybitSignal.executions))
        .where(BybitSignal.id == signal_id)
    )
    signal = result.scalar_one_or_none()
    if signal is None:
        raise HTTPException(status_code=404, detail="Bybit signal not found")
    return BybitSignalDetailResponse.model_validate(signal)
