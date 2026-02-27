"""E2E tests for the central plan enforcement system (PlanEnforcer).

Covers every axis of plan enforcement:
  - Strategy count limits (per plan)
  - Capital allocation limits: per-strategy cap and cumulative cap
  - Allocation enforcement on strategy update
  - Unsubscribed users bypass enforcement (no-op guards)
  - Activation requires an active subscription
  - Trial subscriptions activate immediately
  - Trial re-use is blocked for prior subscribers
"""

import uuid

import pytest

from tests.conftest import TestSessionFactory
from app.models.billing import Plan, Subscription
from app.models.enums import BillingCycle, PlanStatus, SubscriptionStatus
from app.models.user import User
from app.utils.helpers import add_days, utc_now
from app.utils.security import create_access_token


# ── Private helpers ─────────────────────────────────────────────────────────────


async def _plan(
    name: str,
    max_strategies: int = 10,
    max_allocation: float = 0.0,  # 0 = unlimited unless specified
    ate_access: bool = False,
    trial_days: int = 0,
    features: dict | None = None,
) -> Plan:
    """Insert an ACTIVE plan and return the committed ORM object."""
    async with TestSessionFactory() as session:
        p = Plan(
            name=name,
            price_usd=9.99,
            billing_cycle=BillingCycle.MONTHLY,
            max_strategies=max_strategies,
            max_allocation=max_allocation,
            ate_access=ate_access,
            trial_days=trial_days,
            features=features or {},
            status=PlanStatus.ACTIVE,
        )
        session.add(p)
        await session.commit()
        await session.refresh(p)
        return p


async def _subscribed(plan: Plan) -> dict:
    """Create a verified, subscribed user on *plan*. Returns auth headers dict."""
    uid = uuid.uuid4()
    async with TestSessionFactory() as session:
        user = User(
            id=uid,
            email=f"sub-{uid}@test.local",
            email_verified=True,
            is_active=True,
            is_subscribed=True,
        )
        session.add(user)
        await session.flush()

        now = utc_now()
        sub = Subscription(
            user_id=uid,
            plan_id=plan.id,
            status=SubscriptionStatus.ACTIVE,
            started_at=now,
            expires_at=add_days(now, 30),
        )
        session.add(sub)
        await session.commit()

    return {"Authorization": f"Bearer {create_access_token(str(uid))}"}


async def _unsubscribed() -> dict:
    """Create a verified user with NO subscription. Returns auth headers dict."""
    uid = uuid.uuid4()
    async with TestSessionFactory() as session:
        user = User(
            id=uid,
            email=f"unsub-{uid}@test.local",
            email_verified=True,
            is_active=True,
            is_subscribed=False,
        )
        session.add(user)
        await session.commit()

    return {"Authorization": f"Bearer {create_access_token(str(uid))}"}


# Minimal valid strategy payload; override individual fields per test.
_BASE = {
    "name": "Test Strategy",
    "strategy_type": "conservative",
    "risk_profile": "low",
    "capital_allocation": 100.0,
}


# ── 1. Strategy count enforcement ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_strategy_count_blocks_at_limit(client, setup_db):
    """Creating more strategies than max_strategies returns 403."""
    plan = await _plan("cnt-limit", max_strategies=2)
    hdrs = await _subscribed(plan)

    for i in range(2):
        r = await client.post(
            "/api/v1/strategies",
            headers=hdrs,
            json={**_BASE, "name": f"S-{i}"},
        )
        assert r.status_code == 201, f"strategy {i} failed: {r.text}"

    r = await client.post(
        "/api/v1/strategies",
        headers=hdrs,
        json={**_BASE, "name": "S-over-limit"},
    )
    assert r.status_code == 403
    assert (
        "plan allows" in r.json()["detail"].lower()
        or "upgrade" in r.json()["detail"].lower()
    )


@pytest.mark.asyncio
async def test_strategy_count_zero_means_unlimited(client, setup_db):
    """max_strategies=0 imposes no count cap."""
    plan = await _plan("cnt-unlimited", max_strategies=0)
    hdrs = await _subscribed(plan)

    for i in range(5):
        r = await client.post(
            "/api/v1/strategies",
            headers=hdrs,
            json={**_BASE, "name": f"Unlimited-{i}", "capital_allocation": 10.0},
        )
        assert r.status_code == 201, f"strategy {i} failed: {r.text}"


# ── 2. Per-strategy allocation cap ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_strategy_exceeds_allocation_cap(client, setup_db):
    """A single strategy's allocation exceeding max_allocation is rejected."""
    plan = await _plan("alloc-single", max_allocation=1_000.0)
    hdrs = await _subscribed(plan)

    r = await client.post(
        "/api/v1/strategies",
        headers=hdrs,
        json={**_BASE, "capital_allocation": 1_500.0},
    )
    assert r.status_code == 403
    detail = r.json()["detail"].lower()
    assert "1,500" in detail or "exceed" in detail or "limit" in detail


@pytest.mark.asyncio
async def test_allocation_at_cap_is_allowed(client, setup_db):
    """A strategy allocation exactly at the cap is accepted."""
    plan = await _plan("alloc-exact", max_allocation=1_000.0)
    hdrs = await _subscribed(plan)

    r = await client.post(
        "/api/v1/strategies",
        headers=hdrs,
        json={**_BASE, "capital_allocation": 1_000.0},
    )
    assert r.status_code == 201


# ── 3. Cumulative allocation cap ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cumulative_allocation_blocks_second_strategy(client, setup_db):
    """Two strategies whose combined allocation exceeds the cap: second is blocked."""
    plan = await _plan("alloc-cumul", max_allocation=1_000.0)
    hdrs = await _subscribed(plan)

    # First: 600 — within cap
    r1 = await client.post(
        "/api/v1/strategies",
        headers=hdrs,
        json={**_BASE, "name": "First", "capital_allocation": 600.0},
    )
    assert r1.status_code == 201

    # Second: 600 — cumulative 1200 > 1000 → blocked
    r2 = await client.post(
        "/api/v1/strategies",
        headers=hdrs,
        json={**_BASE, "name": "Second", "capital_allocation": 600.0},
    )
    assert r2.status_code == 403
    detail = r2.json()["detail"].lower()
    assert (
        "total" in detail
        or "1,200" in detail
        or "exceed" in detail
        or "current total" in detail
    )


@pytest.mark.asyncio
async def test_cumulative_allocation_allows_exact_fill(client, setup_db):
    """Two strategies that together reach exactly the cap are both accepted."""
    plan = await _plan("alloc-exact-fill", max_allocation=1_000.0)
    hdrs = await _subscribed(plan)

    r1 = await client.post(
        "/api/v1/strategies",
        headers=hdrs,
        json={**_BASE, "name": "Half-1", "capital_allocation": 500.0},
    )
    assert r1.status_code == 201

    r2 = await client.post(
        "/api/v1/strategies",
        headers=hdrs,
        json={**_BASE, "name": "Half-2", "capital_allocation": 500.0},
    )
    assert r2.status_code == 201


# ── 4. Allocation enforcement on update ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_allocation_blocked_above_cap(client, setup_db):
    """Updating a strategy's allocation beyond the plan cap returns 403."""
    plan = await _plan("alloc-upd-block", max_allocation=1_000.0)
    hdrs = await _subscribed(plan)

    r = await client.post(
        "/api/v1/strategies",
        headers=hdrs,
        json={**_BASE, "name": "Updatable", "capital_allocation": 500.0},
    )
    assert r.status_code == 201
    sid = r.json()["id"]

    r2 = await client.put(
        f"/api/v1/strategies/{sid}",
        headers=hdrs,
        json={"capital_allocation": 1_500.0},
    )
    assert r2.status_code == 403


@pytest.mark.asyncio
async def test_update_allocation_within_cap_succeeds(client, setup_db):
    """Updating a strategy's allocation within the cap succeeds."""
    plan = await _plan("alloc-upd-ok", max_allocation=1_000.0)
    hdrs = await _subscribed(plan)

    r = await client.post(
        "/api/v1/strategies",
        headers=hdrs,
        json={**_BASE, "name": "Updatable OK", "capital_allocation": 400.0},
    )
    assert r.status_code == 201
    sid = r.json()["id"]

    r2 = await client.put(
        f"/api/v1/strategies/{sid}",
        headers=hdrs,
        json={"capital_allocation": 900.0},
    )
    assert r2.status_code == 200
    # pytest.approx handles float comparison from Numeric column
    assert float(r2.json()["capital_allocation"]) == pytest.approx(900.0, rel=1e-3)


@pytest.mark.asyncio
async def test_update_does_not_double_count_own_allocation(client, setup_db):
    """Updating the SAME strategy's allocation is not cumulative-counted twice."""
    plan = await _plan("alloc-no-double", max_allocation=1_000.0)
    hdrs = await _subscribed(plan)

    r = await client.post(
        "/api/v1/strategies",
        headers=hdrs,
        json={**_BASE, "name": "No Double", "capital_allocation": 800.0},
    )
    assert r.status_code == 201
    sid = r.json()["id"]

    # Updating from 800 to 900 — should be fine (not 800+900=1700 > 1000)
    r2 = await client.put(
        f"/api/v1/strategies/{sid}",
        headers=hdrs,
        json={"capital_allocation": 900.0},
    )
    assert r2.status_code == 200


# ── 5. Unsubscribed users — no enforcement ───────────────────────────────────────


@pytest.mark.asyncio
async def test_unsubscribed_user_bypasses_count_and_allocation_guards(client, setup_db):
    """PlanEnforcer no-ops when the user has no active subscription."""
    hdrs = await _unsubscribed()

    # Even with a very large allocation — enforcement is skipped for non-subscribers
    r = await client.post(
        "/api/v1/strategies",
        headers=hdrs,
        json={**_BASE, "capital_allocation": 9_999_999.0},
    )
    assert r.status_code == 201


# ── 6. Activation requires an active subscription ────────────────────────────────


@pytest.mark.asyncio
async def test_activate_strategy_blocked_without_subscription(client, setup_db):
    """POST /strategies/{id}/activate requires RequireSubscribed — 403 if unsubscribed."""
    hdrs = await _unsubscribed()

    create_r = await client.post(
        "/api/v1/strategies",
        headers=hdrs,
        json={**_BASE, "name": "Activate Me"},
    )
    assert create_r.status_code == 201
    sid = create_r.json()["id"]

    act_r = await client.post(f"/api/v1/strategies/{sid}/activate", headers=hdrs)
    assert act_r.status_code == 403


@pytest.mark.asyncio
async def test_activate_strategy_succeeds_with_subscription(client, setup_db):
    """POST /strategies/{id}/activate succeeds for a subscribed user."""
    plan = await _plan("activate-plan")
    hdrs = await _subscribed(plan)

    create_r = await client.post(
        "/api/v1/strategies",
        headers=hdrs,
        json={**_BASE, "name": "Activatable"},
    )
    assert create_r.status_code == 201
    sid = create_r.json()["id"]

    act_r = await client.post(f"/api/v1/strategies/{sid}/activate", headers=hdrs)
    assert act_r.status_code == 200
    assert act_r.json()["is_active"] is True


# ── 7. Trial subscription ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trial_plan_activates_subscription_immediately(client, setup_db):
    """POST /billing/subscribe on a trial plan creates an ACTIVE subscription instantly."""
    trial_plan = await _plan(f"trial-{uuid.uuid4().hex[:8]}", trial_days=14)

    uid = uuid.uuid4()
    async with TestSessionFactory() as session:
        user = User(
            id=uid,
            email=f"trial-{uid}@test.local",
            email_verified=True,
            is_active=True,
            is_subscribed=False,
        )
        session.add(user)
        await session.commit()

    hdrs = {"Authorization": f"Bearer {create_access_token(str(uid))}"}

    r = await client.post(
        "/api/v1/billing/subscribe",
        headers=hdrs,
        json={"plan_id": str(trial_plan.id)},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "active"
    assert data["expires_at"] is not None


@pytest.mark.asyncio
async def test_trial_blocked_for_prior_subscriber(client, setup_db):
    """A user who previously held any non-inactive subscription cannot start a trial."""
    trial_plan = await _plan(f"trial-reuse-{uuid.uuid4().hex[:8]}", trial_days=14)

    uid = uuid.uuid4()
    async with TestSessionFactory() as session:
        user = User(
            id=uid,
            email=f"trial-reuse-{uid}@test.local",
            email_verified=True,
            is_active=True,
            is_subscribed=False,
        )
        session.add(user)
        await session.flush()

        # Simulate a prior EXPIRED subscription (trial already used)
        now = utc_now()
        past_sub = Subscription(
            user_id=uid,
            plan_id=trial_plan.id,
            status=SubscriptionStatus.EXPIRED,
            started_at=add_days(now, -60),
            expires_at=add_days(now, -30),
        )
        session.add(past_sub)
        await session.commit()

    hdrs = {"Authorization": f"Bearer {create_access_token(str(uid))}"}

    r = await client.post(
        "/api/v1/billing/subscribe",
        headers=hdrs,
        json={"plan_id": str(trial_plan.id)},
    )
    assert r.status_code == 403
    detail = r.json()["detail"].lower()
    assert "trial" in detail or "already" in detail or "new subscribers" in detail


@pytest.mark.asyncio
async def test_trial_blocked_when_already_active(client, setup_db):
    """A user with an existing ACTIVE subscription cannot subscribe again."""
    plan = await _plan(f"trial-active-{uuid.uuid4().hex[:8]}", trial_days=14)
    hdrs = await _subscribed(plan)

    r = await client.post(
        "/api/v1/billing/subscribe",
        headers=hdrs,
        json={"plan_id": str(plan.id)},
    )
    assert r.status_code == 400
    assert "active subscription" in r.json()["detail"].lower()
