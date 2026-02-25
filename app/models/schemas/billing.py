import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.enums import (
    BillingCycle,
    PaymentStatus,
    PlanStatus,
    SubscriptionStatus,
)


class PlanCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    price_usd: float = Field(..., ge=0)
    billing_cycle: BillingCycle
    features: dict = Field(default_factory=dict)
    max_strategies: int = Field(default=1, ge=1)
    max_allocation: float = Field(default=1000.0, ge=0)
    ate_access: bool = False
    trial_days: int = Field(default=0, ge=0)
    status: PlanStatus = PlanStatus.ACTIVE


class PlanUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    price_usd: Optional[float] = Field(None, ge=0)
    billing_cycle: Optional[BillingCycle] = None
    features: Optional[dict] = None
    max_strategies: Optional[int] = Field(None, ge=1)
    max_allocation: Optional[float] = Field(None, ge=0)
    ate_access: Optional[bool] = None
    trial_days: Optional[int] = Field(None, ge=0)
    status: Optional[PlanStatus] = None


class PlanResponse(BaseModel):
    id: uuid.UUID
    name: str
    price_usd: float
    billing_cycle: BillingCycle
    features: dict
    max_strategies: int
    max_allocation: float
    ate_access: bool
    trial_days: int
    status: PlanStatus
    created_at: datetime

    model_config = {"from_attributes": True}


class SubscribeRequest(BaseModel):
    plan_id: uuid.UUID
    pay_currency: Optional[str] = None


class SubscriptionResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    plan_id: uuid.UUID
    status: SubscriptionStatus
    started_at: Optional[datetime]
    expires_at: Optional[datetime]
    grace_until: Optional[datetime]
    nowpayments_invoice_id: Optional[str]
    invoice_url: Optional[str] = None
    plan: Optional[PlanResponse] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class PaymentResponse(BaseModel):
    id: uuid.UUID
    subscription_id: uuid.UUID
    amount_usd: float
    amount_crypto: Optional[float]
    crypto_currency: Optional[str]
    nowpayments_payment_id: Optional[str]
    status: PaymentStatus
    paid_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class NOWPaymentsWebhook(BaseModel):
    payment_id: Optional[int] = None
    invoice_id: Optional[int] = None
    payment_status: Optional[str] = None
    pay_address: Optional[str] = None
    price_amount: Optional[float] = None
    price_currency: Optional[str] = None
    pay_amount: Optional[float] = None
    pay_currency: Optional[str] = None
    order_id: Optional[str] = None
    order_description: Optional[str] = None
    outcome_amount: Optional[float] = None
    outcome_currency: Optional[str] = None

    model_config = {"extra": "allow"}


class SubscriptionOverride(BaseModel):
    status: SubscriptionStatus
    expires_at: Optional[datetime] = None
    grace_until: Optional[datetime] = None
