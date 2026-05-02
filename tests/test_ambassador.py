"""E2E tests for the Ambassador V2 system.

Covers:
- Service layer: multi-level commission, rank qualification, bonus calculations
- User API: referral generation, dashboard, earnings summary, training
- Admin API: list/get, promote, evaluate rank, bonuses, payouts, travel, pool, cycle
"""

import uuid

import pytest
from sqlalchemy import func, select

from tests.conftest import TestSessionFactory
from app.models.ambassador import Ambassador
from app.models.ambassador_programs import (
    AmbassadorBonus,
    AmbassadorCommission,
    AmbassadorTravelIncentive,
)
from app.models.billing import Plan, Subscription
from app.models.enums import (
    AmbassadorRank,
    BillingCycle,
    BonusType,
    CommissionStatus,
    PlanStatus,
    SubscriptionStatus,
)
from app.models.user import User
from app.services.ambassador import (
    DIAMOND_RANKS,
    FAST_START_BONUS,
    RANK_ADVANCEMENT_BONUSES,
    calculate_commission_for_month,
    calculate_leadership_pool,
    check_fast_start_bonus,
    check_performance_milestones,
    check_rank_qualification,
    evaluate_and_update_rank,
    get_par_count,
    get_tav_count,
)
from app.utils.helpers import add_days, utc_now
from app.utils.security import create_access_token


# ── Shared test helpers ───────────────────────────────────────────────────────


async def _make_plan(price: float = 250.0) -> Plan:
    async with TestSessionFactory() as db:
        plan = Plan(
            name=f"Plan-{uuid.uuid4().hex[:6]}",
            price_usd=price,
            billing_cycle=BillingCycle.MONTHLY,
            max_strategies=10,
            max_allocation=0,
            ate_access=True,
            trial_days=0,
            features={},
            status=PlanStatus.ACTIVE,
        )
        db.add(plan)
        await db.commit()
        await db.refresh(plan)
        return plan


async def _make_user(
    subscribed: bool = False, plan: Plan | None = None
) -> tuple[User, dict]:
    """Create a verified user and optionally attach an active subscription."""
    uid = uuid.uuid4()
    async with TestSessionFactory() as db:
        user = User(
            id=uid,
            email=f"u-{uid}@test.local",
            email_verified=True,
            is_active=True,
            is_subscribed=subscribed,
        )
        db.add(user)
        await db.flush()

        if subscribed and plan:
            now = utc_now()
            sub = Subscription(
                user_id=uid,
                plan_id=plan.id,
                status=SubscriptionStatus.ACTIVE,
                started_at=now,
                expires_at=add_days(now, 30),
            )
            db.add(sub)

        await db.commit()
        await db.refresh(user)

    headers = {"Authorization": f"Bearer {create_access_token(str(uid))}"}
    return user, headers


async def _make_ambassador(
    user_id: uuid.UUID,
    referred_by: uuid.UUID | None = None,
    rank: AmbassadorRank = AmbassadorRank.ASSOCIATE,
) -> Ambassador:
    async with TestSessionFactory() as db:
        amb = Ambassador(
            user_id=user_id,
            referral_code=f"REF{uuid.uuid4().hex[:8].upper()}",
            referred_by=referred_by,
            rank=rank,
        )
        db.add(amb)
        await db.commit()
        await db.refresh(amb)
        return amb


async def _fresh(ambassador_id: uuid.UUID) -> Ambassador:
    """Reload ambassador from DB in a fresh session."""
    async with TestSessionFactory() as db:
        result = await db.execute(
            select(Ambassador).where(Ambassador.id == ambassador_id)
        )
        return result.scalar_one()


# ── Service layer: PAR / TAV counts ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_par_counts_only_subscribed_direct_referrals(setup_db):
    """PAR = direct referrals with active subscriptions only."""
    plan = await _make_plan()
    top_user, _ = await _make_user()
    top = await _make_ambassador(top_user.id)

    sub_user, _ = await _make_user(subscribed=True, plan=plan)
    unsub_user, _ = await _make_user(subscribed=False)

    await _make_ambassador(sub_user.id, referred_by=top.id)
    await _make_ambassador(unsub_user.id, referred_by=top.id)

    async with TestSessionFactory() as db:
        fresh_top = await db.get(Ambassador, top.id)
        par = await get_par_count(fresh_top.id, db)

    assert par == 1


@pytest.mark.asyncio
async def test_tav_includes_all_depth_levels(setup_db):
    """TAV counts active subscribers at any depth (L1 + L2)."""
    plan = await _make_plan()
    top_user, _ = await _make_user()
    top = await _make_ambassador(top_user.id)

    l1_user, _ = await _make_user(subscribed=True, plan=plan)
    l1 = await _make_ambassador(l1_user.id, referred_by=top.id)

    l2_user, _ = await _make_user(subscribed=True, plan=plan)
    await _make_ambassador(l2_user.id, referred_by=l1.id)

    async with TestSessionFactory() as db:
        fresh_top = await db.get(Ambassador, top.id)
        tav = await get_tav_count(fresh_top.id, db)

    assert tav == 2


# ── Service layer: commission calculation ─────────────────────────────────────


@pytest.mark.asyncio
async def test_l1_commission_is_25_pct(setup_db):
    """L1 referral on a $250 plan earns 25% = $62.50."""
    plan = await _make_plan(250.0)
    top_user, _ = await _make_user()
    top = await _make_ambassador(top_user.id)

    member_user, _ = await _make_user(subscribed=True, plan=plan)
    await _make_ambassador(member_user.id, referred_by=top.id)

    async with TestSessionFactory() as db:
        fresh_top = await db.get(Ambassador, top.id)
        comm = await calculate_commission_for_month(fresh_top, 5, 2026, db)
        await db.commit()

    assert round(float(comm.commission_amount), 2) == 62.50
    assert round(float(comm.level_breakdown.get("1", 0)), 2) == 62.50


@pytest.mark.asyncio
async def test_l2_commission_is_8_pct(setup_db):
    """Silver Leader earns L1 25% + L2 8% on a $250 plan → $82.50 total."""
    plan = await _make_plan(250.0)
    top_user, _ = await _make_user()
    top = await _make_ambassador(top_user.id, rank=AmbassadorRank.SILVER_LEADER)

    l1_user, _ = await _make_user(subscribed=True, plan=plan)
    l1 = await _make_ambassador(l1_user.id, referred_by=top.id)

    l2_user, _ = await _make_user(subscribed=True, plan=plan)
    await _make_ambassador(l2_user.id, referred_by=l1.id)

    async with TestSessionFactory() as db:
        fresh_top = await db.get(Ambassador, top.id)
        comm = await calculate_commission_for_month(fresh_top, 5, 2026, db)
        await db.commit()

    assert round(float(comm.commission_amount), 2) == 82.50
    assert round(float(comm.level_breakdown.get("1", 0)), 2) == 62.50
    assert round(float(comm.level_breakdown.get("2", 0)), 2) == 20.00


@pytest.mark.asyncio
async def test_associate_cannot_earn_l2_commission(setup_db):
    """Associate (max_depth=1) earns L1 only, not L2."""
    plan = await _make_plan(250.0)
    top_user, _ = await _make_user()
    top = await _make_ambassador(top_user.id, rank=AmbassadorRank.ASSOCIATE)

    l1_user, _ = await _make_user(subscribed=True, plan=plan)
    l1 = await _make_ambassador(l1_user.id, referred_by=top.id)

    l2_user, _ = await _make_user(subscribed=True, plan=plan)
    await _make_ambassador(l2_user.id, referred_by=l1.id)

    async with TestSessionFactory() as db:
        fresh_top = await db.get(Ambassador, top.id)
        comm = await calculate_commission_for_month(fresh_top, 5, 2026, db)
        await db.commit()

    assert round(float(comm.commission_amount), 2) == 62.50
    assert "2" not in (comm.level_breakdown or {})


@pytest.mark.asyncio
async def test_commission_upsert_on_recalculate(setup_db):
    """Recalculating a pending commission updates it in place."""
    plan = await _make_plan(250.0)
    top_user, _ = await _make_user()
    top = await _make_ambassador(top_user.id)

    member_user, _ = await _make_user(subscribed=True, plan=plan)
    await _make_ambassador(member_user.id, referred_by=top.id)

    async with TestSessionFactory() as db:
        fresh_top = await db.get(Ambassador, top.id)
        comm1 = await calculate_commission_for_month(fresh_top, 5, 2026, db)
        await db.commit()

    async with TestSessionFactory() as db:
        fresh_top = await db.get(Ambassador, top.id)
        comm2 = await calculate_commission_for_month(fresh_top, 5, 2026, db)
        await db.commit()

    assert comm1.id == comm2.id  # same record, not duplicated


# ── Service layer: rank qualification ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_bronze_leader_qualifies_with_3_par(setup_db):
    """3 PAR + 3 TAV (no legs) → qualifies for Bronze Leader."""
    plan = await _make_plan()
    leader_user, _ = await _make_user()
    leader = await _make_ambassador(leader_user.id)

    for _ in range(3):
        u, _ = await _make_user(subscribed=True, plan=plan)
        await _make_ambassador(u.id, referred_by=leader.id)

    async with TestSessionFactory() as db:
        fresh_leader = await db.get(Ambassador, leader.id)
        qualifies = await check_rank_qualification(
            fresh_leader, AmbassadorRank.BRONZE_LEADER, db
        )

    assert qualifies is True


@pytest.mark.asyncio
async def test_bronze_leader_fails_with_2_par(setup_db):
    """2 PAR is not enough for Bronze Leader (needs 3)."""
    plan = await _make_plan()
    leader_user, _ = await _make_user()
    leader = await _make_ambassador(leader_user.id)

    for _ in range(2):
        u, _ = await _make_user(subscribed=True, plan=plan)
        await _make_ambassador(u.id, referred_by=leader.id)

    async with TestSessionFactory() as db:
        fresh_leader = await db.get(Ambassador, leader.id)
        qualifies = await check_rank_qualification(
            fresh_leader, AmbassadorRank.BRONZE_LEADER, db
        )

    assert qualifies is False


@pytest.mark.asyncio
async def test_evaluate_rank_promotes_associate_to_bronze(setup_db):
    """evaluate_and_update_rank promotes associate → bronze when criteria met."""
    plan = await _make_plan()
    leader_user, _ = await _make_user()
    leader = await _make_ambassador(leader_user.id, rank=AmbassadorRank.ASSOCIATE)

    for _ in range(3):
        u, _ = await _make_user(subscribed=True, plan=plan)
        await _make_ambassador(u.id, referred_by=leader.id)

    async with TestSessionFactory() as db:
        fresh_leader = await db.get(Ambassador, leader.id)
        new_rank = await evaluate_and_update_rank(fresh_leader, db)
        await db.commit()

    assert new_rank == AmbassadorRank.BRONZE_LEADER


@pytest.mark.asyncio
async def test_evaluate_rank_creates_advancement_bonus(setup_db):
    """Rank advancement bonus is created when rank is upgraded."""
    plan = await _make_plan()
    leader_user, _ = await _make_user()
    leader = await _make_ambassador(leader_user.id, rank=AmbassadorRank.ASSOCIATE)

    for _ in range(3):
        u, _ = await _make_user(subscribed=True, plan=plan)
        await _make_ambassador(u.id, referred_by=leader.id)

    async with TestSessionFactory() as db:
        fresh_leader = await db.get(Ambassador, leader.id)
        await evaluate_and_update_rank(fresh_leader, db)
        await db.commit()

    async with TestSessionFactory() as db:
        result = await db.execute(
            select(AmbassadorBonus).where(
                AmbassadorBonus.ambassador_id == leader.id,
                AmbassadorBonus.bonus_type == BonusType.RANK_ADVANCEMENT,
            )
        )
        bonus = result.scalar_one_or_none()

    assert bonus is not None
    assert float(bonus.amount) == RANK_ADVANCEMENT_BONUSES[AmbassadorRank.BRONZE_LEADER]


@pytest.mark.asyncio
async def test_evaluate_rank_returns_none_when_unchanged(setup_db):
    """evaluate_and_update_rank returns None when rank does not change."""
    leader_user, _ = await _make_user()
    leader = await _make_ambassador(leader_user.id, rank=AmbassadorRank.ASSOCIATE)

    async with TestSessionFactory() as db:
        fresh_leader = await db.get(Ambassador, leader.id)
        result = await evaluate_and_update_rank(fresh_leader, db)

    assert result is None


# ── Service layer: bonus calculations ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_fast_start_bonus_at_5_referrals(setup_db):
    """Fast Start Bonus ($500) is awarded when 5 active subs are recruited."""
    plan = await _make_plan()
    leader_user, _ = await _make_user()
    leader = await _make_ambassador(leader_user.id)

    for _ in range(5):
        u, _ = await _make_user(subscribed=True, plan=plan)
        await _make_ambassador(u.id, referred_by=leader.id)

    async with TestSessionFactory() as db:
        fresh_leader = await db.get(Ambassador, leader.id)
        bonus = await check_fast_start_bonus(fresh_leader, db)
        await db.commit()

    assert bonus is not None
    assert float(bonus.amount) == FAST_START_BONUS
    assert bonus.bonus_type == BonusType.FAST_START


@pytest.mark.asyncio
async def test_fast_start_not_awarded_below_threshold(setup_db):
    """Fast Start Bonus is NOT awarded with fewer than 5 subs."""
    plan = await _make_plan()
    leader_user, _ = await _make_user()
    leader = await _make_ambassador(leader_user.id)

    for _ in range(4):
        u, _ = await _make_user(subscribed=True, plan=plan)
        await _make_ambassador(u.id, referred_by=leader.id)

    async with TestSessionFactory() as db:
        fresh_leader = await db.get(Ambassador, leader.id)
        bonus = await check_fast_start_bonus(fresh_leader, db)

    assert bonus is None


@pytest.mark.asyncio
async def test_fast_start_not_awarded_twice(setup_db):
    """Fast Start Bonus for the same period is not awarded twice."""
    plan = await _make_plan()
    leader_user, _ = await _make_user()
    leader = await _make_ambassador(leader_user.id)

    for _ in range(5):
        u, _ = await _make_user(subscribed=True, plan=plan)
        await _make_ambassador(u.id, referred_by=leader.id)

    async with TestSessionFactory() as db:
        fresh_leader = await db.get(Ambassador, leader.id)
        b1 = await check_fast_start_bonus(fresh_leader, db)
        await db.commit()

    async with TestSessionFactory() as db:
        fresh_leader = await db.get(Ambassador, leader.id)
        b2 = await check_fast_start_bonus(fresh_leader, db)

    assert b1 is not None
    assert b2 is None  # already claimed


@pytest.mark.asyncio
async def test_performance_milestone_par_5_bonus(setup_db):
    """Performance milestone $100 is awarded when PAR reaches 5."""
    plan = await _make_plan()
    leader_user, _ = await _make_user()
    leader = await _make_ambassador(leader_user.id)

    for _ in range(5):
        u, _ = await _make_user(subscribed=True, plan=plan)
        await _make_ambassador(u.id, referred_by=leader.id)

    async with TestSessionFactory() as db:
        fresh_leader = await db.get(Ambassador, leader.id)
        bonuses = await check_performance_milestones(fresh_leader, db)
        await db.commit()

    keys = [b.period for b in bonuses]
    assert "par_5" in keys
    par5 = next(b for b in bonuses if b.period == "par_5")
    assert float(par5.amount) == 100.0


@pytest.mark.asyncio
async def test_performance_milestone_not_awarded_twice(setup_db):
    """Performance milestone is idempotent — not awarded a second time."""
    plan = await _make_plan()
    leader_user, _ = await _make_user()
    leader = await _make_ambassador(leader_user.id)

    for _ in range(5):
        u, _ = await _make_user(subscribed=True, plan=plan)
        await _make_ambassador(u.id, referred_by=leader.id)

    async with TestSessionFactory() as db:
        fresh_leader = await db.get(Ambassador, leader.id)
        b1 = await check_performance_milestones(fresh_leader, db)
        await db.commit()

    async with TestSessionFactory() as db:
        fresh_leader = await db.get(Ambassador, leader.id)
        b2 = await check_performance_milestones(fresh_leader, db)

    assert len(b1) > 0
    assert len(b2) == 0


# ── Service layer: leadership pool ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_leadership_pool_2pct_of_global_revenue(setup_db):
    """Pool = 2% of global subscription revenue, split among Diamond+ ambassadors."""
    plan = await _make_plan(500.0)

    for _ in range(2):
        u, _ = await _make_user(subscribed=True, plan=plan)

    diamond_user, _ = await _make_user()
    await _make_ambassador(diamond_user.id, rank=AmbassadorRank.DIAMOND_LEADER)

    # Query actual global revenue so the assertion is resilient to shared-DB state
    async with TestSessionFactory() as db:
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
        diamond_result = await db.execute(
            select(func.count(Ambassador.id)).where(
                Ambassador.rank.in_([r.value for r in DIAMOND_RANKS])
            )
        )
        diamond_count = diamond_result.scalar() or 0

        pool = await calculate_leadership_pool(5, 2026, db)
        await db.commit()

    expected_pool = round(global_revenue * 0.02, 2)
    assert float(pool.total_pool_amount) == expected_pool
    assert pool.eligible_ambassador_count == diamond_count
    assert diamond_count >= 1  # at least the one we just created
    assert float(pool.per_ambassador_amount) == round(expected_pool / diamond_count, 2)


@pytest.mark.asyncio
async def test_leadership_pool_zero_when_no_diamond_ambassadors(setup_db):
    """per_ambassador_amount = 0 when eligible_ambassador_count == 0 (math invariant)."""
    async with TestSessionFactory() as db:
        diamond_result = await db.execute(
            select(func.count(Ambassador.id)).where(
                Ambassador.rank.in_([r.value for r in DIAMOND_RANKS])
            )
        )
        diamond_count = diamond_result.scalar() or 0

        pool = await calculate_leadership_pool(6, 2026, db)
        await db.commit()

    if diamond_count == 0:
        assert float(pool.per_ambassador_amount) == 0.0
    else:
        # Diamond ambassadors from earlier tests are present; verify the math holds
        assert pool.eligible_ambassador_count == diamond_count
        assert float(pool.per_ambassador_amount) > 0.0


# ── User API endpoint tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_referral_creates_ambassador(client, setup_db, test_user):
    """POST /ambassador/referral creates an ambassador and returns code + link."""
    r = await client.post("/api/v1/ambassador/referral", headers=test_user["headers"])
    assert r.status_code == 200
    data = r.json()
    assert data["referral_code"]
    assert "referral_link" in data


@pytest.mark.asyncio
async def test_generate_referral_is_idempotent(client, setup_db, test_user):
    """Calling POST /ambassador/referral twice returns the same code."""
    r1 = await client.post("/api/v1/ambassador/referral", headers=test_user["headers"])
    r2 = await client.post("/api/v1/ambassador/referral", headers=test_user["headers"])
    assert r1.json()["referral_code"] == r2.json()["referral_code"]


@pytest.mark.asyncio
async def test_dashboard_returns_ambassador(client, setup_db, test_user):
    """GET /ambassador returns ambassador after it's been created."""
    await client.post("/api/v1/ambassador/referral", headers=test_user["headers"])
    r = await client.get("/api/v1/ambassador", headers=test_user["headers"])
    assert r.status_code == 200
    data = r.json()
    assert data["rank"] == "associate"
    assert data["par_count"] == 0


@pytest.mark.asyncio
async def test_dashboard_returns_null_before_registration(client, setup_db):
    """GET /ambassador returns null when user has no ambassador profile yet."""
    _, headers = await _make_user()
    r = await client.get("/api/v1/ambassador", headers=headers)
    assert r.status_code == 200
    assert r.json() is None


@pytest.mark.asyncio
async def test_earnings_summary_defaults_for_new_ambassador(
    client, setup_db, test_user
):
    """Earnings summary for fresh ambassador has zeroed-out financials."""
    await client.post("/api/v1/ambassador/referral", headers=test_user["headers"])
    r = await client.get(
        "/api/v1/ambassador/earnings-summary", headers=test_user["headers"]
    )
    assert r.status_code == 200
    data = r.json()
    assert data["total_commission_paid"] == 0.0
    assert data["rank_progress"]["current_rank"] == "associate"
    assert data["rank_progress"]["next_rank"] == "bronze_leader"
    assert data["fast_start_eligible"] is True


@pytest.mark.asyncio
async def test_payout_address_update(client, setup_db, test_user):
    """PATCH /ambassador/payout-address sets the payout address."""
    r = await client.patch(
        "/api/v1/ambassador/payout-address",
        headers=test_user["headers"],
        json={"payout_address": "0xDEADBEEF12345678"},
    )
    assert r.status_code == 200
    assert r.json()["payout_address"] == "0xDEADBEEF12345678"


@pytest.mark.asyncio
async def test_leaderboard_returns_list(client, setup_db, test_user):
    """GET /ambassador/leaderboard returns a list."""
    await client.post("/api/v1/ambassador/referral", headers=test_user["headers"])
    r = await client.get("/api/v1/ambassador/leaderboard", headers=test_user["headers"])
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_training_modules_have_v2_ranks(client, setup_db, test_user):
    """GET /ambassador/training returns modules with V2 rank labels."""
    r = await client.get("/api/v1/ambassador/training", headers=test_user["headers"])
    assert r.status_code == 200
    data = r.json()
    assert data["total_count"] > 0
    valid_ranks = {
        "associate",
        "bronze_leader",
        "silver_leader",
        "gold_leader",
        "platinum_leader",
        "diamond_leader",
        "elite_diamond",
        "black_diamond",
        "crown_ambassador",
        "grand_crown",
    }
    for m in data["modules"]:
        assert m["rank"] in valid_ranks


@pytest.mark.asyncio
async def test_commissions_empty_for_new_ambassador(client, setup_db, test_user):
    """GET /ambassador/commissions returns empty list for a fresh ambassador."""
    await client.post("/api/v1/ambassador/referral", headers=test_user["headers"])
    r = await client.get("/api/v1/ambassador/commissions", headers=test_user["headers"])
    assert r.status_code == 200
    assert r.json()["total"] == 0


@pytest.mark.asyncio
async def test_bonuses_empty_for_new_ambassador(client, setup_db, test_user):
    """GET /ambassador/bonuses returns empty for a fresh ambassador."""
    await client.post("/api/v1/ambassador/referral", headers=test_user["headers"])
    r = await client.get("/api/v1/ambassador/bonuses", headers=test_user["headers"])
    assert r.status_code == 200
    assert r.json()["total"] == 0


@pytest.mark.asyncio
async def test_travel_empty_for_new_ambassador(client, setup_db, test_user):
    """GET /ambassador/travel returns [] for a fresh ambassador."""
    await client.post("/api/v1/ambassador/referral", headers=test_user["headers"])
    r = await client.get("/api/v1/ambassador/travel", headers=test_user["headers"])
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_referrals_list_shows_subscribed_status(client, setup_db):
    """GET /ambassador/referrals shows referral with is_subscribed=True when subscribed."""
    plan = await _make_plan(250.0)
    leader_user, leader_headers = await _make_user()

    r = await client.post("/api/v1/ambassador/referral", headers=leader_headers)
    referral_code = r.json()["referral_code"]

    # Create a referred user with subscription
    ref_user, _ = await _make_user(subscribed=True, plan=plan)
    async with TestSessionFactory() as db:
        result = await db.execute(
            select(Ambassador).where(Ambassador.referral_code == referral_code)
        )
        leader_amb = result.scalar_one()
    await _make_ambassador(ref_user.id, referred_by=leader_amb.id)

    r = await client.get("/api/v1/ambassador/referrals", headers=leader_headers)
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 1
    assert items[0]["is_subscribed"] is True
    assert items[0]["rank"] == "associate"


# ── Admin API endpoint tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_list_ambassadors_paginated(client, setup_db, admin_user):
    """GET /admin/ambassadors returns paginated list."""
    r = await client.get("/api/v1/admin/ambassadors", headers=admin_user["headers"])
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert "total" in data
    assert "total_pages" in data


@pytest.mark.asyncio
async def test_admin_list_ambassadors_filter_rank(client, setup_db, admin_user):
    """GET /admin/ambassadors?rank=associate returns only associates."""
    r = await client.get(
        "/api/v1/admin/ambassadors?rank=associate", headers=admin_user["headers"]
    )
    assert r.status_code == 200
    for item in r.json()["items"]:
        assert item["rank"] == "associate"


@pytest.mark.asyncio
async def test_admin_get_ambassador_returns_details(client, setup_db, admin_user):
    """GET /admin/ambassadors/{id} returns ambassador details."""
    u, _ = await _make_user()
    amb = await _make_ambassador(u.id)

    r = await client.get(
        f"/api/v1/admin/ambassadors/{amb.id}", headers=admin_user["headers"]
    )
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == str(amb.id)
    assert data["rank"] == "associate"
    assert "par_count" in data
    assert "tav_count" in data


@pytest.mark.asyncio
async def test_admin_get_ambassador_404(client, setup_db, admin_user):
    """GET /admin/ambassadors/{nonexistent} returns 404."""
    r = await client.get(
        f"/api/v1/admin/ambassadors/{uuid.uuid4()}", headers=admin_user["headers"]
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_admin_promote_ambassador_changes_rank(client, setup_db, admin_user):
    """POST /admin/ambassadors/{id}/promote changes rank to specified value."""
    u, _ = await _make_user()
    amb = await _make_ambassador(u.id, rank=AmbassadorRank.ASSOCIATE)

    r = await client.post(
        f"/api/v1/admin/ambassadors/{amb.id}/promote",
        headers=admin_user["headers"],
        json={"rank": "bronze_leader"},
    )
    assert r.status_code == 200
    assert r.json()["rank"] == "bronze_leader"


@pytest.mark.asyncio
async def test_admin_promote_creates_advancement_bonus(client, setup_db, admin_user):
    """Promoting to Gold Leader creates a $2500 rank advancement bonus."""
    u, _ = await _make_user()
    amb = await _make_ambassador(u.id, rank=AmbassadorRank.ASSOCIATE)

    await client.post(
        f"/api/v1/admin/ambassadors/{amb.id}/promote",
        headers=admin_user["headers"],
        json={"rank": "gold_leader"},
    )

    async with TestSessionFactory() as db:
        result = await db.execute(
            select(AmbassadorBonus).where(
                AmbassadorBonus.ambassador_id == amb.id,
                AmbassadorBonus.bonus_type == BonusType.RANK_ADVANCEMENT,
            )
        )
        bonus = result.scalar_one_or_none()

    assert bonus is not None
    assert float(bonus.amount) == RANK_ADVANCEMENT_BONUSES[AmbassadorRank.GOLD_LEADER]


@pytest.mark.asyncio
async def test_admin_evaluate_rank_no_change(client, setup_db, admin_user):
    """POST /admin/ambassadors/{id}/evaluate-rank returns rank_changed=False with no criteria met."""
    u, _ = await _make_user()
    amb = await _make_ambassador(u.id, rank=AmbassadorRank.ASSOCIATE)

    r = await client.post(
        f"/api/v1/admin/ambassadors/{amb.id}/evaluate-rank",
        headers=admin_user["headers"],
    )
    assert r.status_code == 200
    data = r.json()
    assert data["rank_changed"] is False
    assert data["old_rank"] == "associate"
    assert data["new_rank"] == "associate"


@pytest.mark.asyncio
async def test_admin_create_manual_bonus(client, setup_db, admin_user):
    """POST /admin/ambassadors/{id}/bonus creates a manual bonus record."""
    u, _ = await _make_user()
    amb = await _make_ambassador(u.id)

    r = await client.post(
        f"/api/v1/admin/ambassadors/{amb.id}/bonus",
        headers=admin_user["headers"],
        json={
            "bonus_type": "performance_milestone",
            "amount": 500.0,
            "description": "Manual performance award",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["bonus_type"] == "performance_milestone"
    assert data["amount"] == 500.0
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_admin_create_payout_empty(client, setup_db, admin_user):
    """POST /admin/ambassadors/{id}/payout creates a $0 payout with empty lists."""
    u, _ = await _make_user()
    amb = await _make_ambassador(u.id)

    r = await client.post(
        f"/api/v1/admin/ambassadors/{amb.id}/payout",
        headers=admin_user["headers"],
        json={"commission_ids": [], "bonus_ids": [], "admin_notes": "Test"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "pending"
    assert data["total_amount"] == 0.0


@pytest.mark.asyncio
async def test_admin_create_payout_marks_commission_paid(client, setup_db, admin_user):
    """Creating a payout with a commission_id marks that commission as paid."""
    u, _ = await _make_user()
    amb = await _make_ambassador(u.id)

    comm_id = uuid.uuid4()
    async with TestSessionFactory() as db:
        comm = AmbassadorCommission(
            id=comm_id,
            ambassador_id=amb.id,
            month=5,
            year=2026,
            commission_amount=62.50,
            active_referral_count=1,
            tav_count=1,
            level_breakdown={"1": 62.50},
            status=CommissionStatus.PENDING,
        )
        db.add(comm)
        await db.commit()

    r = await client.post(
        f"/api/v1/admin/ambassadors/{amb.id}/payout",
        headers=admin_user["headers"],
        json={"commission_ids": [str(comm_id)], "bonus_ids": []},
    )
    assert r.status_code == 200
    assert r.json()["total_amount"] == 62.50

    async with TestSessionFactory() as db:
        result = await db.execute(
            select(AmbassadorCommission).where(AmbassadorCommission.id == comm_id)
        )
        updated = result.scalar_one()
    assert updated.status == CommissionStatus.PAID


@pytest.mark.asyncio
async def test_admin_list_payouts(client, setup_db, admin_user):
    """GET /admin/ambassadors/payouts returns paginated payouts."""
    r = await client.get(
        "/api/v1/admin/ambassadors/payouts", headers=admin_user["headers"]
    )
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_admin_update_payout_to_processing(client, setup_db, admin_user):
    """PATCH /admin/ambassadors/payouts/{id} updates status to processing."""
    u, _ = await _make_user()
    amb = await _make_ambassador(u.id)

    create_r = await client.post(
        f"/api/v1/admin/ambassadors/{amb.id}/payout",
        headers=admin_user["headers"],
        json={"commission_ids": [], "bonus_ids": []},
    )
    payout_id = create_r.json()["id"]

    r = await client.patch(
        f"/api/v1/admin/ambassadors/payouts/{payout_id}",
        headers=admin_user["headers"],
        json={"status": "processing"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "processing"


@pytest.mark.asyncio
async def test_admin_recalculate_commissions_for_all(client, setup_db, admin_user):
    """POST /admin/ambassadors/commissions/recalculate returns processed count."""
    r = await client.post(
        "/api/v1/admin/ambassadors/commissions/recalculate",
        headers=admin_user["headers"],
        json={"month": 5, "year": 2026},
    )
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data["processed"], int)


@pytest.mark.asyncio
async def test_admin_run_monthly_cycle(client, setup_db, admin_user):
    """POST /admin/ambassadors/cycle/run returns full summary dict."""
    r = await client.post(
        "/api/v1/admin/ambassadors/cycle/run",
        headers=admin_user["headers"],
        json={"month": 5, "year": 2026},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["month"] == 5
    assert data["year"] == 2026
    assert "ambassadors_processed" in data
    assert "commissions_created" in data
    assert "leadership_pool_amount" in data


@pytest.mark.asyncio
async def test_admin_calculate_pool(client, setup_db, admin_user):
    """POST /admin/ambassadors/pool/calculate returns a pool record."""
    r = await client.post(
        "/api/v1/admin/ambassadors/pool/calculate",
        headers=admin_user["headers"],
        json={"month": 5, "year": 2026},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["month"] == 5
    assert "total_pool_amount" in data


@pytest.mark.asyncio
async def test_admin_list_pools(client, setup_db, admin_user):
    """GET /admin/ambassadors/pool returns paginated pool list."""
    r = await client.get(
        "/api/v1/admin/ambassadors/pool", headers=admin_user["headers"]
    )
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_admin_award_travel_gold_leader(client, setup_db, admin_user):
    """POST /admin/ambassadors/{id}/travel awards Rwanda & Tanzania for Gold Leader."""
    u, _ = await _make_user()
    amb = await _make_ambassador(u.id, rank=AmbassadorRank.GOLD_LEADER)

    r = await client.post(
        f"/api/v1/admin/ambassadors/{amb.id}/travel",
        headers=admin_user["headers"],
        json={"admin_notes": "Q1 performance"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "awarded"
    assert "Rwanda" in data["destination"]


@pytest.mark.asyncio
async def test_admin_award_travel_fails_for_associate(client, setup_db, admin_user):
    """POST /admin/ambassadors/{id}/travel returns 400 for an Associate."""
    u, _ = await _make_user()
    amb = await _make_ambassador(u.id, rank=AmbassadorRank.ASSOCIATE)

    r = await client.post(
        f"/api/v1/admin/ambassadors/{amb.id}/travel",
        headers=admin_user["headers"],
        json={},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_admin_award_travel_is_idempotent(client, setup_db, admin_user):
    """Awarding travel twice upserts instead of creating a duplicate record."""
    u, _ = await _make_user()
    amb = await _make_ambassador(u.id, rank=AmbassadorRank.PLATINUM_LEADER)

    await client.post(
        f"/api/v1/admin/ambassadors/{amb.id}/travel",
        headers=admin_user["headers"],
        json={"admin_notes": "First"},
    )
    r = await client.post(
        f"/api/v1/admin/ambassadors/{amb.id}/travel",
        headers=admin_user["headers"],
        json={"admin_notes": "Second"},
    )
    assert r.status_code == 200

    async with TestSessionFactory() as db:
        result = await db.execute(
            select(func.count(AmbassadorTravelIncentive.id)).where(
                AmbassadorTravelIncentive.ambassador_id == amb.id
            )
        )
        assert result.scalar() == 1


@pytest.mark.asyncio
async def test_admin_list_and_update_travel(client, setup_db, admin_user):
    """GET then PATCH /admin/ambassadors/{id}/travel/{incentive_id} updates status."""
    u, _ = await _make_user()
    amb = await _make_ambassador(u.id, rank=AmbassadorRank.GOLD_LEADER)

    award_r = await client.post(
        f"/api/v1/admin/ambassadors/{amb.id}/travel",
        headers=admin_user["headers"],
        json={},
    )
    incentive_id = award_r.json()["id"]

    list_r = await client.get(
        f"/api/v1/admin/ambassadors/{amb.id}/travel", headers=admin_user["headers"]
    )
    assert list_r.status_code == 200
    assert len(list_r.json()) == 1

    patch_r = await client.patch(
        f"/api/v1/admin/ambassadors/{amb.id}/travel/{incentive_id}",
        headers=admin_user["headers"],
        json={"status": "qualified", "admin_notes": "Verified"},
    )
    assert patch_r.status_code == 200
    assert patch_r.json()["status"] == "qualified"


@pytest.mark.asyncio
async def test_admin_training_complete_then_remove(client, setup_db, admin_user):
    """Admin marks training module complete and then removes it."""
    u, _ = await _make_user()
    amb = await _make_ambassador(u.id)
    key = "platform_overview"

    r = await client.post(
        f"/api/v1/admin/ambassadors/{amb.id}/training/{key}/complete",
        headers=admin_user["headers"],
    )
    assert r.status_code == 200

    r2 = await client.post(
        f"/api/v1/admin/ambassadors/{amb.id}/training/{key}/complete",
        headers=admin_user["headers"],
    )
    assert r2.status_code == 409  # conflict — already complete

    r3 = await client.delete(
        f"/api/v1/admin/ambassadors/{amb.id}/training/{key}",
        headers=admin_user["headers"],
    )
    assert r3.status_code == 200


# ── Auth / permission checks ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unauthenticated_rejected(client, setup_db):
    """All ambassador endpoints require authentication (401 without token)."""
    for path, method in [
        ("/api/v1/ambassador", "GET"),
        ("/api/v1/ambassador/referral", "POST"),
        ("/api/v1/admin/ambassadors", "GET"),
    ]:
        r = await getattr(client, method.lower())(path)
        assert r.status_code == 401, f"{method} {path} should return 401"


@pytest.mark.asyncio
async def test_regular_user_cannot_access_admin_endpoints(client, setup_db):
    """Regular (non-admin) user gets 403 on admin ambassador endpoints."""
    _, headers = await _make_user()
    r = await client.get("/api/v1/admin/ambassadors", headers=headers)
    assert r.status_code == 403
