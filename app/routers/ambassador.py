"""User-facing ambassador program endpoints."""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.middleware.permissions import RequireVerified, UserPermissions
from app.models.ambassador import Ambassador
from app.models.ambassador_programs import (
    AmbassadorBonus,
    AmbassadorCommission,
    AmbassadorPayout,
    AmbassadorTrainingCompletion,
)
from app.models.billing import Plan, Subscription
from app.models.enums import AmbassadorRank, CommissionStatus, SubscriptionStatus
from app.models.schemas.ambassador import (
    AmbassadorResponse,
    BonusResponse,
    CommissionResponse,
    EarningsSummary,
    LeaderboardEntry,
    PayoutResponse,
    ReferralItem,
    ReferralResponse,
    TerritoryResponse,
    TierProgress,
    TrainingModuleSchema,
    TrainingResponse,
)
from app.models.schemas.common import PaginatedResponse
from app.models.user import User
from app.config import get_settings
from app.utils.helpers import generate_referral_code
from app.services.ambassador import (
    TIER_REQUIREMENTS,
    get_active_referrals,
    get_retention_rate,
)

settings = get_settings()

router = APIRouter(prefix="/ambassador", tags=["Ambassador"])

# Training modules definition
TRAINING_MODULES = [
    {
        "key": "non_custodial_basics",
        "name": "Non-Custodial Architecture Basics",
        "tier": AmbassadorRank.SCOUT,
        "duration_min": 15,
    },
    {
        "key": "product_walkthrough",
        "name": "Product Walkthrough",
        "tier": AmbassadorRank.SCOUT,
        "duration_min": 30,
    },
    {
        "key": "advanced_features",
        "name": "Advanced Features Deep Dive",
        "tier": AmbassadorRank.GUIDE,
        "duration_min": 60,
    },
    {
        "key": "community_management",
        "name": "Community Management Best Practices",
        "tier": AmbassadorRank.GUIDE,
        "duration_min": 45,
    },
    {
        "key": "marketing_strategy",
        "name": "Marketing Strategy Session",
        "tier": AmbassadorRank.GUIDE,
        "duration_min": 30,
    },
    {
        "key": "technical_architecture",
        "name": "Technical Architecture",
        "tier": AmbassadorRank.STRATEGIST,
        "duration_min": 45,
    },
    {
        "key": "risk_management",
        "name": "Risk Management Framework",
        "tier": AmbassadorRank.STRATEGIST,
        "duration_min": 30,
    },
    {
        "key": "institutional_sales",
        "name": "Institutional Sales Approach",
        "tier": AmbassadorRank.STRATEGIST,
        "duration_min": 60,
    },
    {
        "key": "territory_strategy",
        "name": "Territory Strategy Development",
        "tier": AmbassadorRank.MASTER,
        "duration_min": 120,
    },
    {
        "key": "sub_ambassador_management",
        "name": "Sub-Ambassador Management",
        "tier": AmbassadorRank.MASTER,
        "duration_min": 90,
    },
]


async def _get_or_none(user_id: uuid.UUID, db: AsyncSession) -> Optional[Ambassador]:
    result = await db.execute(select(Ambassador).where(Ambassador.user_id == user_id))
    return result.scalar_one_or_none()


# ── Existing endpoints ────────────────────────────────────────────────────────


@router.get("", response_model=AmbassadorResponse | None)
async def get_ambassador_dashboard(
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    ambassador = await _get_or_none(perms.id, db)
    if ambassador is None:
        return None
    return AmbassadorResponse.model_validate(ambassador)


@router.get("/leaderboard", response_model=list[LeaderboardEntry])
async def get_leaderboard(
    limit: int = Query(20, ge=1, le=100),
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Ambassador).order_by(Ambassador.total_referrals.desc()).limit(limit)
    )
    ambassadors = result.scalars().all()

    return [
        LeaderboardEntry(
            rank_position=idx + 1,
            user_id=amb.user_id,
            ambassador_rank=amb.rank,
            total_referrals=amb.total_referrals,
            rewards_earned=float(amb.rewards_earned),
        )
        for idx, amb in enumerate(ambassadors)
    ]


@router.post("/referral", response_model=ReferralResponse)
async def generate_referral(
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    ambassador = await _get_or_none(perms.id, db)

    if ambassador is None:
        code = generate_referral_code()
        ambassador = Ambassador(
            user_id=perms.id,
            referral_code=code,
        )
        db.add(ambassador)
        await db.flush()

    return ReferralResponse(
        referral_code=ambassador.referral_code,
        referral_link=f"{settings.frontend_url}/ref/{ambassador.referral_code}",
    )


@router.get("/team", response_model=list[AmbassadorResponse])
async def get_team(
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    ambassador = await _get_or_none(perms.id, db)
    if ambassador is None:
        return []

    team_result = await db.execute(
        select(Ambassador).where(Ambassador.referred_by == ambassador.id)
    )
    team = team_result.scalars().all()
    return [AmbassadorResponse.model_validate(t) for t in team]


# ── New endpoints ─────────────────────────────────────────────────────────────


@router.patch("/payout-address")
async def update_payout_address(
    payout_address: str = Body(..., embed=True),
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    ambassador = await _get_or_none(perms.id, db)
    if ambassador is None:
        code = generate_referral_code()
        ambassador = Ambassador(
            user_id=perms.id,
            referral_code=code,
            payout_address=payout_address,
        )
        db.add(ambassador)
    else:
        ambassador.payout_address = payout_address
    await db.flush()
    return {"payout_address": payout_address}


@router.get("/referrals", response_model=list[ReferralItem])
async def get_referrals(
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    ambassador = await _get_or_none(perms.id, db)
    if ambassador is None:
        return []

    referrals_result = await db.execute(
        select(Ambassador).where(Ambassador.referred_by == ambassador.id)
    )
    referrals = referrals_result.scalars().all()

    items = []
    for ref in referrals:
        # Get the user
        user_result = await db.execute(select(User).where(User.id == ref.user_id))
        user = user_result.scalar_one_or_none()
        if user is None:
            continue

        # Get active subscription + plan
        sub_result = await db.execute(
            select(Subscription, Plan)
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(
                Subscription.user_id == ref.user_id,
                Subscription.status.in_(
                    [SubscriptionStatus.ACTIVE, SubscriptionStatus.EXPIRING_SOON]
                ),
            )
            .order_by(Subscription.created_at.desc())
            .limit(1)
        )
        row = sub_result.first()
        plan_name = row[1].name if row else None
        plan_price = float(row[1].price_usd) if row else None

        items.append(
            ReferralItem(
                joined_at=ref.created_at,
                plan_name=plan_name,
                plan_price=plan_price,
                is_subscribed=user.is_subscribed,
                rank=ref.rank,
            )
        )
    return items


@router.get("/earnings-summary", response_model=EarningsSummary)
async def get_earnings_summary(
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    ambassador = await _get_or_none(perms.id, db)
    if ambassador is None:
        empty_progress = TierProgress(
            current_rank=AmbassadorRank.SCOUT,
            next_rank=AmbassadorRank.GUIDE,
            active_referrals=0,
            required_active_referrals=10,
        )
        now = datetime.now(timezone.utc)
        next_month = now.month % 12 + 1
        next_year = now.year + (1 if now.month == 12 else 0)
        return EarningsSummary(
            total_commission_paid=0,
            total_commission_pending=0,
            current_month_commission=0,
            next_payout_date=f"{next_year}-{next_month:02d}-15",
            active_referral_count=0,
            retention_rate=0.0,
            tier_progress=empty_progress,
        )

    # Commission totals
    comm_result = await db.execute(
        select(AmbassadorCommission).where(
            AmbassadorCommission.ambassador_id == ambassador.id
        )
    )
    commissions = comm_result.scalars().all()

    bonus_result = await db.execute(
        select(AmbassadorBonus).where(AmbassadorBonus.ambassador_id == ambassador.id)
    )
    bonuses = bonus_result.scalars().all()

    total_paid = sum(
        float(c.commission_amount)
        for c in commissions
        if c.status == CommissionStatus.PAID
    ) + sum(float(b.amount) for b in bonuses if b.status == CommissionStatus.PAID)

    total_pending = sum(
        float(c.commission_amount)
        for c in commissions
        if c.status == CommissionStatus.PENDING
    ) + sum(float(b.amount) for b in bonuses if b.status == CommissionStatus.PENDING)

    # Current month commission
    now = datetime.now(timezone.utc)
    current_comm_result = await db.execute(
        select(AmbassadorCommission).where(
            AmbassadorCommission.ambassador_id == ambassador.id,
            AmbassadorCommission.month == now.month,
            AmbassadorCommission.year == now.year,
        )
    )
    current_comm = current_comm_result.scalar_one_or_none()
    current_month_commission = (
        float(current_comm.commission_amount) if current_comm else 0.0
    )

    # Next payout date: 15th of next month
    next_month = now.month % 12 + 1
    next_year = now.year + (1 if now.month == 12 else 0)
    next_payout_date = f"{next_year}-{next_month:02d}-15"

    # Active referrals
    active_refs = await get_active_referrals(ambassador.id, db)
    retention_rate = await get_retention_rate(ambassador.id, db)

    # Tier progress
    rank_order = [
        AmbassadorRank.SCOUT,
        AmbassadorRank.GUIDE,
        AmbassadorRank.STRATEGIST,
        AmbassadorRank.MASTER,
    ]
    current_idx = rank_order.index(ambassador.rank)
    next_rank = (
        rank_order[current_idx + 1] if current_idx < len(rank_order) - 1 else None
    )
    next_reqs = TIER_REQUIREMENTS.get(next_rank) if next_rank else None

    tier_progress = TierProgress(
        current_rank=ambassador.rank,
        next_rank=next_rank,
        active_referrals=len(active_refs),
        required_active_referrals=next_reqs.get("min_active_referrals")
        if next_reqs
        else None,
        required_retention_pct=next_reqs.get("min_retention_pct")
        if next_reqs
        else None,
    )

    return EarningsSummary(
        total_commission_paid=total_paid,
        total_commission_pending=total_pending,
        current_month_commission=current_month_commission,
        next_payout_date=next_payout_date,
        active_referral_count=len(active_refs),
        retention_rate=retention_rate,
        tier_progress=tier_progress,
    )


@router.get("/commissions", response_model=PaginatedResponse)
async def get_commissions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    ambassador = await _get_or_none(perms.id, db)
    if ambassador is None:
        return PaginatedResponse(
            items=[], total=0, page=page, page_size=page_size, total_pages=0
        )

    count_result = await db.execute(
        select(func.count(AmbassadorCommission.id)).where(
            AmbassadorCommission.ambassador_id == ambassador.id
        )
    )
    total = count_result.scalar() or 0
    offset = (page - 1) * page_size

    result = await db.execute(
        select(AmbassadorCommission)
        .where(AmbassadorCommission.ambassador_id == ambassador.id)
        .order_by(AmbassadorCommission.year.desc(), AmbassadorCommission.month.desc())
        .offset(offset)
        .limit(page_size)
    )
    records = result.scalars().all()

    return PaginatedResponse(
        items=[CommissionResponse.model_validate(r) for r in records],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


@router.get("/bonuses", response_model=PaginatedResponse)
async def get_bonuses(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    ambassador = await _get_or_none(perms.id, db)
    if ambassador is None:
        return PaginatedResponse(
            items=[], total=0, page=page, page_size=page_size, total_pages=0
        )

    count_result = await db.execute(
        select(func.count(AmbassadorBonus.id)).where(
            AmbassadorBonus.ambassador_id == ambassador.id
        )
    )
    total = count_result.scalar() or 0
    offset = (page - 1) * page_size

    result = await db.execute(
        select(AmbassadorBonus)
        .where(AmbassadorBonus.ambassador_id == ambassador.id)
        .order_by(AmbassadorBonus.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    records = result.scalars().all()

    return PaginatedResponse(
        items=[BonusResponse.model_validate(r) for r in records],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


@router.get("/payouts", response_model=PaginatedResponse)
async def get_payouts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    ambassador = await _get_or_none(perms.id, db)
    if ambassador is None:
        return PaginatedResponse(
            items=[], total=0, page=page, page_size=page_size, total_pages=0
        )

    count_result = await db.execute(
        select(func.count(AmbassadorPayout.id)).where(
            AmbassadorPayout.ambassador_id == ambassador.id
        )
    )
    total = count_result.scalar() or 0
    offset = (page - 1) * page_size

    result = await db.execute(
        select(AmbassadorPayout)
        .where(AmbassadorPayout.ambassador_id == ambassador.id)
        .order_by(AmbassadorPayout.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    records = result.scalars().all()

    return PaginatedResponse(
        items=[PayoutResponse.model_validate(r) for r in records],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


@router.get("/territory", response_model=TerritoryResponse | None)
async def get_territory(
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    from app.models.ambassador_programs import AmbassadorTerritory

    ambassador = await _get_or_none(perms.id, db)
    if ambassador is None or ambassador.territory_id is None:
        return None

    terr_result = await db.execute(
        select(AmbassadorTerritory).where(
            AmbassadorTerritory.id == ambassador.territory_id
        )
    )
    territory = terr_result.scalar_one_or_none()
    if territory is None:
        return None

    count_result = await db.execute(
        select(func.count(Ambassador.id)).where(Ambassador.territory_id == territory.id)
    )
    ambassador_count = count_result.scalar() or 0

    return TerritoryResponse(
        id=territory.id,
        name=territory.name,
        territory_type=territory.territory_type,
        description=territory.description,
        revenue_share_pct=float(territory.revenue_share_pct),
        master_ambassador_id=territory.master_ambassador_id,
        ambassador_count=ambassador_count,
    )


@router.get("/training", response_model=TrainingResponse)
async def get_training(
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    ambassador = await _get_or_none(perms.id, db)

    completed_map: dict[str, datetime] = {}
    if ambassador is not None:
        completions_result = await db.execute(
            select(AmbassadorTrainingCompletion).where(
                AmbassadorTrainingCompletion.ambassador_id == ambassador.id
            )
        )
        for completion in completions_result.scalars().all():
            completed_map[completion.module_name] = completion.completed_at

    modules = [
        TrainingModuleSchema(
            key=m["key"],
            name=m["name"],
            tier=m["tier"],
            duration_min=m["duration_min"],
            completed=m["key"] in completed_map,
            completed_at=completed_map.get(m["key"]),
        )
        for m in TRAINING_MODULES
    ]
    return TrainingResponse(modules=modules)
