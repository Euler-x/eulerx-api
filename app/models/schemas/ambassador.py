import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.models.enums import (
    ActivityEventType,
    AmbassadorRank,
    AmbassadorStatus,
    BonusType,
    CommissionStatus,
    PayoutStatus,
    TravelStatus,
)


# ── Core ambassador ───────────────────────────────────────────────────────────


class AmbassadorResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    rank: AmbassadorRank
    referral_code: str
    par_count: int = 0
    tav_count: int = 0
    total_referrals: int
    rewards_earned: float
    payout_address: Optional[str] = None
    rank_achieved_at: Optional[datetime] = None
    fast_start_claimed: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class LeaderboardEntry(BaseModel):
    rank_position: int
    user_id: uuid.UUID
    ambassador_rank: AmbassadorRank
    total_referrals: int
    tav_count: int = 0
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
    subscription_months: int = 0


# ── Earnings summary ──────────────────────────────────────────────────────────


class RankProgress(BaseModel):
    current_rank: AmbassadorRank
    next_rank: Optional[AmbassadorRank] = None
    current_rank_label: str
    next_rank_label: Optional[str] = None
    par_count: int
    tav_count: int
    # Requirements for next rank
    par_required: Optional[int] = None
    tav_required: Optional[int] = None
    legs_required: Optional[int] = None
    leg_rank_required: Optional[str] = None
    qualifying_legs: int = 0
    # Depth currently earning on
    current_depth: int = 1
    next_depth: Optional[int] = None


class EarningsSummary(BaseModel):
    total_commission_paid: float
    total_commission_pending: float
    current_month_commission: float
    next_payout_date: str
    active_referral_count: int
    tav_count: int
    rank_progress: RankProgress
    fast_start_eligible: bool = False
    fast_start_period: int = 0


# ── Commission records ────────────────────────────────────────────────────────


class CommissionResponse(BaseModel):
    id: uuid.UUID
    month: int
    year: int
    active_referral_count: int
    tav_count: int = 0
    commission_amount: float
    level_breakdown: Optional[dict] = None
    generational_override: float = 0
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


# ── Travel incentive ──────────────────────────────────────────────────────────


class TravelIncentiveResponse(BaseModel):
    id: uuid.UUID
    destination: str
    rank_required: AmbassadorRank
    qualification_start: datetime
    qualification_end: Optional[datetime] = None
    status: TravelStatus
    awarded_at: Optional[datetime] = None
    admin_notes: Optional[str] = None

    model_config = {"from_attributes": True}


# ── Leadership Revenue Pool ───────────────────────────────────────────────────


class LeadershipPoolResponse(BaseModel):
    id: uuid.UUID
    month: int
    year: int
    total_pool_amount: float
    eligible_ambassador_count: int
    per_ambassador_amount: float
    status: CommissionStatus
    paid_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Training ──────────────────────────────────────────────────────────────────


class TrainingModuleSchema(BaseModel):
    key: str
    name: str
    rank: AmbassadorRank
    duration_min: int
    completed: bool
    completed_at: Optional[datetime] = None


class TrainingResponse(BaseModel):
    modules: list[TrainingModuleSchema]
    completed_count: int
    total_count: int


# ── Territory ────────────────────────────────────────────────────────────────


class TerritoryResponse(BaseModel):
    id: uuid.UUID
    name: str
    territory_type: str
    description: Optional[str] = None
    revenue_share_pct: float

    model_config = {"from_attributes": True}


# ── Admin schemas ─────────────────────────────────────────────────────────────


class AdminAmbassadorResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    full_name: Optional[str] = None
    masked_email: Optional[str] = None
    rank: AmbassadorRank
    status: AmbassadorStatus = AmbassadorStatus.ACTIVE
    par_count: int = 0
    tav_count: int = 0
    total_referrals: int
    rewards_earned: float
    payout_address: Optional[str] = None
    rank_achieved_at: Optional[datetime] = None
    admin_notes: Optional[str] = None
    fast_start_claimed: int = 0
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


class RecalculateCommissionRequest(BaseModel):
    month: int
    year: int
    ambassador_id: Optional[uuid.UUID] = None


class RunMonthlyCycleRequest(BaseModel):
    month: int
    year: int


class AwardTravelRequest(BaseModel):
    admin_notes: Optional[str] = None


class UpdateTravelRequest(BaseModel):
    status: TravelStatus
    admin_notes: Optional[str] = None
    awarded_at: Optional[datetime] = None


class CreateManualBonusRequest(BaseModel):
    bonus_type: BonusType
    amount: float
    description: str
    period: Optional[str] = None


# ── Ambassador status / admin control ────────────────────────────────────────


class UpdateAmbassadorRequest(BaseModel):
    status: Optional[AmbassadorStatus] = None
    admin_notes: Optional[str] = None
    payout_address: Optional[str] = None
    rank_achieved_at: Optional[datetime] = None


# ── Commission / bonus override ───────────────────────────────────────────────


class UpdateCommissionRequest(BaseModel):
    status: Optional[CommissionStatus] = None
    commission_amount: Optional[float] = None
    admin_notes: Optional[str] = None


class UpdateBonusRequest(BaseModel):
    status: Optional[CommissionStatus] = None
    amount: Optional[float] = None
    description: Optional[str] = None


# ── Bulk operations ───────────────────────────────────────────────────────────


class BulkPayoutUpdateRequest(BaseModel):
    payout_ids: list[uuid.UUID]
    status: PayoutStatus
    admin_notes: Optional[str] = None


class BulkCommissionApproveRequest(BaseModel):
    commission_ids: list[uuid.UUID]


# ── Activity log ──────────────────────────────────────────────────────────────


class ActivityLogResponse(BaseModel):
    id: uuid.UUID
    ambassador_id: uuid.UUID
    event_type: ActivityEventType
    description: str
    amount: Optional[float] = None
    related_id: Optional[str] = None
    actor: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Downline tree ─────────────────────────────────────────────────────────────


class DownlineNode(BaseModel):
    ambassador_id: uuid.UUID
    user_id: uuid.UUID
    masked_email: Optional[str] = None
    rank: AmbassadorRank
    par_count: int
    tav_count: int
    depth: int
    direct_referral_count: int


class DownlineResponse(BaseModel):
    ambassador_id: uuid.UUID
    total_nodes: int
    nodes_by_level: dict[int, list[DownlineNode]]


# ── Analytics ─────────────────────────────────────────────────────────────────


class RankDistributionItem(BaseModel):
    rank: AmbassadorRank
    count: int


class TopEarnerItem(BaseModel):
    ambassador_id: uuid.UUID
    masked_email: Optional[str] = None
    rank: AmbassadorRank
    commission_amount: float


class AmbassadorAnalyticsResponse(BaseModel):
    month: int
    year: int
    rank_distribution: list[RankDistributionItem]
    total_commissions_this_month: float
    total_bonuses_this_month: float
    new_ambassadors_this_month: int
    new_referrals_this_month: int
    pool_amount_this_month: float
    pending_payouts_count: int
    pending_payouts_amount: float
    top_earners: list[TopEarnerItem]


# ── Global list items (enriched with ambassador info) ─────────────────────────


class AdminCommissionResponse(CommissionResponse):
    ambassador_id: uuid.UUID
    masked_email: Optional[str] = None
    rank: Optional[AmbassadorRank] = None


class AdminBonusResponse(BonusResponse):
    ambassador_id: uuid.UUID
    masked_email: Optional[str] = None
    rank: Optional[AmbassadorRank] = None


class AdminTravelResponse(TravelIncentiveResponse):
    ambassador_id: uuid.UUID
    masked_email: Optional[str] = None
    rank: Optional[AmbassadorRank] = None


class TrainingCompletionResponse(BaseModel):
    id: uuid.UUID
    ambassador_id: uuid.UUID
    masked_email: Optional[str] = None
    rank: Optional[AmbassadorRank] = None
    module_name: str
    completed_at: datetime
    notes: Optional[str] = None

    model_config = {"from_attributes": True}


class AdminPayoutResponse(PayoutResponse):
    ambassador_id: uuid.UUID
    masked_email: Optional[str] = None
    rank: Optional[AmbassadorRank] = None
    commission_ids: Optional[list] = None
    bonus_ids: Optional[list] = None
