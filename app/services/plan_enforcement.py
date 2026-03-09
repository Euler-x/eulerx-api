"""Central plan enforcement — single source of truth for all plan-limit checks.

Every business rule tied to what a subscription plan permits lives here.
Routers call these helpers instead of duplicating logic inline.  All methods
raise :class:`PlanLimitError` (a 403 HTTPException) on violation, so callers
just ``await PlanEnforcer.strategy_count(db, perms)`` and move on.

Usage::

    from app.services.plan_enforcement import PlanEnforcer

    # In a route handler:
    await PlanEnforcer.strategy_count(db, perms)
    await PlanEnforcer.allocation(db, perms, data.allocation_pct)
    PlanEnforcer.ate_access(perms)
    PlanEnforcer.feature(perms, "api_access")
"""

from __future__ import annotations

import uuid as _uuid
from typing import TYPE_CHECKING

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.strategy import Strategy

if TYPE_CHECKING:
    from app.middleware.permissions import UserPermissions


class PlanLimitError(HTTPException):
    """Raised (HTTP 403) when a user's action would exceed their plan's limits."""

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


class PlanEnforcer:
    """All plan-limit checks in one place.

    Every method is a ``@staticmethod`` — callers never need to instantiate::

        await PlanEnforcer.strategy_count(db, perms)
        await PlanEnforcer.allocation(db, perms, 50.0)
        PlanEnforcer.ate_access(perms)
        PlanEnforcer.feature(perms, "priority_support")
        await PlanEnforcer.trial_eligibility(db, perms.id, plan.id)
    """

    # ── Strategy count ──────────────────────────────────────────────────────

    @staticmethod
    async def strategy_count(db: AsyncSession, perms: UserPermissions) -> None:
        """Raise 403 if the user has reached their plan's ``max_strategies`` cap.

        No-ops when the user is not subscribed (the route guard handles that
        separately) or when the plan sets ``max_strategies = 0`` (unlimited).
        """
        if not perms.is_subscribed or perms.max_strategies <= 0:
            return

        count: int = (
            await db.execute(
                select(func.count(Strategy.id)).where(Strategy.user_id == perms.id)
            )
        ).scalar() or 0

        if count >= perms.max_strategies:
            limit = perms.max_strategies
            noun = "strategy" if limit == 1 else "strategies"
            raise PlanLimitError(
                f"Your {perms.plan_name!r} plan allows {limit} {noun}. "
                "Upgrade your plan to create more."
            )

    # ── Allocation percentage ────────────────────────────────────────────────

    @staticmethod
    async def allocation(
        db: AsyncSession,
        perms: UserPermissions,
        new_allocation_pct: float,
        *,
        exclude_strategy_id: str | _uuid.UUID | None = None,
    ) -> None:
        """Raise 403 if *new_allocation_pct* would breach the cumulative 100% limit.

        Enforces that the sum of all the user's strategy allocation percentages
        (including the new one) does not exceed 100%.

        Pass *exclude_strategy_id* when updating an existing strategy so its
        current value is not double-counted in the cumulative sum.

        No-ops when the user is not subscribed.
        """
        if not perms.is_subscribed:
            return

        # Cumulative cap: all strategy allocation_pct values must sum to ≤ 100%
        query = select(func.coalesce(func.sum(Strategy.allocation_pct), 0.0)).where(
            Strategy.user_id == perms.id
        )

        if exclude_strategy_id is not None:
            excl = (
                _uuid.UUID(str(exclude_strategy_id))
                if not isinstance(exclude_strategy_id, _uuid.UUID)
                else exclude_strategy_id
            )
            query = query.where(Strategy.id != excl)

        current_total: float = float((await db.execute(query)).scalar() or 0.0)
        proposed_total = current_total + new_allocation_pct

        if proposed_total > 100.0:
            raise PlanLimitError(
                f"Adding {new_allocation_pct:.1f}% would bring your total allocation "
                f"to {proposed_total:.1f}%, exceeding the 100% limit. "
                f"(Current total: {current_total:.1f}%)"
            )

    # ── ATE access ──────────────────────────────────────────────────────────

    @staticmethod
    def ate_access(perms: UserPermissions) -> None:
        """Raise 403 if the plan does not include ATE (Automated Trading Engine) access."""
        if not perms.has_ate_access:
            raise PlanLimitError(
                "Your current plan does not include Automated Trading Engine access. "
                "Upgrade to a plan that includes ATE."
            )

    # ── Feature flags ────────────────────────────────────────────────────────

    @staticmethod
    def feature(perms: UserPermissions, feature_key: str) -> None:
        """Raise 403 if the plan's ``features`` dict does not enable *feature_key*.

        Accepts bool, int, and str values:

        - ``bool``  → must be ``True``
        - ``int``   → must be ``> 0``
        - ``str``   → must be non-empty
        - ``None``  → treated as disabled
        """
        if not perms.plan:
            raise PlanLimitError(
                "An active subscription is required to access this feature."
            )

        value = perms.plan.features.get(feature_key)
        enabled = (
            value is True
            or (isinstance(value, int) and value > 0)
            or (isinstance(value, str) and value != "")
        )

        if not enabled:
            raise PlanLimitError(
                f"Your {perms.plan_name!r} plan does not include the "
                f"'{feature_key}' feature. Upgrade to unlock it."
            )

    # ── Trial eligibility ────────────────────────────────────────────────────

    @staticmethod
    async def trial_eligibility(
        db: AsyncSession,
        user_id: _uuid.UUID,
    ) -> None:
        """Raise 403 if the user is not eligible for a free trial.

        A user is ineligible when they have any prior subscription that ever
        left the ``INACTIVE`` / ``PENDING_PAYMENT`` states — i.e. they were
        previously active, in trial, or expired on any plan.
        """
        from app.models.billing import Subscription
        from app.models.enums import SubscriptionStatus

        prior = (
            await db.execute(
                select(Subscription).where(
                    Subscription.user_id == user_id,
                    Subscription.status.notin_(
                        [
                            SubscriptionStatus.INACTIVE,
                            SubscriptionStatus.PENDING_PAYMENT,
                        ]
                    ),
                )
            )
        ).scalar_one_or_none()

        if prior is not None:
            raise PlanLimitError(
                "You have already used a trial or held a paid subscription. "
                "Free trials are available to new subscribers only."
            )
