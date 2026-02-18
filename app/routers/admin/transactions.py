"""Admin transactions router — read-only cross-user access to all transactions."""

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.models.enums import TransactionCategory, TransactionStatus
from app.models.schemas.common import PaginatedResponse
from app.models.schemas.transaction import TransactionResponse
from app.models.transaction import Transaction

router = APIRouter()


@router.get("/transactions", response_model=PaginatedResponse)
async def admin_list_transactions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_id: Optional[uuid.UUID] = None,
    category: Optional[TransactionCategory] = None,
    status: Optional[TransactionStatus] = None,
    asset: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(Transaction).order_by(Transaction.created_at.desc())
    count_query = select(func.count(Transaction.id))

    if user_id:
        query = query.where(Transaction.user_id == user_id)
        count_query = count_query.where(Transaction.user_id == user_id)
    if category:
        query = query.where(Transaction.category == category)
        count_query = count_query.where(Transaction.category == category)
    if status:
        query = query.where(Transaction.status == status)
        count_query = count_query.where(Transaction.status == status)
    if asset:
        query = query.where(Transaction.asset == asset)
        count_query = count_query.where(Transaction.asset == asset)
    if start_date:
        query = query.where(Transaction.created_at >= start_date)
        count_query = count_query.where(Transaction.created_at >= start_date)
    if end_date:
        query = query.where(Transaction.created_at <= end_date)
        count_query = count_query.where(Transaction.created_at <= end_date)

    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    transactions = result.scalars().all()

    return PaginatedResponse(
        items=[TransactionResponse.model_validate(t) for t in transactions],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


@router.get("/transactions/{transaction_id}", response_model=TransactionResponse)
async def admin_get_transaction(
    transaction_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Transaction).where(Transaction.id == transaction_id)
    )
    transaction = result.scalar_one_or_none()
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return TransactionResponse.model_validate(transaction)
