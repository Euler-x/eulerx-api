"""Admin ambassadors router — manage ambassador records, territories, commissions, payouts."""

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
    AmbassadorBonus,
    AmbassadorCommission,
    AmbassadorPayout,
    AmbassadorTerritory,
    AmbassadorTrainingCompletion,
)
from app.models.enums import AmbassadorRank, BonusType, CommissionStatus, PayoutStatus
from app.models.schemas.ambassador import (
    AdminAmbassadorResponse,
    AmbassadorResponse,
    BonusResponse,
    CreateAnnualBonusRequest,
    CreatePayoutRequest,
    CreateTerritoryRequest,
    PayoutResponse,
    PromoteAmbassadorRequest,
    RecalculateCommissionRequest,
    TerritoryResponse,
    UpdatePayoutRequest,
    UpdateTerritoryRequest,
)
from app.models.schemas.common import PaginatedResponse
from app.models.user import User
from app.services.ambassador import (
    TIER_PROMOTION_BONUSES,
    calculate_commission_for_month,
    get_active_referrals,
)

router = APIRouter()


def _mask_email(email: Optional[str]) -> Optional[str]:
    if not email or "@" not in email:
        return email
    local, domain = email.split("@", 1)
    masked_local = local[:3] + "***" if len(local) > 3 else local + "***"
    return f"{masked_local}@{domain}"


# ── List ambassadors ──────────────────────────────────────────────────────────


@router.get("/ambassadors", response_model=PaginatedResponse)
async def admin_list_ambassadors(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    rank: Optional[AmbassadorRank] = None,
    min_referrals: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    query = select(Ambassador).order_by(Ambassador.created_at.desc())
    count_query = select(func.count(Ambassador.id))

    if rank:
        query = query.where(Ambassador.rank == rank)
        count_query = count_query.where(Ambassador.rank == rank)
    if min_referrals is not None:
        query = query.where(Ambassador.total_referrals >= min_referrals)
        count_query = count_query.where(Ambassador.total_referrals >= min_referrals)

    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    ambassadors = result.scalars().all()

    items = []
    for amb in ambassadors:
        user_result = await db.execute(select(User).where(User.id == amb.user_id))
        user = user_result.scalar_one_or_none()
        masked_email = _mask_email(user.email if user else None)

        active_refs = await get_active_referrals(amb.id, db)

        territory_name: Optional[str] = None
        if amb.territory_id is not None:
            terr_result = await db.execute(
                select(AmbassadorTerritory).where(
                    AmbassadorTerritory.id == amb.territory_id
                )
            )
            terr = terr_result.scalar_one_or_none()
            territory_name = terr.name if terr else None

        items.append(
            AdminAmbassadorResponse(
                id=amb.id,
                user_id=amb.user_id,
                masked_email=masked_email,
                rank=amb.rank,
                total_referrals=amb.total_referrals,
                active_referral_count=len(active_refs),
                rewards_earned=float(amb.rewards_earned),
                payout_address=amb.payout_address,
                territory_name=territory_name,
                created_at=amb.created_at,
            )
        )

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


@router.get("/ambassadors/{ambassador_id}", response_model=AmbassadorResponse)
async def admin_get_ambassador(
    ambassador_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    result = await db.execute(select(Ambassador).where(Ambassador.id == ambassador_id))
    ambassador = result.scalar_one_or_none()
    if ambassador is None:
        raise HTTPException(status_code=404, detail="Ambassador not found")
    return AmbassadorResponse.model_validate(ambassador)


# ── Promote ambassador ────────────────────────────────────────────────────────


@router.post("/ambassadors/{ambassador_id}/promote", response_model=AmbassadorResponse)
async def admin_promote_ambassador(
    ambassador_id: uuid.UUID,
    data: PromoteAmbassadorRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    result = await db.execute(select(Ambassador).where(Ambassador.id == ambassador_id))
    ambassador = result.scalar_one_or_none()
    if ambassador is None:
        raise HTTPException(status_code=404, detail="Ambassador not found")

    old_rank = ambassador.rank
    ambassador.rank = data.rank

    promo_amount = TIER_PROMOTION_BONUSES.get(data.rank, 0)
    if promo_amount > 0:
        bonus = AmbassadorBonus(
            id=uuid.uuid4(),
            ambassador_id=ambassador.id,
            bonus_type=BonusType.TIER_PROMOTION,
            amount=promo_amount,
            description=f"Admin promotion: {old_rank.value} → {data.rank.value}",
        )
        db.add(bonus)

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


# ── Payout management ─────────────────────────────────────────────────────────


@router.post("/ambassadors/{ambassador_id}/payout", response_model=PayoutResponse)
async def admin_create_payout(
    ambassador_id: uuid.UUID,
    data: CreatePayoutRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    result = await db.execute(select(Ambassador).where(Ambassador.id == ambassador_id))
    ambassador = result.scalar_one_or_none()
    if ambassador is None:
        raise HTTPException(status_code=404, detail="Ambassador not found")

    now = datetime.now(timezone.utc)
    total_amount = 0.0

    # Load and validate commissions
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

    # Load and validate bonuses
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

    # Mark included records as paid
    for comm in commissions:
        comm.status = CommissionStatus.PAID
        comm.paid_at = now
    for bonus in bonuses:
        bonus.status = CommissionStatus.PAID
        bonus.paid_at = now

    await db.flush()
    await db.refresh(payout)

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

    payout.status = data.status
    if data.admin_notes is not None:
        payout.admin_notes = data.admin_notes

    now = datetime.now(timezone.utc)

    if data.status == PayoutStatus.PAID:
        payout.paid_at = now
        # Mark included commissions and bonuses as paid
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


@router.get("/ambassadors/payouts", response_model=PaginatedResponse)
async def admin_list_payouts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[PayoutStatus] = None,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    query = select(AmbassadorPayout).order_by(AmbassadorPayout.created_at.desc())
    count_query = select(func.count(AmbassadorPayout.id))

    if status:
        query = query.where(AmbassadorPayout.status == status)
        count_query = count_query.where(AmbassadorPayout.status == status)

    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    payouts = result.scalars().all()

    return PaginatedResponse(
        items=[PayoutResponse.model_validate(p) for p in payouts],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


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
    result = await db.execute(select(Ambassador).where(Ambassador.id == ambassador_id))
    ambassador = result.scalar_one_or_none()
    if ambassador is None:
        raise HTTPException(status_code=404, detail="Ambassador not found")

    # Check if already completed
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

    return {"message": "Training completion removed"}


# ── Territory management ──────────────────────────────────────────────────────


@router.get("/ambassadors/territories", response_model=list[TerritoryResponse])
async def admin_list_territories(
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    result = await db.execute(
        select(AmbassadorTerritory).order_by(AmbassadorTerritory.name)
    )
    territories = result.scalars().all()

    items = []
    for terr in territories:
        count_result = await db.execute(
            select(func.count(Ambassador.id)).where(Ambassador.territory_id == terr.id)
        )
        ambassador_count = count_result.scalar() or 0
        items.append(
            TerritoryResponse(
                id=terr.id,
                name=terr.name,
                territory_type=terr.territory_type,
                description=terr.description,
                revenue_share_pct=float(terr.revenue_share_pct),
                master_ambassador_id=terr.master_ambassador_id,
                ambassador_count=ambassador_count,
            )
        )
    return items


@router.post("/ambassadors/territories", response_model=TerritoryResponse)
async def admin_create_territory(
    data: CreateTerritoryRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    territory = AmbassadorTerritory(
        id=uuid.uuid4(),
        name=data.name,
        territory_type=data.territory_type,
        description=data.description,
        master_ambassador_id=data.master_ambassador_id,
        revenue_share_pct=data.revenue_share_pct,
    )
    db.add(territory)
    await db.flush()
    await db.refresh(territory)

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_create_territory",
        resource_type="ambassador_territory",
        resource_id=str(territory.id),
        details={"name": data.name},
        ip_address=request.client.host if request.client else None,
    )

    return TerritoryResponse(
        id=territory.id,
        name=territory.name,
        territory_type=territory.territory_type,
        description=territory.description,
        revenue_share_pct=float(territory.revenue_share_pct),
        master_ambassador_id=territory.master_ambassador_id,
        ambassador_count=0,
    )


@router.patch(
    "/ambassadors/territories/{territory_id}", response_model=TerritoryResponse
)
async def admin_update_territory(
    territory_id: uuid.UUID,
    data: UpdateTerritoryRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    result = await db.execute(
        select(AmbassadorTerritory).where(AmbassadorTerritory.id == territory_id)
    )
    territory = result.scalar_one_or_none()
    if territory is None:
        raise HTTPException(status_code=404, detail="Territory not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(territory, key, value)

    await db.flush()

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


@router.post("/ambassadors/{ambassador_id}/territory/{territory_id}")
async def admin_assign_ambassador_territory(
    ambassador_id: uuid.UUID,
    territory_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    amb_result = await db.execute(
        select(Ambassador).where(Ambassador.id == ambassador_id)
    )
    ambassador = amb_result.scalar_one_or_none()
    if ambassador is None:
        raise HTTPException(status_code=404, detail="Ambassador not found")

    terr_result = await db.execute(
        select(AmbassadorTerritory).where(AmbassadorTerritory.id == territory_id)
    )
    territory = terr_result.scalar_one_or_none()
    if territory is None:
        raise HTTPException(status_code=404, detail="Territory not found")

    ambassador.territory_id = territory_id
    await db.flush()

    return {
        "message": "Ambassador assigned to territory",
        "territory_id": str(territory_id),
    }


@router.delete("/ambassadors/{ambassador_id}/territory")
async def admin_remove_ambassador_territory(
    ambassador_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    result = await db.execute(select(Ambassador).where(Ambassador.id == ambassador_id))
    ambassador = result.scalar_one_or_none()
    if ambassador is None:
        raise HTTPException(status_code=404, detail="Ambassador not found")

    ambassador.territory_id = None
    await db.flush()

    return {"message": "Ambassador removed from territory"}


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
        "message": f"Recalculated commissions for {processed} ambassadors",
        "processed": processed,
    }


# ── Annual recognition bonus ──────────────────────────────────────────────────


@router.post("/ambassadors/{ambassador_id}/bonus/annual", response_model=BonusResponse)
async def admin_create_annual_bonus(
    ambassador_id: uuid.UUID,
    data: CreateAnnualBonusRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    result = await db.execute(select(Ambassador).where(Ambassador.id == ambassador_id))
    ambassador = result.scalar_one_or_none()
    if ambassador is None:
        raise HTTPException(status_code=404, detail="Ambassador not found")

    bonus = AmbassadorBonus(
        id=uuid.uuid4(),
        ambassador_id=ambassador_id,
        bonus_type=BonusType.ANNUAL_RECOGNITION,
        amount=data.amount,
        description=data.description,
        period=data.period,
    )
    db.add(bonus)
    await db.flush()
    await db.refresh(bonus)

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_create_annual_bonus",
        resource_type="ambassador_bonus",
        resource_id=str(bonus.id),
        details={"amount": data.amount, "description": data.description},
        ip_address=request.client.host if request.client else None,
    )

    return BonusResponse.model_validate(bonus)
