import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.enums import (
    AmbassadorRank,
    RiskProfile,
    SubscriptionStatus,
    WalletType,
)


# ── Existing ───────────────────────────────────────────────────


class AdminConfigResponse(BaseModel):
    key: str
    value: dict
    description: Optional[str]
    updated_at: datetime

    model_config = {"from_attributes": True}


class AdminConfigUpdate(BaseModel):
    value: dict
    description: Optional[str] = None


# ── User Admin ─────────────────────────────────────────────────


class AdminUserUpdate(BaseModel):
    email: Optional[str] = None
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None


class AdminUserDetailResponse(BaseModel):
    id: uuid.UUID
    wallet_address_hash: Optional[str] = None
    wallet_type: Optional[WalletType] = None
    is_admin: bool
    is_active: bool
    email: Optional[str] = None
    email_verified: bool = False
    telegram_configured: bool = False
    created_at: datetime
    strategy_count: int = 0
    execution_count: int = 0
    subscription_count: int = 0
    transaction_count: int = 0

    model_config = {"from_attributes": True}


# ── Subscription Admin ─────────────────────────────────────────


class AdminSubscriptionCreate(BaseModel):
    user_id: uuid.UUID
    plan_id: uuid.UUID
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE
    expires_at: Optional[datetime] = None


# ── Strategy Admin ─────────────────────────────────────────────


class AdminStrategyUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    risk_profile: Optional[RiskProfile] = None
    leverage_limit: Optional[float] = Field(None, ge=1.0, le=100.0)
    max_positions: Optional[int] = Field(None, ge=1, le=50)
    allocation_pct: Optional[float] = Field(None, ge=1.0, le=100.0)
    max_drawdown_percent: Optional[float] = Field(None, gt=0, le=100)
    is_active: Optional[bool] = None


# ── Ambassador Admin ───────────────────────────────────────────


class AdminAmbassadorUpdate(BaseModel):
    rank: Optional[AmbassadorRank] = None
    rewards_earned: Optional[float] = Field(None, ge=0)
    team_size: Optional[int] = Field(None, ge=0)
    total_referrals: Optional[int] = Field(None, ge=0)


# ── Audit Log ──────────────────────────────────────────────────


class AuditLogResponse(BaseModel):
    id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    action: str
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    details: Optional[dict] = None
    ip_address: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Analytics ──────────────────────────────────────────────────


class RevenueAnalyticsResponse(BaseModel):
    total_revenue_usd: float
    active_subscriptions: int
    total_users: int
    revenue_by_plan: list[dict] = []
    period_revenue_usd: Optional[float] = None


class UserGrowthResponse(BaseModel):
    total_users: int
    new_users_period: int
    active_users: int
    admin_users: int


class ExecutionStatsResponse(BaseModel):
    total_executions: int
    pending: int
    filled: int
    failed: int
    total_pnl: float
