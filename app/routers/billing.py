from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.middleware.auth import require_verified_email
from app.models.billing import Payment, Plan, Subscription
from app.models.enums import PaymentStatus, PlanStatus, SubscriptionStatus
from app.models.schemas.billing import (
    PaymentResponse,
    PlanResponse,
    SubscribeRequest,
    SubscriptionResponse,
)
from app.models.schemas.common import MessageResponse
from app.models.user import User
from app.services.billing import BillingService

router = APIRouter(prefix="/billing", tags=["Billing"])
billing_service = BillingService()


@router.get("/currencies")
async def list_currencies():
    currencies = await billing_service.get_available_currencies()
    return currencies


@router.get("/plans", response_model=list[PlanResponse])
async def list_plans(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Plan)
        .where(Plan.status == PlanStatus.ACTIVE)
        .order_by(Plan.price_usd.asc())
    )
    plans = result.scalars().all()
    return [PlanResponse.model_validate(p) for p in plans]


@router.post("/subscribe", response_model=SubscriptionResponse)
async def subscribe_to_plan(
    request: SubscribeRequest,
    current_user: User = Depends(require_verified_email),
    db: AsyncSession = Depends(get_db),
):
    # Check plan exists and is active
    plan_result = await db.execute(
        select(Plan).where(Plan.id == request.plan_id, Plan.status == PlanStatus.ACTIVE)
    )
    plan = plan_result.scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found or inactive")

    # Check for existing active or pending subscription
    existing_result = await db.execute(
        select(Subscription).where(
            Subscription.user_id == current_user.id,
            Subscription.status.in_(
                [
                    SubscriptionStatus.ACTIVE,
                    SubscriptionStatus.PENDING_PAYMENT,
                ]
            ),
        )
    )
    existing_subs = existing_result.scalars().all()

    for sub in existing_subs:
        if sub.status == SubscriptionStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You already have an active subscription. Please cancel it first or wait for it to expire.",
            )

    # Cancel any existing pending subscriptions
    for sub in existing_subs:
        if sub.status == SubscriptionStatus.PENDING_PAYMENT:
            sub.status = SubscriptionStatus.CANCELLED

    # Create NOWPayments invoice
    invoice = await billing_service.create_invoice(
        price_amount=float(plan.price_usd),
        pay_currency=request.pay_currency,
        order_id=str(current_user.id),
        order_description=f"EulerX {plan.name} - {plan.billing_cycle.value}",
    )

    invoice_id = str(invoice.get("id", "")) if invoice else None
    invoice_url = invoice.get("invoice_url") if invoice else None

    subscription = Subscription(
        user_id=current_user.id,
        plan_id=plan.id,
        status=SubscriptionStatus.PENDING_PAYMENT,
        nowpayments_invoice_id=invoice_id,
        invoice_url=invoice_url,
    )
    db.add(subscription)
    await db.flush()

    # Create initial payment record so it shows in payment history
    payment = Payment(
        subscription_id=subscription.id,
        user_id=current_user.id,
        amount_usd=float(plan.price_usd),
        crypto_currency=request.pay_currency,
        nowpayments_invoice_id=invoice_id,
        status=PaymentStatus.WAITING,
    )
    db.add(payment)

    response = SubscriptionResponse.model_validate(subscription)
    response.plan = PlanResponse.model_validate(plan)
    return response


@router.get("/subscription", response_model=SubscriptionResponse | None)
async def get_current_subscription(
    current_user: User = Depends(require_verified_email),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Subscription)
        .where(Subscription.user_id == current_user.id)
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    subscription = result.scalar_one_or_none()
    if subscription is None:
        return None

    plan_result = await db.execute(select(Plan).where(Plan.id == subscription.plan_id))
    plan = plan_result.scalar_one_or_none()

    response = SubscriptionResponse.model_validate(subscription)
    if plan:
        response.plan = PlanResponse.model_validate(plan)
    return response


@router.get("/payments", response_model=list[PaymentResponse])
async def list_payments(
    current_user: User = Depends(require_verified_email),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Payment)
        .where(Payment.user_id == current_user.id)
        .order_by(Payment.created_at.desc())
    )
    payments = result.scalars().all()
    return [PaymentResponse.model_validate(p) for p in payments]


@router.post("/webhook/nowpayments", response_model=MessageResponse)
async def nowpayments_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    body = await request.body()
    signature = request.headers.get("x-nowpayments-sig", "")

    if not billing_service.ipn_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Payment webhook not configured",
        )

    if not signature or not billing_service.verify_webhook_signature(body, signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    try:
        data = await request.json()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        ) from e

    success = await billing_service.process_webhook(db, data)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to process webhook",
        )

    return MessageResponse(message="Webhook processed successfully")
