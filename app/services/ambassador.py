"""Ambassador program service: commission calculation, tier management, bonuses."""

import logging
import uuid
from typing import Optional

from sqlalchemy import select, extract, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ambassador import Ambassador
from app.models.ambassador_programs import (
    AmbassadorBonus,
    AmbassadorCommission,
    AmbassadorTerritory,
    TerritoryCommission,
)
from app.models.billing import Subscription, Plan
from app.models.enums import (
    AmbassadorRank,
    BonusType,
    CommissionStatus,
)
from app.models.user import User
from app.models.enums import SubscriptionStatus

logger = logging.getLogger(__name__)

# ── Commission rates by rank (%) ─────────────────────────────────────────────
COMMISSION_RATES: dict[AmbassadorRank, float] = {
    AmbassadorRank.SCOUT: 15.0,
    AmbassadorRank.GUIDE: 20.0,
    AmbassadorRank.STRATEGIST: 25.0,
    AmbassadorRank.MASTER: 30.0,
}

# ── Tier requirements for auto-promotion ────────────────────────────────────
# MASTER is manual-only; not listed here.
TIER_REQUIREMENTS: dict[AmbassadorRank, dict] = {
    AmbassadorRank.GUIDE: {"min_active_referrals": 10},
    AmbassadorRank.STRATEGIST: {"min_active_referrals": 50, "min_retention_pct": 80.0},
}

# ── Monthly conversion bonus tiers (min, max_exclusive, bonus_usd) ────────────
# max_exclusive=None means "and above"
CONVERSION_BONUSES: dict[AmbassadorRank, list[tuple]] = {
    AmbassadorRank.SCOUT: [(3, 5, 50), (6, 10, 150), (10, None, 300)],
    AmbassadorRank.GUIDE: [(5, 10, 200), (11, 20, 600), (20, None, 1500)],
    AmbassadorRank.STRATEGIST: [(10, 20, 500), (21, 50, 1500), (50, None, 5000)],
    AmbassadorRank.MASTER: [(25, 50, 2000), (51, 100, 5000), (100, None, 10000)],
}

# ── Quarterly retention bonus tiers (min_pct, bonus_usd) ─────────────────────
RETENTION_BONUSES: dict[AmbassadorRank, list[tuple]] = {
    AmbassadorRank.SCOUT: [(80, 300), (90, 600), (95, 1000)],
    AmbassadorRank.GUIDE: [(80, 500), (90, 1000), (95, 2000)],
    AmbassadorRank.STRATEGIST: [(80, 1000), (90, 2000), (95, 3500)],
    AmbassadorRank.MASTER: [(80, 3000), (90, 6000), (95, 10000)],
}

# ── One-time milestone bonuses (total_referrals, bonus_usd, description) ─────
MILESTONE_BONUSES: list[tuple] = [
    (50, 500, "First 50 referrals"),
    (100, 1000, "First 100 referrals"),
    (500, 2500, "First 500 referrals"),
    (1000, 5000, "First 1000 referrals"),
]

# ── Tier promotion bonuses ────────────────────────────────────────────────────
TIER_PROMOTION_BONUSES: dict[AmbassadorRank, float] = {
    AmbassadorRank.GUIDE: 250.0,
    AmbassadorRank.STRATEGIST: 1000.0,
    AmbassadorRank.MASTER: 2000.0,
}


# ── Helper queries ────────────────────────────────────────────────────────────


async def get_active_referrals(
    ambassador_id: uuid.UUID, db: AsyncSession
) -> list[Ambassador]:
    """Return all referred ambassadors whose users are currently subscribed."""
    result = await db.execute(
        select(Ambassador)
        .join(User, User.id == Ambassador.user_id)
        .where(
            Ambassador.referred_by == ambassador_id,
            User.is_subscribed.is_(True),
        )
    )
    return list(result.scalars().all())


async def get_referral_count_for_month(
    ambassador_id: uuid.UUID, month: int, year: int, db: AsyncSession
) -> int:
    """Count new referrals created during a specific month/year."""
    result = await db.execute(
        select(func.count(Ambassador.id)).where(
            Ambassador.referred_by == ambassador_id,
            extract("month", Ambassador.created_at) == month,
            extract("year", Ambassador.created_at) == year,
        )
    )
    return result.scalar() or 0


async def get_retention_rate(ambassador_id: uuid.UUID, db: AsyncSession) -> float:
    """Return retention rate as a percentage (0–100)."""
    total_result = await db.execute(
        select(func.count(Ambassador.id)).where(Ambassador.referred_by == ambassador_id)
    )
    total = total_result.scalar() or 0
    if total == 0:
        return 0.0

    active_result = await db.execute(
        select(func.count(Ambassador.id))
        .join(User, User.id == Ambassador.user_id)
        .where(
            Ambassador.referred_by == ambassador_id,
            User.is_subscribed.is_(True),
        )
    )
    active = active_result.scalar() or 0
    return round((active / total) * 100, 2)


async def get_plan_price_for_user(user_id: uuid.UUID, db: AsyncSession) -> float:
    """Return the plan price (USD) for a user's active subscription, or 0.0."""
    result = await db.execute(
        select(Plan.price_usd)
        .join(Subscription, Subscription.plan_id == Plan.id)
        .where(
            Subscription.user_id == user_id,
            Subscription.status.in_(
                [SubscriptionStatus.ACTIVE, SubscriptionStatus.EXPIRING_SOON]
            ),
        )
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    price = result.scalar_one_or_none()
    return float(price) if price is not None else 0.0


# ── Commission calculation ────────────────────────────────────────────────────


async def calculate_commission_for_month(
    ambassador: Ambassador,
    month: int,
    year: int,
    db: AsyncSession,
) -> AmbassadorCommission:
    """Calculate and persist a monthly commission record for an ambassador."""
    # Find all referrals
    referrals_result = await db.execute(
        select(Ambassador).where(Ambassador.referred_by == ambassador.id)
    )
    referrals = list(referrals_result.scalars().all())

    active_count = 0
    total_base = 0.0
    for ref in referrals:
        price = await get_plan_price_for_user(ref.user_id, db)
        if price > 0:
            active_count += 1
            total_base += price

    rate = COMMISSION_RATES.get(ambassador.rank, 15.0)
    commission_amount = round(total_base * (rate / 100), 2)

    # Upsert commission record
    existing_result = await db.execute(
        select(AmbassadorCommission).where(
            AmbassadorCommission.ambassador_id == ambassador.id,
            AmbassadorCommission.month == month,
            AmbassadorCommission.year == year,
        )
    )
    commission = existing_result.scalar_one_or_none()

    if commission is None:
        commission = AmbassadorCommission(
            id=uuid.uuid4(),
            ambassador_id=ambassador.id,
            month=month,
            year=year,
            active_referral_count=active_count,
            commission_rate=rate,
            commission_amount=commission_amount,
            status=CommissionStatus.PENDING,
        )
        db.add(commission)
    else:
        if commission.status == CommissionStatus.PENDING:
            commission.active_referral_count = active_count
            commission.commission_rate = rate
            commission.commission_amount = commission_amount

    await db.flush()
    return commission


# ── Bonus calculations ────────────────────────────────────────────────────────


async def calculate_conversion_bonus(
    ambassador: Ambassador,
    month: int,
    year: int,
    db: AsyncSession,
) -> Optional[AmbassadorBonus]:
    """Create a conversion bonus if the ambassador hit a tier this month."""
    new_count = await get_referral_count_for_month(ambassador.id, month, year, db)
    tiers = CONVERSION_BONUSES.get(ambassador.rank, [])

    # Find the highest matching tier
    bonus_amount = 0
    for min_refs, max_refs, amount in reversed(tiers):
        if new_count >= min_refs and (max_refs is None or new_count < max_refs + 1):
            bonus_amount = amount
            break
        elif max_refs is None and new_count >= min_refs:
            bonus_amount = amount
            break

    if bonus_amount == 0:
        return None

    period = f"{year}-{month:02d}"

    # Avoid duplicates
    existing_result = await db.execute(
        select(AmbassadorBonus).where(
            AmbassadorBonus.ambassador_id == ambassador.id,
            AmbassadorBonus.bonus_type == BonusType.CONVERSION,
            AmbassadorBonus.period == period,
        )
    )
    if existing_result.scalar_one_or_none() is not None:
        return None

    bonus = AmbassadorBonus(
        id=uuid.uuid4(),
        ambassador_id=ambassador.id,
        bonus_type=BonusType.CONVERSION,
        amount=bonus_amount,
        period=period,
        description=f"Conversion bonus: {new_count} new referrals in {period}",
        status=CommissionStatus.PENDING,
    )
    db.add(bonus)
    await db.flush()
    return bonus


async def calculate_retention_bonus(
    ambassador: Ambassador,
    quarter: int,
    year: int,
    db: AsyncSession,
) -> Optional[AmbassadorBonus]:
    """Create a quarterly retention bonus if retention rate meets a tier."""
    retention_rate = await get_retention_rate(ambassador.id, db)
    tiers = RETENTION_BONUSES.get(ambassador.rank, [])

    bonus_amount = 0
    for min_pct, amount in reversed(tiers):
        if retention_rate >= min_pct:
            bonus_amount = amount
            break

    if bonus_amount == 0:
        return None

    period = f"{year}-Q{quarter}"

    # Avoid duplicates
    existing_result = await db.execute(
        select(AmbassadorBonus).where(
            AmbassadorBonus.ambassador_id == ambassador.id,
            AmbassadorBonus.bonus_type == BonusType.RETENTION,
            AmbassadorBonus.period == period,
        )
    )
    if existing_result.scalar_one_or_none() is not None:
        return None

    bonus = AmbassadorBonus(
        id=uuid.uuid4(),
        ambassador_id=ambassador.id,
        bonus_type=BonusType.RETENTION,
        amount=bonus_amount,
        period=period,
        description=f"Retention bonus: {retention_rate:.1f}% retention in Q{quarter} {year}",
        status=CommissionStatus.PENDING,
    )
    db.add(bonus)
    await db.flush()
    return bonus


async def check_milestone_bonuses(
    ambassador: Ambassador, db: AsyncSession
) -> list[AmbassadorBonus]:
    """Award one-time milestone bonuses for referral count milestones not yet awarded."""
    awarded = []
    for threshold, amount, description in MILESTONE_BONUSES:
        if ambassador.total_referrals < threshold:
            continue

        # Check if already awarded
        existing_result = await db.execute(
            select(AmbassadorBonus).where(
                AmbassadorBonus.ambassador_id == ambassador.id,
                AmbassadorBonus.bonus_type == BonusType.MILESTONE,
                AmbassadorBonus.description == description,
            )
        )
        if existing_result.scalar_one_or_none() is not None:
            continue

        bonus = AmbassadorBonus(
            id=uuid.uuid4(),
            ambassador_id=ambassador.id,
            bonus_type=BonusType.MILESTONE,
            amount=amount,
            description=description,
            status=CommissionStatus.PENDING,
        )
        db.add(bonus)
        awarded.append(bonus)

    if awarded:
        await db.flush()
    return awarded


async def check_and_promote_tier(ambassador: Ambassador, db: AsyncSession) -> bool:
    """Auto-promote an ambassador if they meet tier requirements. Returns True if promoted."""
    active_referrals = await get_active_referrals(ambassador.id, db)
    active_count = len(active_referrals)
    retention_rate = await get_retention_rate(ambassador.id, db)

    new_rank: Optional[AmbassadorRank] = None

    if ambassador.rank == AmbassadorRank.SCOUT:
        reqs = TIER_REQUIREMENTS.get(AmbassadorRank.GUIDE, {})
        if active_count >= reqs.get("min_active_referrals", 0):
            new_rank = AmbassadorRank.GUIDE

    elif ambassador.rank == AmbassadorRank.GUIDE:
        reqs = TIER_REQUIREMENTS.get(AmbassadorRank.STRATEGIST, {})
        if active_count >= reqs.get(
            "min_active_referrals", 0
        ) and retention_rate >= reqs.get("min_retention_pct", 0.0):
            new_rank = AmbassadorRank.STRATEGIST

    # STRATEGIST → MASTER is manual only

    if new_rank is None:
        return False

    ambassador.rank = new_rank

    promo_amount = TIER_PROMOTION_BONUSES.get(new_rank, 0)
    if promo_amount > 0:
        bonus = AmbassadorBonus(
            id=uuid.uuid4(),
            ambassador_id=ambassador.id,
            bonus_type=BonusType.TIER_PROMOTION,
            amount=promo_amount,
            description=f"Tier promotion bonus: promoted to {new_rank.value}",
            status=CommissionStatus.PENDING,
        )
        db.add(bonus)

    await db.flush()
    logger.info("Ambassador %s promoted to %s", ambassador.id, new_rank.value)
    return True


# ── Territory commission ──────────────────────────────────────────────────────


async def calculate_territory_commission(
    territory: AmbassadorTerritory,
    month: int,
    year: int,
    db: AsyncSession,
) -> Optional[TerritoryCommission]:
    """Calculate and persist territory commission for the master ambassador."""
    if territory.master_ambassador_id is None:
        return None

    # Get all ambassadors in this territory
    amb_result = await db.execute(
        select(Ambassador).where(Ambassador.territory_id == territory.id)
    )
    territory_ambassadors = list(amb_result.scalars().all())

    total_volume = 0.0
    for amb in territory_ambassadors:
        referrals_result = await db.execute(
            select(Ambassador).where(Ambassador.referred_by == amb.id)
        )
        referrals = list(referrals_result.scalars().all())
        for ref in referrals:
            total_volume += await get_plan_price_for_user(ref.user_id, db)

    revenue_share_pct = float(territory.revenue_share_pct)
    commission_amount = round(total_volume * (revenue_share_pct / 100), 2)

    # Upsert
    existing_result = await db.execute(
        select(TerritoryCommission).where(
            TerritoryCommission.territory_id == territory.id,
            TerritoryCommission.month == month,
            TerritoryCommission.year == year,
        )
    )
    tc = existing_result.scalar_one_or_none()

    if tc is None:
        tc = TerritoryCommission(
            id=uuid.uuid4(),
            territory_id=territory.id,
            master_ambassador_id=territory.master_ambassador_id,
            month=month,
            year=year,
            territory_volume_usd=total_volume,
            ambassador_count=len(territory_ambassadors),
            revenue_share_pct=revenue_share_pct,
            commission_amount=commission_amount,
        )
        db.add(tc)
    else:
        tc.territory_volume_usd = total_volume
        tc.ambassador_count = len(territory_ambassadors)
        tc.revenue_share_pct = revenue_share_pct
        tc.commission_amount = commission_amount

    await db.flush()
    return tc
