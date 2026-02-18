from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.middleware.auth import require_verified_email
from app.models.enums import TransactionCategory, TransactionStatus
from app.models.schemas.common import PaginatedResponse
from app.models.schemas.transaction import TransactionResponse
from app.models.transaction import Transaction
from app.models.user import User

router = APIRouter(prefix="/transactions", tags=["Transactions"])


@router.get("", response_model=PaginatedResponse)
async def list_transactions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category: Optional[TransactionCategory] = None,
    status: Optional[TransactionStatus] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    current_user: User = Depends(require_verified_email),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(Transaction)
        .where(Transaction.user_id == current_user.id)
        .order_by(Transaction.created_at.desc())
    )
    count_query = select(func.count(Transaction.id)).where(
        Transaction.user_id == current_user.id
    )

    if category:
        query = query.where(Transaction.category == category)
        count_query = count_query.where(Transaction.category == category)
    if status:
        query = query.where(Transaction.status == status)
        count_query = count_query.where(Transaction.status == status)
    if start_date:
        query = query.where(Transaction.created_at >= start_date)
        count_query = count_query.where(Transaction.created_at >= start_date)
    if end_date:
        query = query.where(Transaction.created_at <= end_date)
        count_query = count_query.where(Transaction.created_at <= end_date)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

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
