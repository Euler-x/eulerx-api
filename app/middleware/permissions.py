"""Unified permission system for EulerX API.

Every route handler receives a single ``UserPermissions`` object that bundles
the authenticated user, their active subscription, and all derived boolean
flags.  Use the pre-built ``Depends`` constants as default parameter values:

    from app.middleware.permissions import UserPermissions, RequireSubscribed

    @router.get("/signals")
    async def list_signals(perms: UserPermissions = RequireSubscribed):
        user_id = perms.id
        ...

Available guards (in ascending order of restriction):

    RequireAuth        – valid JWT, active account
    RequireVerified    – RequireAuth + email verified
    RequireSubscribed  – RequireVerified + active subscription
    RequireAdmin       – RequireAuth + is_admin flag
    RequireATE         – RequireSubscribed + plan.ate_access
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

if TYPE_CHECKING:
    from app.models.schemas.billing import PlanFeatures

from app.db.base import get_db
from app.models.billing import Plan, Subscription
from app.models.enums import SubscriptionStatus
from app.models.user import User
from app.utils.helpers import utc_now
from app.utils.security import verify_token

_bearer = HTTPBearer(auto_error=False)


# ── Permission context object ─────────────────────────────────────────────────


@dataclass
class UserPermissions:
    """Rich permission context injected into route handlers.

    Wraps the authenticated ``User`` row together with their active
    ``Subscription`` / ``Plan`` so that routes can do simple attribute
    checks instead of querying the DB themselves.

    Attributes
    ----------
    user         The authenticated SQLAlchemy User object.
    subscription The user's current active Subscription, or None.
    plan         The plan attached to that subscription, or None.

    Permission flags
    ----------------
    is_admin       True when user.is_admin is set.
    is_active      True when the account is not disabled.
    is_verified    True when the user has verified their email.
    is_subscribed  True when the user has an active (or in-grace) subscription.

    Plan limits (0 / False when no active subscription)
    ----------------------------------------------------
    max_strategies   Maximum number of strategies allowed by the plan.
    max_allocation   Maximum capital allocation allowed by the plan.
    has_ate_access   True when the plan includes ATE (Automated Trading Engine).
    plan_name        Human-readable plan name, or None.
    """

    user: User
    subscription: Subscription | None = field(default=None, repr=False)
    plan: Plan | None = field(default=None, repr=False)
    _sub_active: bool = field(default=False, repr=False)

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def id(self) -> uuid.UUID:
        """Shortcut for ``perms.user.id``."""
        return self.user.id

    # ── Core permission flags ─────────────────────────────────────────────────

    @property
    def is_admin(self) -> bool:
        """True if the user holds the admin role."""
        return self.user.is_admin

    @property
    def is_active(self) -> bool:
        """True if the user account has not been disabled."""
        return self.user.is_active

    @property
    def is_verified(self) -> bool:
        """True if the user has verified their email address."""
        return self.user.email_verified

    @property
    def is_subscribed(self) -> bool:
        """True if the user has an active or grace-period subscription."""
        return self._sub_active

    # ── Plan limits ───────────────────────────────────────────────────────────

    @property
    def max_strategies(self) -> int:
        """Maximum strategies allowed under the current plan (0 if none)."""
        return self.plan.max_strategies if self.plan else 0

    @property
    def max_allocation(self) -> float:
        """Maximum capital allocation under the current plan (0.0 if none)."""
        return float(self.plan.max_allocation) if self.plan else 0.0

    @property
    def has_ate_access(self) -> bool:
        """True when the user's plan includes ATE access."""
        return self.plan.ate_access if self.plan else False

    @property
    def plan_name(self) -> str | None:
        """Human-readable name of the current plan, or None."""
        return self.plan.name if self.plan else None

    @property
    def plan_features(self) -> "PlanFeatures":
        """Typed view of the plan's ``features`` JSON column.

        Returns a :class:`~app.models.schemas.billing.PlanFeatures` instance
        with all keys defaulting to their most-restrictive values when the user
        has no active plan.
        """
        from app.models.schemas.billing import PlanFeatures

        return PlanFeatures.from_plan(self.plan)


# ── Internal base resolver ────────────────────────────────────────────────────


async def _load_user_permissions(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> UserPermissions:
    """Validate JWT, load User + active Subscription/Plan, return UserPermissions.

    This function is the single source of truth for authentication.  Every
    permission guard below depends on it so the DB is hit only once per request.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = verify_token(credentials.credentials, expected_type="access")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    try:
        user_uuid = uuid.UUID(user_id_str)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user ID in token",
        ) from exc

    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account has been disabled. Contact support.",
        )

    # ── Load active subscription + plan ──────────────────────────────────────
    sub_result = await db.execute(
        select(Subscription)
        .options(selectinload(Subscription.plan))
        .where(Subscription.user_id == user_uuid)
        .where(
            Subscription.status.in_(
                [SubscriptionStatus.ACTIVE, SubscriptionStatus.EXPIRING_SOON]
            )
        )
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    subscription = sub_result.scalar_one_or_none()
    plan = subscription.plan if subscription else None

    sub_active = False
    if subscription:
        now = utc_now()

        def _utc(dt: datetime | None) -> datetime | None:
            """Ensure *dt* is timezone-aware (handles SQLite returning naive datetimes)."""
            if dt is None:
                return None
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

        expires_at = _utc(subscription.expires_at)
        grace_until = _utc(subscription.grace_until)

        if expires_at is None or expires_at >= now:
            sub_active = True
        elif grace_until and grace_until >= now:
            sub_active = True
        else:
            # Subscription has expired past the grace period — update status
            subscription.status = SubscriptionStatus.EXPIRED
            user.is_subscribed = False
            sub_active = False

    if sub_active and not user.is_subscribed:
        # Keep the denormalized flag in sync
        user.is_subscribed = True

    return UserPermissions(
        user=user,
        subscription=subscription,
        plan=plan,
        _sub_active=sub_active,
    )


# ── Permission guard functions ────────────────────────────────────────────────


async def _require_auth(
    perms: UserPermissions = Depends(_load_user_permissions),
) -> UserPermissions:
    """Any valid, active JWT user."""
    return perms


async def _require_verified(
    perms: UserPermissions = Depends(_load_user_permissions),
) -> UserPermissions:
    """Authenticated user with a verified email address."""
    if not perms.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email verification required. Please verify your email to access this feature.",
        )
    return perms


async def _require_subscribed(
    perms: UserPermissions = Depends(_load_user_permissions),
) -> UserPermissions:
    """Verified user with an active (or grace-period) subscription."""
    if not perms.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email verification required. Please verify your email to access this feature.",
        )
    if not perms.is_subscribed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active subscription required. Please upgrade your plan.",
        )
    return perms


async def _require_admin(
    perms: UserPermissions = Depends(_load_user_permissions),
) -> UserPermissions:
    """User with the admin role (``is_admin=True``)."""
    if not perms.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return perms


async def _require_ate(
    perms: UserPermissions = Depends(_load_user_permissions),
) -> UserPermissions:
    """Verified + subscribed user whose plan includes ATE access."""
    if not perms.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email verification required. Please verify your email to access this feature.",
        )
    if not perms.is_subscribed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active subscription required. Please upgrade your plan.",
        )
    if not perms.has_ate_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your current plan does not include ATE access. Please upgrade.",
        )
    return perms


# ── Public Depends instances ──────────────────────────────────────────────────
# Use these directly as default parameter values in route handlers:
#
#   async def my_route(perms: UserPermissions = RequireSubscribed):
#       ...

RequireAuth = Depends(_require_auth)
RequireVerified = Depends(_require_verified)
RequireSubscribed = Depends(_require_subscribed)
RequireAdmin = Depends(_require_admin)
RequireATE = Depends(_require_ate)
