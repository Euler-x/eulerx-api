"""Admin payments router -- paginated payment listing and detail."""

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.models.billing import Payment, Plan, Subscription
from app.models.user import User

router = APIRouter()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class PaymentItem(BaseModel):
    id: uuid.UUID
    subscription_id: uuid.UUID
    user_id: uuid.UUID
    user_email: Optional[str] = None
    plan_name: Optional[str] = None
    amount_usd: float
    amount_crypto: Optional[float] = None
    crypto_currency: Optional[str] = None
    nowpayments_payment_id: Optional[str] = None
    nowpayments_invoice_id: Optional[str] = None
    status: str
    paid_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class PaginatedPaymentsResponse(BaseModel):
    items: list[PaymentItem]
    total: int
    page: int
    page_size: int
    total_pages: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/payments", response_model=PaginatedPaymentsResponse)
async def list_payments(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    user_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Return a paginated list of all payments with user and plan context."""

    # Base filters
    filters = []
    if status is not None:
        filters.append(Payment.status == status)
    if user_id is not None:
        filters.append(Payment.user_id == user_id)

    # Total count
    count_query = select(func.count(Payment.id))
    if filters:
        count_query = count_query.where(*filters)
    total = (await db.execute(count_query)).scalar() or 0

    total_pages = max(1, (total + page_size - 1) // page_size)

    # Paginated data query
    offset = (page - 1) * page_size
    data_query = (
        select(Payment, User.email, Plan.name)
        .join(Subscription, Payment.subscription_id == Subscription.id)
        .join(Plan, Subscription.plan_id == Plan.id)
        .outerjoin(User, Payment.user_id == User.id)
        .where(*filters)
        .order_by(Payment.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )

    rows = (await db.execute(data_query)).all()

    items = []
    for payment, user_email, plan_name in rows:
        items.append(
            PaymentItem(
                id=payment.id,
                subscription_id=payment.subscription_id,
                user_id=payment.user_id,
                user_email=user_email,
                plan_name=plan_name,
                amount_usd=float(payment.amount_usd),
                amount_crypto=float(payment.amount_crypto)
                if payment.amount_crypto is not None
                else None,
                crypto_currency=payment.crypto_currency,
                nowpayments_payment_id=payment.nowpayments_payment_id,
                nowpayments_invoice_id=payment.nowpayments_invoice_id,
                status=payment.status.value
                if hasattr(payment.status, "value")
                else str(payment.status),
                paid_at=payment.paid_at,
                created_at=payment.created_at,
            )
        )

    return PaginatedPaymentsResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/payments/{payment_id}", response_model=PaymentItem)
async def get_payment(
    payment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Return a single payment with user and plan context."""
    query = (
        select(Payment, User.email, Plan.name)
        .join(Subscription, Payment.subscription_id == Subscription.id)
        .join(Plan, Subscription.plan_id == Plan.id)
        .outerjoin(User, Payment.user_id == User.id)
        .where(Payment.id == payment_id)
    )

    row = (await db.execute(query)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Payment not found")

    payment, user_email, plan_name = row

    return PaymentItem(
        id=payment.id,
        subscription_id=payment.subscription_id,
        user_id=payment.user_id,
        user_email=user_email,
        plan_name=plan_name,
        amount_usd=float(payment.amount_usd),
        amount_crypto=float(payment.amount_crypto)
        if payment.amount_crypto is not None
        else None,
        crypto_currency=payment.crypto_currency,
        nowpayments_payment_id=payment.nowpayments_payment_id,
        nowpayments_invoice_id=payment.nowpayments_invoice_id,
        status=payment.status.value
        if hasattr(payment.status, "value")
        else str(payment.status),
        paid_at=payment.paid_at,
        created_at=payment.created_at,
    )
