import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.models.enums import (
    AmbassadorRank,
    BonusType,
    CommissionStatus,
    PayoutStatus,
    TerritoryType,
)


# ── Core ambassador schemas ───────────────────────────────────────────────────


class AmbassadorResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    rank: AmbassadorRank
    referral_code: str
    team_size: int
    total_referrals: int
    rewards_earned: float
    payout_address: Optional[str] = None
    territory_id: Optional[uuid.UUID] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class LeaderboardEntry(BaseModel):
    rank_position: int
    user_id: uuid.UUID
    ambassador_rank: AmbassadorRank
    total_referrals: int
    rewards_earned: float


class ReferralResponse(BaseModel):
    referral_code: str
    referral_link: str


# ── Referrals list ────────────────────────────────────────────────────────────


class ReferralItem(BaseModel):
    joined_at: datetime
    plan_name: Optional[str] = None
    plan_price: Optional[float] = None
    is_subscribed: bool
    rank: AmbassadorRank


# ── Earnings summary ──────────────────────────────────────────────────────────


class TierProgress(BaseModel):
    current_rank: AmbassadorRank
    next_rank: Optional[AmbassadorRank] = None
    active_referrals: int
    required_active_referrals: Optional[int] = None
    required_retention_pct: Optional[float] = None


class EarningsSummary(BaseModel):
    total_commission_paid: float
    total_commission_pending: float
    current_month_commission: float
    next_payout_date: str
    active_referral_count: int
    retention_rate: float
    tier_progress: TierProgress


# ── Commission records ────────────────────────────────────────────────────────


class CommissionResponse(BaseModel):
    id: uuid.UUID
    month: int
    year: int
    active_referral_count: int
    commission_rate: Optional[float] = None
    commission_amount: float
    status: CommissionStatus
    paid_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Bonus records ─────────────────────────────────────────────────────────────


class BonusResponse(BaseModel):
    id: uuid.UUID
    bonus_type: BonusType
    amount: float
    period: Optional[str] = None
    description: Optional[str] = None
    status: CommissionStatus
    paid_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Payout records ────────────────────────────────────────────────────────────


class PayoutResponse(BaseModel):
    id: uuid.UUID
    total_amount: float
    status: PayoutStatus
    payout_address: Optional[str] = None
    admin_notes: Optional[str] = None
    paid_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Territory ─────────────────────────────────────────────────────────────────


class TerritoryResponse(BaseModel):
    id: uuid.UUID
    name: str
    territory_type: TerritoryType
    description: Optional[str] = None
    revenue_share_pct: float
    master_ambassador_id: Optional[uuid.UUID] = None
    ambassador_count: int = 0

    model_config = {"from_attributes": True}


# ── Training ──────────────────────────────────────────────────────────────────


class TrainingModuleSchema(BaseModel):
    key: str
    name: str
    tier: AmbassadorRank
    duration_min: int
    completed: bool
    completed_at: Optional[datetime] = None


class TrainingResponse(BaseModel):
    modules: list[TrainingModuleSchema]


# ── Admin schemas ─────────────────────────────────────────────────────────────


class AdminAmbassadorResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    masked_email: Optional[str] = None
    rank: AmbassadorRank
    total_referrals: int
    active_referral_count: int
    rewards_earned: float
    payout_address: Optional[str] = None
    territory_name: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class PromoteAmbassadorRequest(BaseModel):
    rank: AmbassadorRank


class CreatePayoutRequest(BaseModel):
    commission_ids: list[uuid.UUID] = []
    bonus_ids: list[uuid.UUID] = []
    payout_address: Optional[str] = None
    admin_notes: Optional[str] = None


class UpdatePayoutRequest(BaseModel):
    status: PayoutStatus
    admin_notes: Optional[str] = None


class CreateTerritoryRequest(BaseModel):
    name: str
    territory_type: TerritoryType
    description: Optional[str] = None
    master_ambassador_id: Optional[uuid.UUID] = None
    revenue_share_pct: float = 5.0


class UpdateTerritoryRequest(BaseModel):
    name: Optional[str] = None
    territory_type: Optional[TerritoryType] = None
    description: Optional[str] = None
    master_ambassador_id: Optional[uuid.UUID] = None
    revenue_share_pct: Optional[float] = None


class CreateAnnualBonusRequest(BaseModel):
    amount: float
    description: str
    period: str  # e.g. "2026"


class RecalculateCommissionRequest(BaseModel):
    month: int
    year: int
    ambassador_id: Optional[uuid.UUID] = None


class TerritoryCommissionResponse(BaseModel):
    id: uuid.UUID
    territory_id: uuid.UUID
    master_ambassador_id: uuid.UUID
    month: int
    year: int
    territory_volume_usd: Optional[float] = None
    ambassador_count: Optional[int] = None
    revenue_share_pct: Optional[float] = None
    commission_amount: Optional[float] = None
    status: CommissionStatus
    paid_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}
