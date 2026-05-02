"""Admin ambassadors router — V2: ranks, commissions, bonuses, payouts, pool, travel."""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.middleware.audit import log_audit
from app.middleware.permissions import RequireAdmin, UserPermissions
from app.models.ambassador import Ambassador
from app.models.ambassador_programs import (
    AmbassadorActivityLog,
    AmbassadorBonus,
    AmbassadorCommission,
    AmbassadorPayout,
    AmbassadorTrainingCompletion,
    AmbassadorTravelIncentive,
    LeadershipRevenuePool,
)
from app.models.enums import (
    ActivityEventType,
    AmbassadorRank,
    AmbassadorStatus,
    BonusType,
    CommissionStatus,
    PayoutStatus,
    TravelStatus,
)
from app.models.schemas.ambassador import (
    ActivityLogResponse,
    AdminAmbassadorResponse,
    AdminBonusResponse,
    AdminCommissionResponse,
    AdminPayoutResponse,
    AdminTravelResponse,
    AmbassadorAnalyticsResponse,
    AmbassadorResponse,
    AwardTravelRequest,
    BonusResponse,
    BulkCommissionApproveRequest,
    BulkPayoutUpdateRequest,
    CreateManualBonusRequest,
    CreatePayoutRequest,
    DownlineNode,
    DownlineResponse,
    LeadershipPoolResponse,
    PayoutResponse,
    PromoteAmbassadorRequest,
    RankDistributionItem,
    RecalculateCommissionRequest,
    RunMonthlyCycleRequest,
    TopEarnerItem,
    TrainingCompletionResponse,
    TravelIncentiveResponse,
    UpdateAmbassadorRequest,
    UpdateBonusRequest,
    UpdateCommissionRequest,
    UpdatePayoutRequest,
    UpdateTravelRequest,
)
from app.models.schemas.common import PaginatedResponse
from app.models.user import User
from app.services.ambassador import (
    RANK_ADVANCEMENT_BONUSES,
    TRAVEL_DESTINATIONS,
    calculate_commission_for_month,
    calculate_leadership_pool,
    evaluate_and_update_rank,
    get_downline_by_level,
    log_activity,
    run_monthly_ambassador_cycle,
)

router = APIRouter()


def _mask_email(email: Optional[str]) -> Optional[str]:
    if not email or "@" not in email:
        return email
    local, domain = email.split("@", 1)
    masked_local = local[:3] + "***" if len(local) > 3 else local + "***"
    return f"{masked_local}@{domain}"


async def _get_ambassador_or_404(
    ambassador_id: uuid.UUID, db: AsyncSession
) -> Ambassador:
    result = await db.execute(select(Ambassador).where(Ambassador.id == ambassador_id))
    amb = result.scalar_one_or_none()
    if amb is None:
        raise HTTPException(status_code=404, detail="Ambassador not found")
    return amb


async def _enrich_ambassador(
    amb: Ambassador, db: AsyncSession
) -> AdminAmbassadorResponse:
    user_result = await db.execute(select(User).where(User.id == amb.user_id))
    user = user_result.scalar_one_or_none()
    return AdminAmbassadorResponse(
        id=amb.id,
        user_id=amb.user_id,
        masked_email=_mask_email(user.email if user else None),
        rank=amb.rank,
        status=amb.status,
        par_count=amb.par_count,
        tav_count=amb.tav_count,
        total_referrals=amb.total_referrals,
        rewards_earned=float(amb.rewards_earned),
        payout_address=amb.payout_address,
        rank_achieved_at=amb.rank_achieved_at,
        admin_notes=amb.admin_notes,
        fast_start_claimed=amb.fast_start_claimed,
        created_at=amb.created_at,
    )


# ── IMPORTANT: All static GET routes are defined BEFORE parameterized routes ──
# FastAPI matches routes top-down. /payouts, /pool, /activity etc must come
# before /{ambassador_id} to avoid 422 UUID validation errors.


# ── List ambassadors ──────────────────────────────────────────────────────────


@router.get("/ambassadors", response_model=PaginatedResponse)
async def admin_list_ambassadors(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    rank: Optional[AmbassadorRank] = None,
    status: Optional[AmbassadorStatus] = None,
    min_referrals: Optional[int] = None,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    query = select(Ambassador).order_by(Ambassador.created_at.desc())
    count_query = select(func.count(Ambassador.id))

    if rank:
        query = query.where(Ambassador.rank == rank)
        count_query = count_query.where(Ambassador.rank == rank)
    if status:
        query = query.where(Ambassador.status == status)
        count_query = count_query.where(Ambassador.status == status)
    if min_referrals is not None:
        query = query.where(Ambassador.total_referrals >= min_referrals)
        count_query = count_query.where(Ambassador.total_referrals >= min_referrals)

    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    ambassadors = result.scalars().all()

    items = [await _enrich_ambassador(amb, db) for amb in ambassadors]

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


# ── Global activity feed ──────────────────────────────────────────────────────


@router.get("/ambassadors/activity", response_model=PaginatedResponse)
async def admin_activity_feed(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    ambassador_id: Optional[uuid.UUID] = None,
    event_type: Optional[ActivityEventType] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    """Global activity feed — every ambassador event, newest first."""
    query = select(AmbassadorActivityLog).order_by(
        AmbassadorActivityLog.created_at.desc()
    )
    count_query = select(func.count(AmbassadorActivityLog.id))

    if ambassador_id:
        query = query.where(AmbassadorActivityLog.ambassador_id == ambassador_id)
        count_query = count_query.where(
            AmbassadorActivityLog.ambassador_id == ambassador_id
        )
    if event_type:
        query = query.where(AmbassadorActivityLog.event_type == event_type)
        count_query = count_query.where(AmbassadorActivityLog.event_type == event_type)
    if date_from:
        query = query.where(AmbassadorActivityLog.created_at >= date_from)
        count_query = count_query.where(AmbassadorActivityLog.created_at >= date_from)
    if date_to:
        query = query.where(AmbassadorActivityLog.created_at <= date_to)
        count_query = count_query.where(AmbassadorActivityLog.created_at <= date_to)

    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    logs = result.scalars().all()

    return PaginatedResponse(
        items=[ActivityLogResponse.model_validate(log) for log in logs],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


# ── Global commissions list ───────────────────────────────────────────────────


@router.get("/ambassadors/commissions", response_model=PaginatedResponse)
async def admin_list_commissions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    ambassador_id: Optional[uuid.UUID] = None,
    month: Optional[int] = None,
    year: Optional[int] = None,
    status: Optional[CommissionStatus] = None,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    query = select(AmbassadorCommission).order_by(
        AmbassadorCommission.year.desc(), AmbassadorCommission.month.desc()
    )
    count_query = select(func.count(AmbassadorCommission.id))

    if ambassador_id:
        query = query.where(AmbassadorCommission.ambassador_id == ambassador_id)
        count_query = count_query.where(
            AmbassadorCommission.ambassador_id == ambassador_id
        )
    if month:
        query = query.where(AmbassadorCommission.month == month)
        count_query = count_query.where(AmbassadorCommission.month == month)
    if year:
        query = query.where(AmbassadorCommission.year == year)
        count_query = count_query.where(AmbassadorCommission.year == year)
    if status:
        query = query.where(AmbassadorCommission.status == status)
        count_query = count_query.where(AmbassadorCommission.status == status)

    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    commissions = result.scalars().all()

    items = []
    for c in commissions:
        amb_result = await db.execute(
            select(Ambassador).where(Ambassador.id == c.ambassador_id)
        )
        amb = amb_result.scalar_one_or_none()
        user_result = (
            await db.execute(select(User).where(User.id == amb.user_id))
            if amb
            else None
        )
        user = user_result.scalar_one_or_none() if user_result else None
        items.append(
            AdminCommissionResponse(
                id=c.id,
                ambassador_id=c.ambassador_id,
                masked_email=_mask_email(user.email if user else None),
                rank=amb.rank if amb else None,
                month=c.month,
                year=c.year,
                active_referral_count=c.active_referral_count,
                tav_count=c.tav_count,
                commission_amount=float(c.commission_amount),
                level_breakdown=c.level_breakdown,
                generational_override=float(c.generational_override),
                status=c.status,
                paid_at=c.paid_at,
                created_at=c.created_at,
            )
        )

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


# ── Global bonuses list ───────────────────────────────────────────────────────


@router.get("/ambassadors/bonuses", response_model=PaginatedResponse)
async def admin_list_bonuses(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    ambassador_id: Optional[uuid.UUID] = None,
    bonus_type: Optional[BonusType] = None,
    status: Optional[CommissionStatus] = None,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    query = select(AmbassadorBonus).order_by(AmbassadorBonus.created_at.desc())
    count_query = select(func.count(AmbassadorBonus.id))

    if ambassador_id:
        query = query.where(AmbassadorBonus.ambassador_id == ambassador_id)
        count_query = count_query.where(AmbassadorBonus.ambassador_id == ambassador_id)
    if bonus_type:
        query = query.where(AmbassadorBonus.bonus_type == bonus_type)
        count_query = count_query.where(AmbassadorBonus.bonus_type == bonus_type)
    if status:
        query = query.where(AmbassadorBonus.status == status)
        count_query = count_query.where(AmbassadorBonus.status == status)

    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    bonuses = result.scalars().all()

    items = []
    for b in bonuses:
        amb_result = await db.execute(
            select(Ambassador).where(Ambassador.id == b.ambassador_id)
        )
        amb = amb_result.scalar_one_or_none()
        user_result = (
            await db.execute(select(User).where(User.id == amb.user_id))
            if amb
            else None
        )
        user = user_result.scalar_one_or_none() if user_result else None
        items.append(
            AdminBonusResponse(
                id=b.id,
                ambassador_id=b.ambassador_id,
                masked_email=_mask_email(user.email if user else None),
                rank=amb.rank if amb else None,
                bonus_type=b.bonus_type,
                amount=float(b.amount),
                period=b.period,
                description=b.description,
                status=b.status,
                paid_at=b.paid_at,
                created_at=b.created_at,
            )
        )

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


# ── Global travel list ────────────────────────────────────────────────────────


@router.get("/ambassadors/travel", response_model=PaginatedResponse)
async def admin_list_all_travel(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[TravelStatus] = None,
    rank_required: Optional[AmbassadorRank] = None,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    query = select(AmbassadorTravelIncentive).order_by(
        AmbassadorTravelIncentive.created_at.desc()
    )
    count_query = select(func.count(AmbassadorTravelIncentive.id))

    if status:
        query = query.where(AmbassadorTravelIncentive.status == status)
        count_query = count_query.where(AmbassadorTravelIncentive.status == status)
    if rank_required:
        query = query.where(AmbassadorTravelIncentive.rank_required == rank_required)
        count_query = count_query.where(
            AmbassadorTravelIncentive.rank_required == rank_required
        )

    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    incentives = result.scalars().all()

    items = []
    for t in incentives:
        amb_result = await db.execute(
            select(Ambassador).where(Ambassador.id == t.ambassador_id)
        )
        amb = amb_result.scalar_one_or_none()
        user_result = (
            await db.execute(select(User).where(User.id == amb.user_id))
            if amb
            else None
        )
        user = user_result.scalar_one_or_none() if user_result else None
        items.append(
            AdminTravelResponse(
                id=t.id,
                ambassador_id=t.ambassador_id,
                masked_email=_mask_email(user.email if user else None),
                rank=amb.rank if amb else None,
                destination=t.destination,
                rank_required=t.rank_required,
                qualification_start=t.qualification_start,
                qualification_end=t.qualification_end,
                status=t.status,
                awarded_at=t.awarded_at,
                admin_notes=t.admin_notes,
            )
        )

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


# ── Global training completions list ─────────────────────────────────────────


@router.get("/ambassadors/training", response_model=PaginatedResponse)
async def admin_list_all_training(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    ambassador_id: Optional[uuid.UUID] = None,
    module_name: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    query = select(AmbassadorTrainingCompletion).order_by(
        AmbassadorTrainingCompletion.completed_at.desc()
    )
    count_query = select(func.count(AmbassadorTrainingCompletion.id))

    if ambassador_id:
        query = query.where(AmbassadorTrainingCompletion.ambassador_id == ambassador_id)
        count_query = count_query.where(
            AmbassadorTrainingCompletion.ambassador_id == ambassador_id
        )
    if module_name:
        query = query.where(AmbassadorTrainingCompletion.module_name == module_name)
        count_query = count_query.where(
            AmbassadorTrainingCompletion.module_name == module_name
        )

    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    completions = result.scalars().all()

    items = []
    for tc in completions:
        amb_result = await db.execute(
            select(Ambassador).where(Ambassador.id == tc.ambassador_id)
        )
        amb = amb_result.scalar_one_or_none()
        user_result = (
            await db.execute(select(User).where(User.id == amb.user_id))
            if amb
            else None
        )
        user = user_result.scalar_one_or_none() if user_result else None
        items.append(
            TrainingCompletionResponse(
                id=tc.id,
                ambassador_id=tc.ambassador_id,
                masked_email=_mask_email(user.email if user else None),
                rank=amb.rank if amb else None,
                module_name=tc.module_name,
                completed_at=tc.completed_at,
                notes=tc.notes,
            )
        )

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


# ── Global payouts (enriched) ─────────────────────────────────────────────────


@router.get("/ambassadors/payouts", response_model=PaginatedResponse)
async def admin_list_payouts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[PayoutStatus] = None,
    ambassador_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    query = select(AmbassadorPayout).order_by(AmbassadorPayout.created_at.desc())
    count_query = select(func.count(AmbassadorPayout.id))

    if status:
        query = query.where(AmbassadorPayout.status == status)
        count_query = count_query.where(AmbassadorPayout.status == status)
    if ambassador_id:
        query = query.where(AmbassadorPayout.ambassador_id == ambassador_id)
        count_query = count_query.where(AmbassadorPayout.ambassador_id == ambassador_id)

    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    payouts = result.scalars().all()

    items = []
    for p in payouts:
        amb_result = await db.execute(
            select(Ambassador).where(Ambassador.id == p.ambassador_id)
        )
        amb = amb_result.scalar_one_or_none()
        user_result = (
            await db.execute(select(User).where(User.id == amb.user_id))
            if amb
            else None
        )
        user = user_result.scalar_one_or_none() if user_result else None
        items.append(
            AdminPayoutResponse(
                id=p.id,
                ambassador_id=p.ambassador_id,
                masked_email=_mask_email(user.email if user else None),
                rank=amb.rank if amb else None,
                total_amount=float(p.total_amount),
                status=p.status,
                payout_address=p.payout_address,
                admin_notes=p.admin_notes,
                paid_at=p.paid_at,
                commission_ids=p.commission_ids,
                bonus_ids=p.bonus_ids,
                created_at=p.created_at,
            )
        )

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


# ── Analytics dashboard ───────────────────────────────────────────────────────


@router.get("/ambassadors/analytics", response_model=AmbassadorAnalyticsResponse)
async def admin_analytics(
    month: int = Query(..., ge=1, le=12),
    year: int = Query(..., ge=2020),
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    # Rank distribution
    rank_dist_result = await db.execute(
        select(Ambassador.rank, func.count(Ambassador.id)).group_by(Ambassador.rank)
    )
    rank_distribution = [
        RankDistributionItem(rank=r, count=c) for r, c in rank_dist_result.all()
    ]

    # Commissions this month
    comm_result = await db.execute(
        select(func.sum(AmbassadorCommission.commission_amount)).where(
            AmbassadorCommission.month == month,
            AmbassadorCommission.year == year,
        )
    )
    total_commissions = float(comm_result.scalar() or 0)

    # Bonuses this month (by created_at month/year approximation via period)
    bonus_result = await db.execute(
        select(func.sum(AmbassadorBonus.amount)).where(
            AmbassadorBonus.period == f"{year}-{month:02d}",
        )
    )
    total_bonuses = float(bonus_result.scalar() or 0)

    # New ambassadors this month
    new_amb_result = await db.execute(
        select(func.count(Ambassador.id)).where(
            func.strftime("%Y-%m", Ambassador.created_at) == f"{year}-{month:02d}"
        )
    )
    new_ambassadors = new_amb_result.scalar() or 0

    # New referrals this month
    new_ref_result = await db.execute(
        select(func.count(Ambassador.id)).where(
            Ambassador.referred_by.is_not(None),
            func.strftime("%Y-%m", Ambassador.created_at) == f"{year}-{month:02d}",
        )
    )
    new_referrals = new_ref_result.scalar() or 0

    # Pool this month
    pool_result = await db.execute(
        select(LeadershipRevenuePool).where(
            LeadershipRevenuePool.month == month,
            LeadershipRevenuePool.year == year,
        )
    )
    pool = pool_result.scalar_one_or_none()
    pool_amount = float(pool.total_pool_amount) if pool else 0.0

    # Pending payouts
    pending_result = await db.execute(
        select(
            func.count(AmbassadorPayout.id),
            func.sum(AmbassadorPayout.total_amount),
        ).where(AmbassadorPayout.status == PayoutStatus.PENDING)
    )
    pending_row = pending_result.one()
    pending_count = pending_row[0] or 0
    pending_amount = float(pending_row[1] or 0)

    # Top earners this month
    top_result = await db.execute(
        select(
            AmbassadorCommission.ambassador_id, AmbassadorCommission.commission_amount
        )
        .where(
            AmbassadorCommission.month == month,
            AmbassadorCommission.year == year,
        )
        .order_by(AmbassadorCommission.commission_amount.desc())
        .limit(10)
    )
    top_earners = []
    for amb_id, amount in top_result.all():
        amb_result = await db.execute(select(Ambassador).where(Ambassador.id == amb_id))
        amb = amb_result.scalar_one_or_none()
        user_result = (
            await db.execute(select(User).where(User.id == amb.user_id))
            if amb
            else None
        )
        user = user_result.scalar_one_or_none() if user_result else None
        top_earners.append(
            TopEarnerItem(
                ambassador_id=amb_id,
                masked_email=_mask_email(user.email if user else None),
                rank=amb.rank if amb else AmbassadorRank.ASSOCIATE,
                commission_amount=float(amount),
            )
        )

    return AmbassadorAnalyticsResponse(
        month=month,
        year=year,
        rank_distribution=rank_distribution,
        total_commissions_this_month=total_commissions,
        total_bonuses_this_month=total_bonuses,
        new_ambassadors_this_month=new_ambassadors,
        new_referrals_this_month=new_referrals,
        pool_amount_this_month=pool_amount,
        pending_payouts_count=pending_count,
        pending_payouts_amount=pending_amount,
        top_earners=top_earners,
    )


# ── Leadership pool list ──────────────────────────────────────────────────────


@router.get("/ambassadors/pool", response_model=PaginatedResponse)
async def admin_list_pools(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    query = select(LeadershipRevenuePool).order_by(
        LeadershipRevenuePool.year.desc(),
        LeadershipRevenuePool.month.desc(),
    )
    count_query = select(func.count(LeadershipRevenuePool.id))

    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    pools = result.scalars().all()

    return PaginatedResponse(
        items=[LeadershipPoolResponse.model_validate(p) for p in pools],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


# ── Bulk operations ───────────────────────────────────────────────────────────


@router.post("/ambassadors/evaluate-all")
async def admin_evaluate_all(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    """Re-evaluate rank for every ambassador."""
    result = await db.execute(select(Ambassador))
    ambassadors = result.scalars().all()
    changed = 0
    for amb in ambassadors:
        new_rank = await evaluate_and_update_rank(amb, db)
        if new_rank:
            changed += 1
    await db.flush()
    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_evaluate_all_ranks",
        resource_type="ambassador",
        resource_id="all",
        details={"total": len(ambassadors), "changed": changed},
        ip_address=request.client.host if request.client else None,
    )
    return {"total": len(ambassadors), "rank_changed": changed}


@router.post("/ambassadors/payouts/bulk-update")
async def admin_bulk_update_payouts(
    data: BulkPayoutUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    now = datetime.now(timezone.utc)
    updated = 0
    for payout_id in data.payout_ids:
        result = await db.execute(
            select(AmbassadorPayout).where(AmbassadorPayout.id == payout_id)
        )
        payout = result.scalar_one_or_none()
        if payout is None:
            continue
        payout.status = data.status
        if data.admin_notes:
            payout.admin_notes = data.admin_notes
        if data.status == PayoutStatus.PAID:
            payout.paid_at = now
        updated += 1
        await log_activity(
            payout.ambassador_id,
            ActivityEventType.PAYOUT_STATUS_CHANGED,
            f"Payout status changed to {data.status.value} (bulk update)",
            db,
            amount=float(payout.total_amount),
            related_id=str(payout.id),
            actor=f"admin:{admin_perms.user.id}",
        )

    await db.flush()
    return {"updated": updated}


@router.post("/ambassadors/commissions/bulk-approve")
async def admin_bulk_approve_commissions(
    data: BulkCommissionApproveRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    now = datetime.now(timezone.utc)
    approved = 0
    for cid in data.commission_ids:
        result = await db.execute(
            select(AmbassadorCommission).where(AmbassadorCommission.id == cid)
        )
        comm = result.scalar_one_or_none()
        if comm and comm.status == CommissionStatus.PENDING:
            comm.status = CommissionStatus.PAID
            comm.paid_at = now
            approved += 1
            await log_activity(
                comm.ambassador_id,
                ActivityEventType.COMMISSION_PAID,
                f"Commission {comm.month}/{comm.year} approved (${float(comm.commission_amount):,.2f})",
                db,
                amount=float(comm.commission_amount),
                related_id=str(comm.id),
                actor=f"admin:{admin_perms.user.id}",
            )

    await db.flush()
    return {"approved": approved}


# ── Single payout detail ──────────────────────────────────────────────────────


@router.get("/ambassadors/payouts/{payout_id}", response_model=AdminPayoutResponse)
async def admin_get_payout(
    payout_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    result = await db.execute(
        select(AmbassadorPayout).where(AmbassadorPayout.id == payout_id)
    )
    payout = result.scalar_one_or_none()
    if payout is None:
        raise HTTPException(status_code=404, detail="Payout not found")

    amb_result = await db.execute(
        select(Ambassador).where(Ambassador.id == payout.ambassador_id)
    )
    amb = amb_result.scalar_one_or_none()
    user_result = (
        await db.execute(select(User).where(User.id == amb.user_id)) if amb else None
    )
    user = user_result.scalar_one_or_none() if user_result else None

    return AdminPayoutResponse(
        id=payout.id,
        ambassador_id=payout.ambassador_id,
        masked_email=_mask_email(user.email if user else None),
        rank=amb.rank if amb else None,
        total_amount=float(payout.total_amount),
        status=payout.status,
        payout_address=payout.payout_address,
        admin_notes=payout.admin_notes,
        paid_at=payout.paid_at,
        commission_ids=payout.commission_ids,
        bonus_ids=payout.bonus_ids,
        created_at=payout.created_at,
    )


# ── Single ambassador ─────────────────────────────────────────────────────────


@router.get("/ambassadors/{ambassador_id}", response_model=AdminAmbassadorResponse)
async def admin_get_ambassador(
    ambassador_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    amb = await _get_ambassador_or_404(ambassador_id, db)
    return await _enrich_ambassador(amb, db)


# ── Update ambassador status / admin notes ────────────────────────────────────


@router.patch("/ambassadors/{ambassador_id}", response_model=AdminAmbassadorResponse)
async def admin_update_ambassador(
    ambassador_id: uuid.UUID,
    data: UpdateAmbassadorRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    amb = await _get_ambassador_or_404(ambassador_id, db)

    changes = {}
    if data.status is not None and data.status != amb.status:
        old_status = amb.status
        amb.status = data.status
        changes["status"] = {"from": old_status.value, "to": data.status.value}
        await log_activity(
            ambassador_id,
            ActivityEventType.STATUS_CHANGED,
            f"Ambassador status changed: {old_status.value} → {data.status.value}",
            db,
            actor=f"admin:{admin_perms.user.id}",
        )
    if data.admin_notes is not None:
        amb.admin_notes = data.admin_notes
        changes["admin_notes"] = "updated"
        await log_activity(
            ambassador_id,
            ActivityEventType.ADMIN_NOTE_ADDED,
            "Admin note updated",
            db,
            actor=f"admin:{admin_perms.user.id}",
        )
    if data.payout_address is not None:
        amb.payout_address = data.payout_address
        changes["payout_address"] = "updated"
    if data.rank_achieved_at is not None:
        amb.rank_achieved_at = data.rank_achieved_at
        changes["rank_achieved_at"] = "updated"

    await db.flush()
    await db.refresh(amb)

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_update_ambassador",
        resource_type="ambassador",
        resource_id=str(ambassador_id),
        details=changes,
        ip_address=request.client.host if request.client else None,
    )

    return await _enrich_ambassador(amb, db)


# ── Per-ambassador activity log ───────────────────────────────────────────────


@router.get(
    "/ambassadors/{ambassador_id}/activity",
    response_model=PaginatedResponse,
)
async def admin_ambassador_activity(
    ambassador_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    event_type: Optional[ActivityEventType] = None,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    await _get_ambassador_or_404(ambassador_id, db)

    query = (
        select(AmbassadorActivityLog)
        .where(AmbassadorActivityLog.ambassador_id == ambassador_id)
        .order_by(AmbassadorActivityLog.created_at.desc())
    )
    count_query = select(func.count(AmbassadorActivityLog.id)).where(
        AmbassadorActivityLog.ambassador_id == ambassador_id
    )

    if event_type:
        query = query.where(AmbassadorActivityLog.event_type == event_type)
        count_query = count_query.where(AmbassadorActivityLog.event_type == event_type)

    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    logs = result.scalars().all()

    return PaginatedResponse(
        items=[ActivityLogResponse.model_validate(log) for log in logs],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


# ── Downline tree ─────────────────────────────────────────────────────────────


@router.get("/ambassadors/{ambassador_id}/downline", response_model=DownlineResponse)
async def admin_ambassador_downline(
    ambassador_id: uuid.UUID,
    depth: int = Query(5, ge=1, le=10),
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    await _get_ambassador_or_404(ambassador_id, db)
    levels = await get_downline_by_level(ambassador_id, depth, db)

    nodes_by_level: dict[int, list[DownlineNode]] = {}
    total_nodes = 0

    for level, members in levels.items():
        level_nodes = []
        for amb in members:
            user_result = await db.execute(select(User).where(User.id == amb.user_id))
            user = user_result.scalar_one_or_none()
            # Count direct referrals for this node
            ref_count_result = await db.execute(
                select(func.count(Ambassador.id)).where(
                    Ambassador.referred_by == amb.id
                )
            )
            ref_count = ref_count_result.scalar() or 0
            level_nodes.append(
                DownlineNode(
                    ambassador_id=amb.id,
                    user_id=amb.user_id,
                    masked_email=_mask_email(user.email if user else None),
                    rank=amb.rank,
                    par_count=amb.par_count,
                    tav_count=amb.tav_count,
                    depth=level,
                    direct_referral_count=ref_count,
                )
            )
            total_nodes += 1
        nodes_by_level[level] = level_nodes

    return DownlineResponse(
        ambassador_id=ambassador_id,
        total_nodes=total_nodes,
        nodes_by_level=nodes_by_level,
    )


# ── Promote / evaluate rank ───────────────────────────────────────────────────


@router.post("/ambassadors/{ambassador_id}/promote", response_model=AmbassadorResponse)
async def admin_promote_ambassador(
    ambassador_id: uuid.UUID,
    data: PromoteAmbassadorRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    ambassador = await _get_ambassador_or_404(ambassador_id, db)

    old_rank = ambassador.rank
    ambassador.rank = data.rank
    ambassador.rank_achieved_at = datetime.now(timezone.utc)

    promo_amount = RANK_ADVANCEMENT_BONUSES.get(data.rank, 0)
    if promo_amount > 0:
        bonus = AmbassadorBonus(
            id=uuid.uuid4(),
            ambassador_id=ambassador.id,
            bonus_type=BonusType.RANK_ADVANCEMENT,
            amount=promo_amount,
            period=data.rank.value,
            description=f"Admin promotion: {old_rank.value} → {data.rank.value}",
        )
        db.add(bonus)

    await log_activity(
        ambassador_id,
        ActivityEventType.RANK_CHANGED,
        f"Rank manually set: {old_rank.value.replace('_', ' ').title()} → {data.rank.value.replace('_', ' ').title()} by admin",
        db,
        actor=f"admin:{admin_perms.user.id}",
    )

    await db.flush()
    await db.refresh(ambassador)

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_promote_ambassador",
        resource_type="ambassador",
        resource_id=str(ambassador_id),
        details={"old_rank": old_rank.value, "new_rank": data.rank.value},
        ip_address=request.client.host if request.client else None,
    )

    return AmbassadorResponse.model_validate(ambassador)


@router.post("/ambassadors/{ambassador_id}/evaluate-rank")
async def admin_evaluate_rank(
    ambassador_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    ambassador = await _get_ambassador_or_404(ambassador_id, db)

    old_rank = ambassador.rank
    new_rank = await evaluate_and_update_rank(ambassador, db)
    await db.flush()

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_evaluate_rank",
        resource_type="ambassador",
        resource_id=str(ambassador_id),
        details={"old_rank": old_rank.value, "new_rank": (new_rank or old_rank).value},
        ip_address=request.client.host if request.client else None,
    )

    return {
        "ambassador_id": str(ambassador_id),
        "old_rank": old_rank.value,
        "new_rank": (new_rank or old_rank).value,
        "rank_changed": new_rank is not None,
    }


# ── Commission override ───────────────────────────────────────────────────────


@router.patch(
    "/ambassadors/{ambassador_id}/commissions/{commission_id}",
    response_model=AdminCommissionResponse,
)
async def admin_update_commission(
    ambassador_id: uuid.UUID,
    commission_id: uuid.UUID,
    data: UpdateCommissionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    result = await db.execute(
        select(AmbassadorCommission).where(
            AmbassadorCommission.id == commission_id,
            AmbassadorCommission.ambassador_id == ambassador_id,
        )
    )
    commission = result.scalar_one_or_none()
    if commission is None:
        raise HTTPException(status_code=404, detail="Commission not found")

    changes = {}
    if data.status is not None:
        commission.status = data.status
        if data.status == CommissionStatus.PAID and commission.paid_at is None:
            commission.paid_at = datetime.now(timezone.utc)
        changes["status"] = data.status.value
    if data.commission_amount is not None:
        changes["amount_override"] = {
            "from": float(commission.commission_amount),
            "to": data.commission_amount,
        }
        commission.commission_amount = data.commission_amount

    await db.flush()
    await db.refresh(commission)

    if changes:
        await log_activity(
            ambassador_id,
            ActivityEventType.COMMISSION_PAID
            if data.status == CommissionStatus.PAID
            else ActivityEventType.COMMISSION_CALCULATED,
            f"Commission {commission.month}/{commission.year} updated by admin: {changes}",
            db,
            amount=float(commission.commission_amount),
            related_id=str(commission_id),
            actor=f"admin:{admin_perms.user.id}",
        )

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_update_commission",
        resource_type="ambassador_commission",
        resource_id=str(commission_id),
        details=changes,
        ip_address=request.client.host if request.client else None,
    )

    amb_result = await db.execute(
        select(Ambassador).where(Ambassador.id == ambassador_id)
    )
    amb = amb_result.scalar_one_or_none()
    user_result = (
        await db.execute(select(User).where(User.id == amb.user_id)) if amb else None
    )
    user = user_result.scalar_one_or_none() if user_result else None

    return AdminCommissionResponse(
        id=commission.id,
        ambassador_id=commission.ambassador_id,
        masked_email=_mask_email(user.email if user else None),
        rank=amb.rank if amb else None,
        month=commission.month,
        year=commission.year,
        active_referral_count=commission.active_referral_count,
        tav_count=commission.tav_count,
        commission_amount=float(commission.commission_amount),
        level_breakdown=commission.level_breakdown,
        generational_override=float(commission.generational_override),
        status=commission.status,
        paid_at=commission.paid_at,
        created_at=commission.created_at,
    )


# ── Bonus override ────────────────────────────────────────────────────────────


@router.patch(
    "/ambassadors/{ambassador_id}/bonuses/{bonus_id}",
    response_model=AdminBonusResponse,
)
async def admin_update_bonus(
    ambassador_id: uuid.UUID,
    bonus_id: uuid.UUID,
    data: UpdateBonusRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    result = await db.execute(
        select(AmbassadorBonus).where(
            AmbassadorBonus.id == bonus_id,
            AmbassadorBonus.ambassador_id == ambassador_id,
        )
    )
    bonus = result.scalar_one_or_none()
    if bonus is None:
        raise HTTPException(status_code=404, detail="Bonus not found")

    changes = {}
    if data.status is not None:
        bonus.status = data.status
        if data.status == CommissionStatus.PAID and bonus.paid_at is None:
            bonus.paid_at = datetime.now(timezone.utc)
        changes["status"] = data.status.value
    if data.amount is not None:
        changes["amount"] = {"from": float(bonus.amount), "to": data.amount}
        bonus.amount = data.amount
    if data.description is not None:
        bonus.description = data.description

    await db.flush()
    await db.refresh(bonus)

    if changes:
        await log_activity(
            ambassador_id,
            ActivityEventType.BONUS_PAID
            if data.status == CommissionStatus.PAID
            else ActivityEventType.BONUS_AWARDED,
            f"Bonus updated by admin: {bonus.bonus_type.value} — {changes}",
            db,
            amount=float(bonus.amount),
            related_id=str(bonus_id),
            actor=f"admin:{admin_perms.user.id}",
        )

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_update_bonus",
        resource_type="ambassador_bonus",
        resource_id=str(bonus_id),
        details=changes,
        ip_address=request.client.host if request.client else None,
    )

    amb_result = await db.execute(
        select(Ambassador).where(Ambassador.id == ambassador_id)
    )
    amb = amb_result.scalar_one_or_none()
    user_result = (
        await db.execute(select(User).where(User.id == amb.user_id)) if amb else None
    )
    user = user_result.scalar_one_or_none() if user_result else None

    return AdminBonusResponse(
        id=bonus.id,
        ambassador_id=bonus.ambassador_id,
        masked_email=_mask_email(user.email if user else None),
        rank=amb.rank if amb else None,
        bonus_type=bonus.bonus_type,
        amount=float(bonus.amount),
        period=bonus.period,
        description=bonus.description,
        status=bonus.status,
        paid_at=bonus.paid_at,
        created_at=bonus.created_at,
    )


# ── Payout management ─────────────────────────────────────────────────────────


@router.post("/ambassadors/{ambassador_id}/payout", response_model=PayoutResponse)
async def admin_create_payout(
    ambassador_id: uuid.UUID,
    data: CreatePayoutRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    ambassador = await _get_ambassador_or_404(ambassador_id, db)

    now = datetime.now(timezone.utc)
    total_amount = 0.0

    commissions = []
    for cid in data.commission_ids:
        comm_result = await db.execute(
            select(AmbassadorCommission).where(
                AmbassadorCommission.id == cid,
                AmbassadorCommission.ambassador_id == ambassador_id,
            )
        )
        comm = comm_result.scalar_one_or_none()
        if comm:
            commissions.append(comm)
            total_amount += float(comm.commission_amount)

    bonuses = []
    for bid in data.bonus_ids:
        bonus_result = await db.execute(
            select(AmbassadorBonus).where(
                AmbassadorBonus.id == bid,
                AmbassadorBonus.ambassador_id == ambassador_id,
            )
        )
        bonus = bonus_result.scalar_one_or_none()
        if bonus:
            bonuses.append(bonus)
            total_amount += float(bonus.amount)

    payout_address = data.payout_address or ambassador.payout_address

    payout = AmbassadorPayout(
        id=uuid.uuid4(),
        ambassador_id=ambassador_id,
        total_amount=total_amount,
        commission_ids=[str(c.id) for c in commissions],
        bonus_ids=[str(b.id) for b in bonuses],
        payout_address=payout_address,
        admin_notes=data.admin_notes,
        processed_by=admin_perms.user.id,
    )
    db.add(payout)

    for comm in commissions:
        comm.status = CommissionStatus.PAID
        comm.paid_at = now
    for bonus in bonuses:
        bonus.status = CommissionStatus.PAID
        bonus.paid_at = now

    await db.flush()
    await db.refresh(payout)

    await log_activity(
        ambassador_id,
        ActivityEventType.PAYOUT_CREATED,
        f"Payout created: ${total_amount:,.2f} ({len(commissions)} commissions, {len(bonuses)} bonuses)",
        db,
        amount=total_amount,
        related_id=str(payout.id),
        actor=f"admin:{admin_perms.user.id}",
    )

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_create_payout",
        resource_type="ambassador_payout",
        resource_id=str(payout.id),
        details={"ambassador_id": str(ambassador_id), "total_amount": total_amount},
        ip_address=request.client.host if request.client else None,
    )

    return PayoutResponse.model_validate(payout)


@router.patch("/ambassadors/payouts/{payout_id}", response_model=PayoutResponse)
async def admin_update_payout(
    payout_id: uuid.UUID,
    data: UpdatePayoutRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    result = await db.execute(
        select(AmbassadorPayout).where(AmbassadorPayout.id == payout_id)
    )
    payout = result.scalar_one_or_none()
    if payout is None:
        raise HTTPException(status_code=404, detail="Payout not found")

    old_status = payout.status
    payout.status = data.status
    if data.admin_notes is not None:
        payout.admin_notes = data.admin_notes

    now = datetime.now(timezone.utc)

    if data.status == PayoutStatus.PAID:
        payout.paid_at = now
        for cid_str in payout.commission_ids or []:
            try:
                cid = uuid.UUID(cid_str)
                comm_result = await db.execute(
                    select(AmbassadorCommission).where(AmbassadorCommission.id == cid)
                )
                comm = comm_result.scalar_one_or_none()
                if comm:
                    comm.status = CommissionStatus.PAID
                    comm.paid_at = now
            except (ValueError, AttributeError):
                pass

        for bid_str in payout.bonus_ids or []:
            try:
                bid = uuid.UUID(bid_str)
                bonus_result = await db.execute(
                    select(AmbassadorBonus).where(AmbassadorBonus.id == bid)
                )
                bonus = bonus_result.scalar_one_or_none()
                if bonus:
                    bonus.status = CommissionStatus.PAID
                    bonus.paid_at = now
            except (ValueError, AttributeError):
                pass

    await db.flush()
    await db.refresh(payout)

    await log_activity(
        payout.ambassador_id,
        ActivityEventType.PAYOUT_STATUS_CHANGED,
        f"Payout status: {old_status.value} → {data.status.value} (${float(payout.total_amount):,.2f})",
        db,
        amount=float(payout.total_amount),
        related_id=str(payout_id),
        actor=f"admin:{admin_perms.user.id}",
    )

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_update_payout",
        resource_type="ambassador_payout",
        resource_id=str(payout_id),
        details={"status": data.status.value},
        ip_address=request.client.host if request.client else None,
    )

    return PayoutResponse.model_validate(payout)


@router.delete("/ambassadors/{ambassador_id}/payouts/{payout_id}")
async def admin_cancel_payout(
    ambassador_id: uuid.UUID,
    payout_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    """Cancel a pending payout and return commissions/bonuses to pending status."""
    result = await db.execute(
        select(AmbassadorPayout).where(
            AmbassadorPayout.id == payout_id,
            AmbassadorPayout.ambassador_id == ambassador_id,
        )
    )
    payout = result.scalar_one_or_none()
    if payout is None:
        raise HTTPException(status_code=404, detail="Payout not found")
    if payout.status == PayoutStatus.PAID:
        raise HTTPException(
            status_code=400, detail="Cannot cancel an already paid payout"
        )

    # Return commissions to pending
    for cid_str in payout.commission_ids or []:
        try:
            cid = uuid.UUID(cid_str)
            comm_result = await db.execute(
                select(AmbassadorCommission).where(AmbassadorCommission.id == cid)
            )
            comm = comm_result.scalar_one_or_none()
            if comm and comm.status == CommissionStatus.PAID:
                comm.status = CommissionStatus.PENDING
                comm.paid_at = None
        except (ValueError, AttributeError):
            pass

    for bid_str in payout.bonus_ids or []:
        try:
            bid = uuid.UUID(bid_str)
            bonus_result = await db.execute(
                select(AmbassadorBonus).where(AmbassadorBonus.id == bid)
            )
            bonus = bonus_result.scalar_one_or_none()
            if bonus and bonus.status == CommissionStatus.PAID:
                bonus.status = CommissionStatus.PENDING
                bonus.paid_at = None
        except (ValueError, AttributeError):
            pass

    await log_activity(
        ambassador_id,
        ActivityEventType.PAYOUT_CANCELLED,
        f"Payout cancelled (${float(payout.total_amount):,.2f}) — commissions/bonuses returned to pending",
        db,
        amount=float(payout.total_amount),
        related_id=str(payout_id),
        actor=f"admin:{admin_perms.user.id}",
    )

    await db.delete(payout)
    await db.flush()

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_cancel_payout",
        resource_type="ambassador_payout",
        resource_id=str(payout_id),
        details={"ambassador_id": str(ambassador_id)},
        ip_address=request.client.host if request.client else None,
    )

    return {"message": "Payout cancelled and records returned to pending"}


# ── Manual bonus ──────────────────────────────────────────────────────────────


@router.post("/ambassadors/{ambassador_id}/bonus", response_model=BonusResponse)
async def admin_create_bonus(
    ambassador_id: uuid.UUID,
    data: CreateManualBonusRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    await _get_ambassador_or_404(ambassador_id, db)

    bonus = AmbassadorBonus(
        id=uuid.uuid4(),
        ambassador_id=ambassador_id,
        bonus_type=data.bonus_type,
        amount=data.amount,
        description=data.description,
        period=data.period,
    )
    db.add(bonus)
    await db.flush()
    await db.refresh(bonus)

    await log_activity(
        ambassador_id,
        ActivityEventType.BONUS_AWARDED,
        f"Manual bonus ${data.amount:,.2f} ({data.bonus_type.value}): {data.description}",
        db,
        amount=data.amount,
        related_id=str(bonus.id),
        actor=f"admin:{admin_perms.user.id}",
    )

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_create_bonus",
        resource_type="ambassador_bonus",
        resource_id=str(bonus.id),
        details={"bonus_type": data.bonus_type.value, "amount": data.amount},
        ip_address=request.client.host if request.client else None,
    )

    return BonusResponse.model_validate(bonus)


# ── Commission recalculation ──────────────────────────────────────────────────


@router.post("/ambassadors/commissions/recalculate")
async def admin_recalculate_commissions(
    data: RecalculateCommissionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    if data.ambassador_id:
        result = await db.execute(
            select(Ambassador).where(Ambassador.id == data.ambassador_id)
        )
        ambassadors = [result.scalar_one_or_none()]
        if ambassadors[0] is None:
            raise HTTPException(status_code=404, detail="Ambassador not found")
    else:
        result = await db.execute(select(Ambassador))
        ambassadors = list(result.scalars().all())

    processed = 0
    for amb in ambassadors:
        await calculate_commission_for_month(amb, data.month, data.year, db)
        processed += 1

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_recalculate_commissions",
        resource_type="ambassador_commission",
        resource_id=str(data.ambassador_id) if data.ambassador_id else "all",
        details={"month": data.month, "year": data.year, "processed": processed},
        ip_address=request.client.host if request.client else None,
    )

    return {
        "message": f"Recalculated commissions for {processed} ambassador(s)",
        "processed": processed,
    }


# ── Monthly cycle ─────────────────────────────────────────────────────────────


@router.post("/ambassadors/cycle/run")
async def admin_run_monthly_cycle(
    data: RunMonthlyCycleRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    summary = await run_monthly_ambassador_cycle(data.month, data.year, db)

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_run_monthly_cycle",
        resource_type="ambassador_commission",
        resource_id=f"{data.year}-{data.month:02d}",
        details=summary,
        ip_address=request.client.host if request.client else None,
    )

    return summary


# ── Leadership Revenue Pool ───────────────────────────────────────────────────


@router.post("/ambassadors/pool/calculate", response_model=LeadershipPoolResponse)
async def admin_calculate_pool(
    data: RunMonthlyCycleRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    pool = await calculate_leadership_pool(data.month, data.year, db)

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_calculate_leadership_pool",
        resource_type="leadership_pool",
        resource_id=f"{data.year}-{data.month:02d}",
        details={
            "month": data.month,
            "year": data.year,
            "pool_amount": float(pool.total_pool_amount),
        },
        ip_address=request.client.host if request.client else None,
    )

    return LeadershipPoolResponse.model_validate(pool)


# ── Travel incentives ─────────────────────────────────────────────────────────


@router.get(
    "/ambassadors/{ambassador_id}/travel",
    response_model=list[TravelIncentiveResponse],
)
async def admin_list_ambassador_travel(
    ambassador_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    await _get_ambassador_or_404(ambassador_id, db)

    travel_result = await db.execute(
        select(AmbassadorTravelIncentive)
        .where(AmbassadorTravelIncentive.ambassador_id == ambassador_id)
        .order_by(AmbassadorTravelIncentive.qualification_start.desc())
    )
    return [
        TravelIncentiveResponse.model_validate(t) for t in travel_result.scalars().all()
    ]


@router.post(
    "/ambassadors/{ambassador_id}/travel",
    response_model=TravelIncentiveResponse,
)
async def admin_award_travel(
    ambassador_id: uuid.UUID,
    data: AwardTravelRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    ambassador = await _get_ambassador_or_404(ambassador_id, db)

    destination = TRAVEL_DESTINATIONS.get(ambassador.rank)
    if not destination:
        raise HTTPException(
            status_code=400,
            detail=f"No travel incentive defined for rank {ambassador.rank.value}",
        )

    existing_result = await db.execute(
        select(AmbassadorTravelIncentive).where(
            AmbassadorTravelIncentive.ambassador_id == ambassador_id,
            AmbassadorTravelIncentive.rank_required == ambassador.rank,
        )
    )
    incentive = existing_result.scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if incentive is None:
        incentive = AmbassadorTravelIncentive(
            id=uuid.uuid4(),
            ambassador_id=ambassador_id,
            destination=destination,
            rank_required=ambassador.rank,
            qualification_start=now,
            qualification_end=now,
            status=TravelStatus.AWARDED,
            awarded_at=now,
            admin_notes=data.admin_notes,
        )
        db.add(incentive)
    else:
        incentive.status = TravelStatus.AWARDED
        incentive.qualification_end = now
        incentive.awarded_at = now
        if data.admin_notes:
            incentive.admin_notes = data.admin_notes

    await db.flush()
    await db.refresh(incentive)

    await log_activity(
        ambassador_id,
        ActivityEventType.TRAVEL_AWARDED,
        f"Travel incentive awarded: {destination}",
        db,
        related_id=str(incentive.id),
        actor=f"admin:{admin_perms.user.id}",
    )

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_award_travel",
        resource_type="ambassador_travel",
        resource_id=str(incentive.id),
        details={"ambassador_id": str(ambassador_id), "destination": destination},
        ip_address=request.client.host if request.client else None,
    )

    return TravelIncentiveResponse.model_validate(incentive)


@router.patch(
    "/ambassadors/{ambassador_id}/travel/{incentive_id}",
    response_model=TravelIncentiveResponse,
)
async def admin_update_travel(
    ambassador_id: uuid.UUID,
    incentive_id: uuid.UUID,
    data: UpdateTravelRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    result = await db.execute(
        select(AmbassadorTravelIncentive).where(
            AmbassadorTravelIncentive.id == incentive_id,
            AmbassadorTravelIncentive.ambassador_id == ambassador_id,
        )
    )
    incentive = result.scalar_one_or_none()
    if incentive is None:
        raise HTTPException(status_code=404, detail="Travel incentive not found")

    old_status = incentive.status
    incentive.status = data.status
    if data.admin_notes is not None:
        incentive.admin_notes = data.admin_notes
    if data.awarded_at is not None:
        incentive.awarded_at = data.awarded_at
    elif data.status == TravelStatus.AWARDED and incentive.awarded_at is None:
        incentive.awarded_at = datetime.now(timezone.utc)

    await db.flush()
    await db.refresh(incentive)

    await log_activity(
        ambassador_id,
        ActivityEventType.TRAVEL_STATUS_CHANGED,
        f"Travel status: {old_status.value} → {data.status.value} ({incentive.destination})",
        db,
        related_id=str(incentive_id),
        actor=f"admin:{admin_perms.user.id}",
    )

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_update_travel",
        resource_type="ambassador_travel",
        resource_id=str(incentive_id),
        details={"status": data.status.value},
        ip_address=request.client.host if request.client else None,
    )

    return TravelIncentiveResponse.model_validate(incentive)


# ── Training management ───────────────────────────────────────────────────────


@router.post("/ambassadors/{ambassador_id}/training/{module_key}/complete")
async def admin_complete_training(
    ambassador_id: uuid.UUID,
    module_key: str,
    request: Request,
    notes: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    await _get_ambassador_or_404(ambassador_id, db)

    existing_result = await db.execute(
        select(AmbassadorTrainingCompletion).where(
            AmbassadorTrainingCompletion.ambassador_id == ambassador_id,
            AmbassadorTrainingCompletion.module_name == module_key,
        )
    )
    if existing_result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Module already completed")

    completion = AmbassadorTrainingCompletion(
        id=uuid.uuid4(),
        ambassador_id=ambassador_id,
        module_name=module_key,
        completed_by_admin=admin_perms.user.id,
        notes=notes,
    )
    db.add(completion)
    await db.flush()

    await log_activity(
        ambassador_id,
        ActivityEventType.TRAINING_COMPLETED,
        f"Training module completed: {module_key}",
        db,
        related_id=str(completion.id),
        actor=f"admin:{admin_perms.user.id}",
    )

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_complete_training",
        resource_type="ambassador_training",
        resource_id=str(ambassador_id),
        details={"module_key": module_key},
        ip_address=request.client.host if request.client else None,
    )

    return {"message": "Training module marked complete", "module_key": module_key}


@router.delete("/ambassadors/{ambassador_id}/training/{module_key}")
async def admin_remove_training(
    ambassador_id: uuid.UUID,
    module_key: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    result = await db.execute(
        select(AmbassadorTrainingCompletion).where(
            AmbassadorTrainingCompletion.ambassador_id == ambassador_id,
            AmbassadorTrainingCompletion.module_name == module_key,
        )
    )
    completion = result.scalar_one_or_none()
    if completion is None:
        raise HTTPException(status_code=404, detail="Training completion not found")

    await db.delete(completion)
    await db.flush()

    await log_activity(
        ambassador_id,
        ActivityEventType.TRAINING_REMOVED,
        f"Training module removed: {module_key}",
        db,
        actor=f"admin:{admin_perms.user.id}",
    )

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_remove_training",
        resource_type="ambassador_training",
        resource_id=str(ambassador_id),
        details={"module_key": module_key},
        ip_address=request.client.host if request.client else None,
    )

    return {"message": "Training completion removed"}
