"""Ambassador program V2 service: multi-level commissions, rank qualification, bonuses."""

import logging
import uuid
from datetime import timedelta
from typing import Optional

from sqlalchemy import func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ambassador import Ambassador
from app.models.ambassador_programs import (
    AmbassadorActivityLog,
    AmbassadorBonus,
    AmbassadorCommission,
    AmbassadorTravelIncentive,
    LeadershipRevenuePool,
)
from app.models.billing import Plan, Subscription
from app.models.enums import (
    ActivityEventType,
    AmbassadorRank,
    BonusType,
    CommissionStatus,
    SubscriptionStatus,
    TravelStatus,
)
from app.models.user import User
from app.utils.helpers import utc_now

logger = logging.getLogger(__name__)


# ── Activity logging helper ───────────────────────────────────────────────────


async def log_activity(
    ambassador_id: uuid.UUID,
    event_type: ActivityEventType,
    description: str,
    db: AsyncSession,
    *,
    amount: float | None = None,
    related_id: str | None = None,
    actor: str = "system",
) -> None:
    """Append one event to ambassador_activity_logs. Never raises — log errors silently."""
    try:
        entry = AmbassadorActivityLog(
            id=uuid.uuid4(),
            ambassador_id=ambassador_id,
            event_type=event_type,
            description=description,
            amount=amount,
            related_id=related_id,
            actor=actor,
        )
        db.add(entry)
        await db.flush()
    except Exception:
        logger.exception(
            "Failed to write activity log for ambassador %s", ambassador_id
        )


# ── Commission rates per level (%) ────────────────────────────────────────────

LEVEL_RATES: dict[int, float] = {
    1: 25.0,
    2: 8.0,
    3: 5.0,
    4: 4.0,
    5: 3.0,
    6: 3.0,
    7: 2.0,
    8: 2.0,
    9: 2.0,
    10: 2.0,
}

SUBSCRIPTION_PRICE = 250.0  # ATE Pro monthly price

# ── Max depth each rank can earn on ──────────────────────────────────────────

RANK_MAX_DEPTH: dict[AmbassadorRank, int] = {
    AmbassadorRank.ASSOCIATE: 1,
    AmbassadorRank.BRONZE_LEADER: 2,
    AmbassadorRank.SILVER_LEADER: 3,
    AmbassadorRank.GOLD_LEADER: 4,
    AmbassadorRank.PLATINUM_LEADER: 5,
    AmbassadorRank.DIAMOND_LEADER: 6,
    AmbassadorRank.ELITE_DIAMOND: 7,
    AmbassadorRank.BLACK_DIAMOND: 8,
    AmbassadorRank.CROWN_AMBASSADOR: 9,
    AmbassadorRank.GRAND_CROWN: 10,
}

# Ranks with Crown-level generational override (1% beyond L10)
GENERATIONAL_RANKS = {AmbassadorRank.CROWN_AMBASSADOR, AmbassadorRank.GRAND_CROWN}

# ── Rank qualification criteria ───────────────────────────────────────────────

RANK_CRITERIA: dict[AmbassadorRank, dict] = {
    AmbassadorRank.ASSOCIATE: {
        "par": 0,
        "tav": 0,
        "legs": 0,
        "leg_rank": None,
    },
    AmbassadorRank.BRONZE_LEADER: {
        "par": 3,
        "tav": 3,
        "legs": 0,
        "leg_rank": None,
    },
    AmbassadorRank.SILVER_LEADER: {
        "par": 5,
        "tav": 15,
        "legs": 1,
        "leg_rank": AmbassadorRank.BRONZE_LEADER,
    },
    AmbassadorRank.GOLD_LEADER: {
        "par": 5,
        "tav": 40,
        "legs": 2,
        "leg_rank": AmbassadorRank.SILVER_LEADER,
    },
    AmbassadorRank.PLATINUM_LEADER: {
        "par": 5,
        "tav": 100,
        "legs": 3,
        "leg_rank": AmbassadorRank.GOLD_LEADER,
    },
    AmbassadorRank.DIAMOND_LEADER: {
        "par": 5,
        "tav": 250,
        "legs": 2,
        "leg_rank": AmbassadorRank.PLATINUM_LEADER,
    },
    AmbassadorRank.ELITE_DIAMOND: {
        "par": 5,
        "tav": 600,
        "legs": 3,
        "leg_rank": AmbassadorRank.DIAMOND_LEADER,
    },
    AmbassadorRank.BLACK_DIAMOND: {
        "par": 5,
        "tav": 1500,
        "legs": 2,
        "leg_rank": AmbassadorRank.ELITE_DIAMOND,
    },
    AmbassadorRank.CROWN_AMBASSADOR: {
        "par": 5,
        "tav": 3000,
        "legs": 2,
        "leg_rank": AmbassadorRank.BLACK_DIAMOND,
    },
    AmbassadorRank.GRAND_CROWN: {
        "par": 5,
        "tav": 7500,
        "legs": 3,
        "leg_rank": AmbassadorRank.CROWN_AMBASSADOR,
    },
}

# ── Rank order for comparisons ────────────────────────────────────────────────

RANK_ORDER = list(AmbassadorRank)


def rank_index(rank: AmbassadorRank) -> int:
    return RANK_ORDER.index(rank)


# ── Rank advancement bonuses (one-time, paid on first achieving rank) ─────────

RANK_ADVANCEMENT_BONUSES: dict[AmbassadorRank, float] = {
    AmbassadorRank.BRONZE_LEADER: 250.0,
    AmbassadorRank.SILVER_LEADER: 750.0,
    AmbassadorRank.GOLD_LEADER: 2500.0,
    AmbassadorRank.PLATINUM_LEADER: 5000.0,
    AmbassadorRank.DIAMOND_LEADER: 15000.0,
    AmbassadorRank.ELITE_DIAMOND: 30000.0,
    AmbassadorRank.BLACK_DIAMOND: 75000.0,
    AmbassadorRank.CROWN_AMBASSADOR: 150000.0,
    AmbassadorRank.GRAND_CROWN: 500000.0,
}

# ── Travel incentive destinations by rank ────────────────────────────────────

TRAVEL_DESTINATIONS: dict[AmbassadorRank, str] = {
    AmbassadorRank.GOLD_LEADER: "Rwanda & Tanzania",
    AmbassadorRank.PLATINUM_LEADER: "Thailand",
    AmbassadorRank.DIAMOND_LEADER: "Singapore",
    AmbassadorRank.ELITE_DIAMOND: "Qatar",
    AmbassadorRank.BLACK_DIAMOND: "All 5 Destinations (Full Circuit)",
    AmbassadorRank.CROWN_AMBASSADOR: "All 5 Destinations + Partner",
    AmbassadorRank.GRAND_CROWN: "All 5 Destinations + Private Charter Option",
}

# Qualification window in months for each travel tier
TRAVEL_QUALIFICATION_MONTHS: dict[AmbassadorRank, int] = {
    AmbassadorRank.GOLD_LEADER: 3,
    AmbassadorRank.PLATINUM_LEADER: 3,
    AmbassadorRank.DIAMOND_LEADER: 3,
    AmbassadorRank.ELITE_DIAMOND: 3,
    AmbassadorRank.BLACK_DIAMOND: 6,
    AmbassadorRank.CROWN_AMBASSADOR: 6,
    AmbassadorRank.GRAND_CROWN: 12,
}

# ── Performance milestone bonuses ────────────────────────────────────────────

# (key, threshold, metric, bonus_usd, description)
PERFORMANCE_MILESTONES: list[tuple] = [
    ("par_5", 5, "par", 100, "Recruit first 5 active subscribers"),
    ("par_10", 10, "par", 250, "Reach 10 active direct referrals"),
    ("par_25", 25, "par", 500, "Reach 25 active direct referrals"),
    ("earned_5000", 5000, "total_earned", 250, "First $5,000 total earnings"),
    ("earned_25000", 25000, "total_earned", 1000, "First $25,000 total earnings"),
]


# ── Core helpers ──────────────────────────────────────────────────────────────


async def get_plan_price_for_user(user_id: uuid.UUID, db: AsyncSession) -> float:
    """Return the active subscription plan price for a user, or 0."""
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


async def get_subscription_start_for_user(
    user_id: uuid.UUID, db: AsyncSession
) -> Optional[object]:
    """Return the earliest active subscription start date for a user."""
    result = await db.execute(
        select(Subscription.created_at)
        .where(
            Subscription.user_id == user_id,
            Subscription.status.in_(
                [SubscriptionStatus.ACTIVE, SubscriptionStatus.EXPIRING_SOON]
            ),
        )
        .order_by(Subscription.created_at.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_downline_by_level(
    ambassador_id: uuid.UUID,
    max_depth: int,
    db: AsyncSession,
) -> dict[int, list[Ambassador]]:
    """BFS walk from ambassador_id, returning downline grouped by depth level.

    Level 1 = direct referrals of ambassador_id.
    Each level only fetches one batch of DB rows — O(max_depth) queries total.
    """
    levels: dict[int, list[Ambassador]] = {}
    current_ids = [ambassador_id]

    for depth in range(1, max_depth + 1):
        result = await db.execute(
            select(Ambassador).where(Ambassador.referred_by.in_(current_ids))
        )
        members = result.scalars().all()
        if not members:
            break
        levels[depth] = list(members)
        current_ids = [m.id for m in members]

    return levels


async def get_par_count(ambassador_id: uuid.UUID, db: AsyncSession) -> int:
    """Count direct referrals with an active subscription (PAR)."""
    result = await db.execute(
        select(func.count(Ambassador.id))
        .join(User, User.id == Ambassador.user_id)
        .join(
            Subscription,
            (Subscription.user_id == User.id)
            & Subscription.status.in_(
                [SubscriptionStatus.ACTIVE, SubscriptionStatus.EXPIRING_SOON]
            ),
        )
        .where(Ambassador.referred_by == ambassador_id)
    )
    return result.scalar() or 0


async def get_tav_count(ambassador_id: uuid.UUID, db: AsyncSession) -> int:
    """Count all active subscribers in the full downline (any depth, up to 20 levels)."""
    levels = await get_downline_by_level(ambassador_id, 20, db)
    total = 0
    for members in levels.values():
        for m in members:
            price = await get_plan_price_for_user(m.user_id, db)
            if price > 0:
                total += 1
    return total


async def _downline_has_rank(
    start_id: uuid.UUID,
    required_idx: int,
    db: AsyncSession,
    max_depth: int = 20,
) -> bool:
    """Return True if start_id or any of its downline is at required rank or above."""
    to_check = [start_id]
    visited: set[uuid.UUID] = set()

    while to_check:
        current_id = to_check.pop()
        if current_id in visited:
            continue
        visited.add(current_id)

        amb_result = await db.execute(
            select(Ambassador.rank, Ambassador.id).where(Ambassador.id == current_id)
        )
        row = amb_result.one_or_none()
        if row is None:
            continue

        if rank_index(row.rank) >= required_idx:
            return True

        refs_result = await db.execute(
            select(Ambassador.id).where(Ambassador.referred_by == current_id)
        )
        to_check.extend(r[0] for r in refs_result.all())

    return False


async def count_qualifying_legs(
    ambassador_id: uuid.UUID,
    required_rank: AmbassadorRank,
    db: AsyncSession,
) -> int:
    """Count direct lines that contain at least one person at or above required_rank."""
    direct_refs_result = await db.execute(
        select(Ambassador.id).where(Ambassador.referred_by == ambassador_id)
    )
    direct_ref_ids = [r[0] for r in direct_refs_result.all()]

    required_idx = rank_index(required_rank)
    qualifying = 0
    for ref_id in direct_ref_ids:
        if await _downline_has_rank(ref_id, required_idx, db):
            qualifying += 1
    return qualifying


# ── Rank qualification ────────────────────────────────────────────────────────


async def check_rank_qualification(
    ambassador: Ambassador,
    target_rank: AmbassadorRank,
    db: AsyncSession,
) -> bool:
    """Return True if ambassador currently meets all criteria for target_rank."""
    criteria = RANK_CRITERIA.get(target_rank)
    if not criteria:
        return False

    # PAR
    if criteria["par"] > 0:
        par = await get_par_count(ambassador.id, db)
        if par < criteria["par"]:
            return False

    # TAV
    if criteria["tav"] > 0:
        tav = await get_tav_count(ambassador.id, db)
        if tav < criteria["tav"]:
            return False

    # Qualifying legs
    if criteria["legs"] > 0 and criteria["leg_rank"]:
        legs = await count_qualifying_legs(ambassador.id, criteria["leg_rank"], db)
        if legs < criteria["legs"]:
            return False

    return True


async def evaluate_and_update_rank(
    ambassador: Ambassador, db: AsyncSession
) -> Optional[AmbassadorRank]:
    """Determine the highest rank the ambassador qualifies for and update if changed.

    Returns the new rank if it changed, else None.
    """
    # Walk from highest rank downward to find the highest qualifying rank
    best_rank = AmbassadorRank.ASSOCIATE
    for rank in reversed(RANK_ORDER):
        if await check_rank_qualification(ambassador, rank, db):
            best_rank = rank
            break

    if best_rank == ambassador.rank:
        return None

    old_rank = ambassador.rank
    ambassador.rank = best_rank
    ambassador.rank_achieved_at = utc_now()

    # Award rank advancement bonus if this is an upgrade
    if rank_index(best_rank) > rank_index(old_rank):
        await _award_rank_advancement_bonus(ambassador, best_rank, db)
        await _check_travel_incentive(ambassador, best_rank, db)

    logger.info(
        "Ambassador %s rank changed: %s → %s",
        ambassador.id,
        old_rank.value,
        best_rank.value,
    )
    await log_activity(
        ambassador.id,
        ActivityEventType.RANK_CHANGED,
        f"Rank changed from {old_rank.value.replace('_', ' ').title()} to {best_rank.value.replace('_', ' ').title()}",
        db,
        actor="system",
    )
    return best_rank


async def _award_rank_advancement_bonus(
    ambassador: Ambassador, new_rank: AmbassadorRank, db: AsyncSession
) -> None:
    """Create a rank advancement bonus if not already awarded for this rank."""
    amount = RANK_ADVANCEMENT_BONUSES.get(new_rank, 0)
    if amount <= 0:
        return

    existing = await db.execute(
        select(AmbassadorBonus).where(
            AmbassadorBonus.ambassador_id == ambassador.id,
            AmbassadorBonus.bonus_type == BonusType.RANK_ADVANCEMENT,
            AmbassadorBonus.period == new_rank.value,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return

    bonus = AmbassadorBonus(
        id=uuid.uuid4(),
        ambassador_id=ambassador.id,
        bonus_type=BonusType.RANK_ADVANCEMENT,
        amount=amount,
        period=new_rank.value,
        description=f"Rank advancement bonus: promoted to {new_rank.value.replace('_', ' ').title()}",
        status=CommissionStatus.PENDING,
    )
    db.add(bonus)
    await log_activity(
        ambassador.id,
        ActivityEventType.BONUS_AWARDED,
        f"Rank advancement bonus ${amount:,.2f} awarded for reaching {new_rank.value.replace('_', ' ').title()}",
        db,
        amount=amount,
        related_id=str(bonus.id),
    )


async def _check_travel_incentive(
    ambassador: Ambassador, rank: AmbassadorRank, db: AsyncSession
) -> None:
    """Create or update travel incentive record when ambassador achieves a travel-eligible rank."""
    destination = TRAVEL_DESTINATIONS.get(rank)
    if not destination:
        return

    existing = await db.execute(
        select(AmbassadorTravelIncentive).where(
            AmbassadorTravelIncentive.ambassador_id == ambassador.id,
            AmbassadorTravelIncentive.rank_required == rank,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return

    incentive = AmbassadorTravelIncentive(
        id=uuid.uuid4(),
        ambassador_id=ambassador.id,
        destination=destination,
        rank_required=rank,
        qualification_start=utc_now(),
        status=TravelStatus.QUALIFYING,
    )
    db.add(incentive)
    logger.info(
        "Travel incentive created for ambassador %s: %s (%s)",
        ambassador.id,
        destination,
        rank.value,
    )
    await log_activity(
        ambassador.id,
        ActivityEventType.TRAVEL_AWARDED,
        f"Travel incentive unlocked: {destination} (qualifying for {rank.value.replace('_', ' ').title()})",
        db,
        related_id=str(incentive.id),
    )


# ── Monthly commission calculation ────────────────────────────────────────────


async def calculate_commission_for_month(
    ambassador: Ambassador,
    month: int,
    year: int,
    db: AsyncSession,
) -> AmbassadorCommission:
    """Calculate and persist multi-level commission for an ambassador for month/year."""
    max_depth = RANK_MAX_DEPTH.get(ambassador.rank, 1)
    levels = await get_downline_by_level(ambassador.id, max_depth, db)

    level_breakdown: dict[str, float] = {}
    total_amount = 0.0
    par_count = 0
    tav_count = 0

    for level, members in levels.items():
        if level > max_depth:
            break
        rate = LEVEL_RATES.get(level, 0.0)
        level_total = 0.0
        for member in members:
            price = await get_plan_price_for_user(member.user_id, db)
            if price > 0:
                tav_count += 1
                if level == 1:
                    par_count += 1
                level_total += price * (rate / 100)
        if level_total > 0:
            level_breakdown[str(level)] = round(level_total, 2)
            total_amount += level_total

    # Generational override: Crown+ earns 1% on subscribers beyond L10
    generational_amount = 0.0
    if ambassador.rank in GENERATIONAL_RANKS:
        # Walk beyond level 10, cap at 50 to prevent runaway
        deep_levels = await get_downline_by_level(ambassador.id, 50, db)
        for level, members in deep_levels.items():
            if level <= 10:
                continue
            for member in members:
                price = await get_plan_price_for_user(member.user_id, db)
                if price > 0:
                    generational_amount += price * 0.01
        generational_amount = round(generational_amount, 2)
        if generational_amount > 0:
            level_breakdown["generational"] = generational_amount
            total_amount += generational_amount

    total_amount = round(total_amount, 2)

    # Update cached counts on the ambassador row
    ambassador.par_count = par_count
    ambassador.tav_count = tav_count

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
            active_referral_count=par_count,
            tav_count=tav_count,
            commission_amount=total_amount,
            level_breakdown=level_breakdown,
            generational_override=generational_amount,
            status=CommissionStatus.PENDING,
        )
        db.add(commission)
    elif commission.status == CommissionStatus.PENDING:
        commission.active_referral_count = par_count
        commission.tav_count = tav_count
        commission.commission_amount = total_amount
        commission.level_breakdown = level_breakdown
        commission.generational_override = generational_amount

    await db.flush()
    if total_amount > 0:
        await log_activity(
            ambassador.id,
            ActivityEventType.COMMISSION_CALCULATED,
            f"Commission calculated for {month}/{year}: ${total_amount:,.2f} (PAR={par_count}, TAV={tav_count})",
            db,
            amount=total_amount,
            related_id=str(commission.id),
        )
    return commission


# ── Fast Start Bonus ──────────────────────────────────────────────────────────

FAST_START_BONUS = 500.0
FAST_START_TARGET = 5  # active subs needed in the window
FAST_START_PERIODS = 3  # 3 × 30-day windows


async def check_fast_start_bonus(
    ambassador: Ambassador, db: AsyncSession
) -> Optional[AmbassadorBonus]:
    """Award $500 Fast Start Bonus if 5 active subs acquired within a 30-day window.

    Runs for the first 3 × 30-day periods from ambassador creation date.
    Returns the bonus record if a new bonus was awarded, else None.
    """
    if ambassador.fast_start_claimed >= FAST_START_PERIODS:
        return None

    window_start = ambassador.created_at
    # Normalize to naive UTC for consistent SQL comparison (SQLite stores naive datetimes)
    if window_start.tzinfo is not None:
        window_start = window_start.replace(tzinfo=None)
    next_period = ambassador.fast_start_claimed + 1
    window_start_dt = window_start + timedelta(days=30 * (next_period - 1))
    window_end_dt = window_start_dt + timedelta(days=30)

    # Use second-precision string literals so the comparison works in both
    # SQLite (CURRENT_TIMESTAMP has no microseconds) and PostgreSQL.
    wstart_lit = literal(window_start_dt.strftime("%Y-%m-%d %H:%M:%S"))
    wend_lit = literal(window_end_dt.strftime("%Y-%m-%d %H:%M:%S"))

    _referral_count_query = (
        select(func.count(Ambassador.id))
        .join(User, User.id == Ambassador.user_id)
        .join(
            Subscription,
            (Subscription.user_id == User.id)
            & Subscription.status.in_(
                [SubscriptionStatus.ACTIVE, SubscriptionStatus.EXPIRING_SOON]
            ),
        )
        .where(
            Ambassador.referred_by == ambassador.id,
            Ambassador.created_at >= wstart_lit,
            Ambassador.created_at < wend_lit,
        )
    )

    # Compare with naive now for Python-level check
    now = utc_now().replace(tzinfo=None)
    result = await db.execute(_referral_count_query)
    count = result.scalar() or 0

    if now < window_end_dt:
        # Window still open
        if count < FAST_START_TARGET:
            return None
    else:
        # Window closed
        if count < FAST_START_TARGET:
            # Miss — still increment to avoid re-checking closed windows
            ambassador.fast_start_claimed = next_period
            return None

    period_key = f"fast_start_{next_period}"
    existing = await db.execute(
        select(AmbassadorBonus).where(
            AmbassadorBonus.ambassador_id == ambassador.id,
            AmbassadorBonus.bonus_type == BonusType.FAST_START,
            AmbassadorBonus.period == period_key,
        )
    )
    if existing.scalar_one_or_none() is not None:
        ambassador.fast_start_claimed = max(ambassador.fast_start_claimed, next_period)
        return None

    bonus = AmbassadorBonus(
        id=uuid.uuid4(),
        ambassador_id=ambassador.id,
        bonus_type=BonusType.FAST_START,
        amount=FAST_START_BONUS,
        period=period_key,
        description=f"Fast Start Bonus: {count} active subscribers in 30-day window {next_period}",
        status=CommissionStatus.PENDING,
    )
    db.add(bonus)
    ambassador.fast_start_claimed = next_period
    await db.flush()
    await log_activity(
        ambassador.id,
        ActivityEventType.BONUS_AWARDED,
        f"Fast Start Bonus ${FAST_START_BONUS:,.2f} awarded (period {next_period}, {count} active referrals)",
        db,
        amount=FAST_START_BONUS,
        related_id=str(bonus.id),
    )
    return bonus


# ── Loyalty Retention Bonus ───────────────────────────────────────────────────

LOYALTY_BONUS_PER_SUB = 25.0
LOYALTY_MIN_MONTHS = 12


async def calculate_loyalty_retention_bonus(
    ambassador: Ambassador, month: int, year: int, db: AsyncSession
) -> Optional[AmbassadorBonus]:
    """Award $25/month per L1 subscriber who has been active for 12+ uninterrupted months."""
    period = f"{year}-{month:02d}"

    existing = await db.execute(
        select(AmbassadorBonus).where(
            AmbassadorBonus.ambassador_id == ambassador.id,
            AmbassadorBonus.bonus_type == BonusType.LOYALTY_RETENTION,
            AmbassadorBonus.period == period,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return None

    # Get direct referrals
    refs_result = await db.execute(
        select(Ambassador).where(Ambassador.referred_by == ambassador.id)
    )
    direct_refs = refs_result.scalars().all()

    loyalty_count = 0
    cutoff = utc_now() - timedelta(days=LOYALTY_MIN_MONTHS * 30)

    for ref in direct_refs:
        sub_start = await get_subscription_start_for_user(ref.user_id, db)
        if sub_start is None:
            continue
        # Normalize timezone
        if hasattr(sub_start, "tzinfo") and sub_start.tzinfo is None:
            from datetime import timezone

            sub_start = sub_start.replace(tzinfo=timezone.utc)
        if sub_start <= cutoff:
            loyalty_count += 1

    if loyalty_count == 0:
        return None

    amount = round(loyalty_count * LOYALTY_BONUS_PER_SUB, 2)
    bonus = AmbassadorBonus(
        id=uuid.uuid4(),
        ambassador_id=ambassador.id,
        bonus_type=BonusType.LOYALTY_RETENTION,
        amount=amount,
        period=period,
        description=f"Loyalty Retention Bonus: {loyalty_count} subscriber(s) with 12+ months",
        status=CommissionStatus.PENDING,
    )
    db.add(bonus)
    await db.flush()
    return bonus


# ── Performance milestone bonuses ─────────────────────────────────────────────


async def check_performance_milestones(
    ambassador: Ambassador, db: AsyncSession
) -> list[AmbassadorBonus]:
    """Award performance milestone bonuses that have not yet been claimed."""
    awarded: list[AmbassadorBonus] = []
    par = await get_par_count(ambassador.id, db)
    total_earned = float(ambassador.rewards_earned)

    for key, threshold, metric, bonus_usd, description in PERFORMANCE_MILESTONES:
        value = par if metric == "par" else total_earned
        if value < threshold:
            continue

        existing = await db.execute(
            select(AmbassadorBonus).where(
                AmbassadorBonus.ambassador_id == ambassador.id,
                AmbassadorBonus.bonus_type == BonusType.PERFORMANCE_MILESTONE,
                AmbassadorBonus.period == key,
            )
        )
        if existing.scalar_one_or_none() is not None:
            continue

        bonus = AmbassadorBonus(
            id=uuid.uuid4(),
            ambassador_id=ambassador.id,
            bonus_type=BonusType.PERFORMANCE_MILESTONE,
            amount=float(bonus_usd),
            period=key,
            description=description,
            status=CommissionStatus.PENDING,
        )
        db.add(bonus)
        awarded.append(bonus)
        await log_activity(
            ambassador.id,
            ActivityEventType.BONUS_AWARDED,
            f"Performance milestone bonus ${float(bonus_usd):,.2f} — {description}",
            db,
            amount=float(bonus_usd),
            related_id=str(bonus.id),
        )

    if awarded:
        await db.flush()
    return awarded


# ── Leadership Revenue Pool ────────────────────────────────────────────────────

LEADERSHIP_POOL_PCT = 2.0  # 2% of global subscription revenue
DIAMOND_RANKS = {
    AmbassadorRank.DIAMOND_LEADER,
    AmbassadorRank.ELITE_DIAMOND,
    AmbassadorRank.BLACK_DIAMOND,
    AmbassadorRank.CROWN_AMBASSADOR,
    AmbassadorRank.GRAND_CROWN,
}


async def calculate_leadership_pool(
    month: int, year: int, db: AsyncSession
) -> LeadershipRevenuePool:
    """Calculate the 2% leadership pool for Diamond+ ambassadors.

    Should be called once per month (last business day) by admin trigger.
    """
    # Total global subscription revenue this month:
    # count active subscriptions × plan price
    rev_result = await db.execute(
        select(func.sum(Plan.price_usd))
        .join(Subscription, Subscription.plan_id == Plan.id)
        .where(
            Subscription.status.in_(
                [SubscriptionStatus.ACTIVE, SubscriptionStatus.EXPIRING_SOON]
            )
        )
    )
    global_revenue = float(rev_result.scalar() or 0)
    pool_amount = round(global_revenue * (LEADERSHIP_POOL_PCT / 100), 2)

    # Count Diamond+ ambassadors
    diamond_result = await db.execute(
        select(func.count(Ambassador.id)).where(
            Ambassador.rank.in_([r.value for r in DIAMOND_RANKS])
        )
    )
    diamond_count = diamond_result.scalar() or 0

    per_ambassador = round(pool_amount / diamond_count, 2) if diamond_count > 0 else 0.0

    # Upsert pool record
    existing = await db.execute(
        select(LeadershipRevenuePool).where(
            LeadershipRevenuePool.month == month,
            LeadershipRevenuePool.year == year,
        )
    )
    pool = existing.scalar_one_or_none()

    if pool is None:
        pool = LeadershipRevenuePool(
            id=uuid.uuid4(),
            month=month,
            year=year,
            total_pool_amount=pool_amount,
            eligible_ambassador_count=diamond_count,
            per_ambassador_amount=per_ambassador,
            status=CommissionStatus.PENDING,
        )
        db.add(pool)
    elif pool.status == CommissionStatus.PENDING:
        pool.total_pool_amount = pool_amount
        pool.eligible_ambassador_count = diamond_count
        pool.per_ambassador_amount = per_ambassador

    # Create LEADERSHIP_POOL bonus records for each Diamond+ ambassador
    if per_ambassador > 0:
        amb_result = await db.execute(
            select(Ambassador).where(
                Ambassador.rank.in_([r.value for r in DIAMOND_RANKS])
            )
        )
        diamond_ambassadors = amb_result.scalars().all()
        period = f"{year}-{month:02d}"

        for amb in diamond_ambassadors:
            existing_bonus = await db.execute(
                select(AmbassadorBonus).where(
                    AmbassadorBonus.ambassador_id == amb.id,
                    AmbassadorBonus.bonus_type == BonusType.LEADERSHIP_POOL,
                    AmbassadorBonus.period == period,
                )
            )
            if existing_bonus.scalar_one_or_none() is None:
                bonus = AmbassadorBonus(
                    id=uuid.uuid4(),
                    ambassador_id=amb.id,
                    bonus_type=BonusType.LEADERSHIP_POOL,
                    amount=per_ambassador,
                    period=period,
                    description=f"Leadership Revenue Pool share — {period}",
                    status=CommissionStatus.PENDING,
                )
                db.add(bonus)
                await log_activity(
                    amb.id,
                    ActivityEventType.POOL_CALCULATED,
                    f"Leadership pool share ${per_ambassador:,.2f} for {period} (total pool ${pool_amount:,.2f})",
                    db,
                    amount=per_ambassador,
                    related_id=str(pool.id),
                )

    await db.flush()
    return pool


# ── Full monthly run helper ────────────────────────────────────────────────────


async def run_monthly_ambassador_cycle(month: int, year: int, db: AsyncSession) -> dict:
    """Run the complete monthly ambassador cycle for all ambassadors.

    Called by admin endpoint or scheduled task on the last business day of the month.
    Returns a summary dict.
    """
    all_result = await db.execute(select(Ambassador))
    ambassadors = all_result.scalars().all()

    commissions_created = 0
    bonuses_created = 0
    ranks_changed = 0

    for amb in ambassadors:
        # 1. Evaluate rank qualification (may auto-promote or demote)
        new_rank = await evaluate_and_update_rank(amb, db)
        if new_rank is not None:
            ranks_changed += 1

        # 2. Calculate multi-level commission
        comm = await calculate_commission_for_month(amb, month, year, db)
        if float(comm.commission_amount) > 0:
            commissions_created += 1
            # Update cumulative rewards
            amb.rewards_earned = float(amb.rewards_earned) + float(
                comm.commission_amount
            )

        # 3. Loyalty retention bonus
        loyalty = await calculate_loyalty_retention_bonus(amb, month, year, db)
        if loyalty:
            bonuses_created += 1
            amb.rewards_earned = float(amb.rewards_earned) + float(loyalty.amount)

        # 4. Fast start bonus
        fast = await check_fast_start_bonus(amb, db)
        if fast:
            bonuses_created += 1
            amb.rewards_earned = float(amb.rewards_earned) + float(fast.amount)

        # 5. Performance milestones
        milestones = await check_performance_milestones(amb, db)
        for m in milestones:
            bonuses_created += 1
            amb.rewards_earned = float(amb.rewards_earned) + float(m.amount)

        # 6. Update total_referrals count
        total_result = await db.execute(
            select(func.count(Ambassador.id)).where(Ambassador.referred_by == amb.id)
        )
        amb.total_referrals = total_result.scalar() or 0

    # 7. Leadership revenue pool (Diamond+)
    pool = await calculate_leadership_pool(month, year, db)

    await db.flush()

    return {
        "month": month,
        "year": year,
        "ambassadors_processed": len(ambassadors),
        "commissions_created": commissions_created,
        "bonuses_created": bonuses_created,
        "ranks_changed": ranks_changed,
        "leadership_pool_amount": float(pool.total_pool_amount),
        "pool_per_ambassador": float(pool.per_ambassador_amount),
    }


# ── Backwards-compatible helpers used by routers ─────────────────────────────


async def get_active_referrals(
    ambassador_id: uuid.UUID, db: AsyncSession
) -> list[Ambassador]:
    """Return direct referrals whose users have an active subscription."""
    result = await db.execute(
        select(Ambassador)
        .join(User, User.id == Ambassador.user_id)
        .join(
            Subscription,
            (Subscription.user_id == User.id)
            & Subscription.status.in_(
                [SubscriptionStatus.ACTIVE, SubscriptionStatus.EXPIRING_SOON]
            ),
        )
        .where(Ambassador.referred_by == ambassador_id)
    )
    return list(result.scalars().all())
