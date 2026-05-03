"""User-facing ambassador programme V2 endpoints."""

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
    AmbassadorTerritory,
    AmbassadorTrainingCompletion,
    AmbassadorTravelIncentive,
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
    RankProgress,
    ReferralItem,
    ReferralResponse,
    TerritoryResponse,
    TrainingModuleSchema,
    TrainingResponse,
    TravelIncentiveResponse,
)
from app.models.schemas.common import PaginatedResponse
from app.models.user import User
from app.config import get_settings
from app.utils.helpers import generate_referral_code
from app.services.ambassador import (
    RANK_CRITERIA,
    RANK_MAX_DEPTH,
    RANK_ORDER,
    get_par_count,
    get_tav_count,
    count_qualifying_legs,
)

settings = get_settings()

router = APIRouter(prefix="/ambassador", tags=["Ambassador"])

# Training modules (updated rank labels to match V2)
TRAINING_MODULES = [
    {
        "key": "platform_overview",
        "name": "EulerX Platform Overview",
        "rank": AmbassadorRank.ASSOCIATE,
        "duration_min": 15,
    },
    {
        "key": "product_walkthrough",
        "name": "ATE Pro Product Walkthrough",
        "rank": AmbassadorRank.ASSOCIATE,
        "duration_min": 30,
    },
    {
        "key": "referral_best_practices",
        "name": "Referral Best Practices",
        "rank": AmbassadorRank.BRONZE_LEADER,
        "duration_min": 30,
    },
    {
        "key": "community_management",
        "name": "Community Management & Retention",
        "rank": AmbassadorRank.BRONZE_LEADER,
        "duration_min": 45,
    },
    {
        "key": "advanced_features",
        "name": "Advanced ATE Pro Features Deep Dive",
        "rank": AmbassadorRank.SILVER_LEADER,
        "duration_min": 60,
    },
    {
        "key": "marketing_strategy",
        "name": "Marketing Strategy & Conversion",
        "rank": AmbassadorRank.GOLD_LEADER,
        "duration_min": 30,
    },
    {
        "key": "risk_management",
        "name": "Risk Management Framework",
        "rank": AmbassadorRank.PLATINUM_LEADER,
        "duration_min": 30,
    },
    {
        "key": "team_leadership",
        "name": "Team Leadership & Sub-Ambassador Development",
        "rank": AmbassadorRank.DIAMOND_LEADER,
        "duration_min": 60,
    },
    {
        "key": "institutional_approach",
        "name": "Institutional & High-Net-Worth Approach",
        "rank": AmbassadorRank.ELITE_DIAMOND,
        "duration_min": 60,
    },
    {
        "key": "network_strategy",
        "name": "Network Strategy & Organisation Building",
        "rank": AmbassadorRank.BLACK_DIAMOND,
        "duration_min": 120,
    },
]


async def _get_or_none(user_id: uuid.UUID, db: AsyncSession) -> Optional[Ambassador]:
    result = await db.execute(select(Ambassador).where(Ambassador.user_id == user_id))
    return result.scalar_one_or_none()


def _rank_label(rank: AmbassadorRank) -> str:
    return rank.value.replace("_", " ").title()


# ── Dashboard ─────────────────────────────────────────────────────────────────


@router.get("", response_model=AmbassadorResponse | None)
async def get_ambassador_dashboard(
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    ambassador = await _get_or_none(perms.id, db)
    if ambassador is None:
        return None
    return AmbassadorResponse.model_validate(ambassador)


# ── Leaderboard ───────────────────────────────────────────────────────────────


@router.get("/leaderboard", response_model=list[LeaderboardEntry])
async def get_leaderboard(
    limit: int = Query(20, ge=1, le=100),
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Ambassador).order_by(Ambassador.rewards_earned.desc()).limit(limit)
    )
    ambassadors = result.scalars().all()

    return [
        LeaderboardEntry(
            rank_position=idx + 1,
            user_id=amb.user_id,
            ambassador_rank=amb.rank,
            total_referrals=amb.total_referrals,
            tav_count=amb.tav_count,
            rewards_earned=float(amb.rewards_earned),
        )
        for idx, amb in enumerate(ambassadors)
    ]


# ── Referral link ─────────────────────────────────────────────────────────────


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


# ── Team (direct referrals, L1) ───────────────────────────────────────────────


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
    return [AmbassadorResponse.model_validate(t) for t in team_result.scalars().all()]


# ── Payout address ────────────────────────────────────────────────────────────


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


# ── Referrals list ────────────────────────────────────────────────────────────


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
    now = datetime.now(timezone.utc)

    for ref in referrals:
        user_result = await db.execute(select(User).where(User.id == ref.user_id))
        user = user_result.scalar_one_or_none()
        if user is None:
            continue

        sub_result = await db.execute(
            select(Subscription, Plan)
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(
                Subscription.user_id == ref.user_id,
                Subscription.status.in_(
                    [SubscriptionStatus.ACTIVE, SubscriptionStatus.EXPIRING_SOON]
                ),
            )
            .order_by(Subscription.created_at.asc())
            .limit(1)
        )
        row = sub_result.first()
        plan_name = row[1].name if row else None
        plan_price = float(row[1].price_usd) if row else None

        # Calculate subscription months for loyalty tracking
        sub_months = 0
        if row:
            start = row[0].created_at
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            sub_months = max(0, int((now - start).days / 30))

        items.append(
            ReferralItem(
                joined_at=ref.created_at,
                plan_name=plan_name,
                plan_price=plan_price,
                is_subscribed=user.is_subscribed,
                rank=ref.rank,
                subscription_months=sub_months,
            )
        )
    return items


# ── Earnings summary ──────────────────────────────────────────────────────────


@router.get("/earnings-summary", response_model=EarningsSummary)
async def get_earnings_summary(
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    next_month = now.month % 12 + 1
    next_year = now.year + (1 if now.month == 12 else 0)
    next_payout_date = f"{next_year}-{next_month:02d}-15"

    ambassador = await _get_or_none(perms.id, db)
    if ambassador is None:
        return EarningsSummary(
            total_commission_paid=0,
            total_commission_pending=0,
            current_month_commission=0,
            next_payout_date=next_payout_date,
            active_referral_count=0,
            tav_count=0,
            rank_progress=RankProgress(
                current_rank=AmbassadorRank.ASSOCIATE,
                next_rank=AmbassadorRank.BRONZE_LEADER,
                current_rank_label=_rank_label(AmbassadorRank.ASSOCIATE),
                next_rank_label=_rank_label(AmbassadorRank.BRONZE_LEADER),
                par_count=0,
                tav_count=0,
                par_required=3,
                tav_required=3,
                legs_required=0,
                current_depth=1,
                next_depth=2,
            ),
        )

    # Commission + bonus totals
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

    # Live PAR / TAV
    par = await get_par_count(ambassador.id, db)
    tav = await get_tav_count(ambassador.id, db)

    # Rank progress
    current_idx = RANK_ORDER.index(ambassador.rank)
    next_rank = (
        RANK_ORDER[current_idx + 1] if current_idx < len(RANK_ORDER) - 1 else None
    )
    next_criteria = RANK_CRITERIA.get(next_rank) if next_rank else None

    qualifying_legs = 0
    if next_criteria and next_criteria["legs"] > 0 and next_criteria["leg_rank"]:
        qualifying_legs = await count_qualifying_legs(
            ambassador.id, next_criteria["leg_rank"], db
        )

    rank_progress = RankProgress(
        current_rank=ambassador.rank,
        next_rank=next_rank,
        current_rank_label=_rank_label(ambassador.rank),
        next_rank_label=_rank_label(next_rank) if next_rank else None,
        par_count=par,
        tav_count=tav,
        par_required=next_criteria["par"] if next_criteria else None,
        tav_required=next_criteria["tav"] if next_criteria else None,
        legs_required=next_criteria["legs"] if next_criteria else None,
        leg_rank_required=(
            _rank_label(next_criteria["leg_rank"])
            if next_criteria and next_criteria["leg_rank"]
            else None
        ),
        qualifying_legs=qualifying_legs,
        current_depth=RANK_MAX_DEPTH.get(ambassador.rank, 1),
        next_depth=RANK_MAX_DEPTH.get(next_rank) if next_rank else None,
    )

    # Fast start eligibility
    from app.services.ambassador import FAST_START_PERIODS

    fast_start_eligible = ambassador.fast_start_claimed < FAST_START_PERIODS

    return EarningsSummary(
        total_commission_paid=total_paid,
        total_commission_pending=total_pending,
        current_month_commission=current_month_commission,
        next_payout_date=next_payout_date,
        active_referral_count=par,
        tav_count=tav,
        rank_progress=rank_progress,
        fast_start_eligible=fast_start_eligible,
        fast_start_period=ambassador.fast_start_claimed + 1,
    )


# ── Commissions ───────────────────────────────────────────────────────────────


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
    return PaginatedResponse(
        items=[CommissionResponse.model_validate(r) for r in result.scalars().all()],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


# ── Bonuses ───────────────────────────────────────────────────────────────────


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
    return PaginatedResponse(
        items=[BonusResponse.model_validate(r) for r in result.scalars().all()],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


# ── Payouts ───────────────────────────────────────────────────────────────────


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
    return PaginatedResponse(
        items=[PayoutResponse.model_validate(r) for r in result.scalars().all()],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


# ── Travel incentives ─────────────────────────────────────────────────────────


@router.get("/travel", response_model=list[TravelIncentiveResponse])
async def get_travel_incentives(
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    ambassador = await _get_or_none(perms.id, db)
    if ambassador is None:
        return []

    result = await db.execute(
        select(AmbassadorTravelIncentive)
        .where(AmbassadorTravelIncentive.ambassador_id == ambassador.id)
        .order_by(AmbassadorTravelIncentive.qualification_start.desc())
    )
    return [TravelIncentiveResponse.model_validate(t) for t in result.scalars().all()]


# ── Training ──────────────────────────────────────────────────────────────────


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
            rank=m["rank"],
            duration_min=m["duration_min"],
            completed=m["key"] in completed_map,
            completed_at=completed_map.get(m["key"]),
        )
        for m in TRAINING_MODULES
    ]
    return TrainingResponse(
        modules=modules,
        completed_count=len(completed_map),
        total_count=len(TRAINING_MODULES),
    )


# ── Territory ─────────────────────────────────────────────────────────────────


@router.get("/territory", response_model=TerritoryResponse | None)
async def get_territory(
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    ambassador = await _get_or_none(perms.id, db)
    if ambassador is None or ambassador.territory_id is None:
        return None

    result = await db.execute(
        select(AmbassadorTerritory).where(
            AmbassadorTerritory.id == ambassador.territory_id
        )
    )
    territory = result.scalar_one_or_none()
    if territory is None:
        return None
    return TerritoryResponse.model_validate(territory)
