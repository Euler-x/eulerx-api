"""Admin trading router — emergency kill switch for halting and resuming strategies.

Uses AdminConfig (key="trading_halt") to persist halt state and remember which
strategies were active before the halt, so that resume only re-enables those —
not strategies that a user had manually deactivated.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.middleware.audit import log_audit
from app.middleware.auth import get_admin_user
from app.models.admin_config import AdminConfig
from app.models.strategy import Strategy
from app.models.user import User

router = APIRouter()


class HaltRequest(BaseModel):
    reason: Optional[str] = None


class ResumeRequest(BaseModel):
    reason: Optional[str] = None


# ── Helpers ──────────────────────────────────────────────────────


async def _get_halt_config(db: AsyncSession) -> AdminConfig | None:
    result = await db.execute(
        select(AdminConfig).where(AdminConfig.key == "trading_halt")
    )
    return result.scalar_one_or_none()


async def _upsert_halt_config(db: AsyncSession, value: dict) -> AdminConfig:
    config = await _get_halt_config(db)
    if config is None:
        config = AdminConfig(
            key="trading_halt",
            value=value,
            description="Emergency trading halt state",
        )
        db.add(config)
    else:
        config.value = value
    return config


# ── Endpoints ────────────────────────────────────────────────────


@router.get("/trading/status")
async def trading_status(
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_admin_user),
):
    """Check whether trading is currently halted."""
    config = await _get_halt_config(db)
    if config and config.value.get("halted"):
        return {
            "halted": True,
            "reason": config.value.get("reason"),
            "halted_by": config.value.get("halted_by"),
            "strategies_halted": len(config.value.get("halted_strategy_ids", [])),
        }
    return {"halted": False}


@router.post("/trading/halt")
async def halt_trading(
    body: HaltRequest = HaltRequest(),
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_admin_user),
):
    """Emergency kill switch — deactivates all active strategies immediately.

    Stores the IDs of halted strategies so that resume only re-enables those,
    leaving user-deactivated strategies untouched.
    """
    # Check if already halted
    existing = await _get_halt_config(db)
    if existing and existing.value.get("halted"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Trading is already halted.",
        )

    # Collect IDs of currently active strategies
    result = await db.execute(
        select(Strategy.id).where(Strategy.is_active == True)  # noqa: E712
    )
    active_ids = [str(sid) for sid in result.scalars().all()]

    # Deactivate all active strategies
    if active_ids:
        await db.execute(
            update(Strategy)
            .where(Strategy.is_active == True)  # noqa: E712
            .values(is_active=False)
        )

    # Persist halt state with the list of affected strategy IDs
    await _upsert_halt_config(
        db,
        {
            "halted": True,
            "halted_strategy_ids": active_ids,
            "reason": body.reason,
            "halted_by": str(current_admin.id),
        },
    )

    # Audit log — committed atomically by get_db dependency
    await log_audit(
        db=db,
        user_id=current_admin.id,
        action="TRADING_HALT",
        details={
            "halted_count": len(active_ids),
            "reason": body.reason,
        },
    )

    return {
        "status": "halted",
        "strategies_halted": len(active_ids),
        "reason": body.reason,
    }


@router.post("/trading/resume")
async def resume_trading(
    body: ResumeRequest = ResumeRequest(),
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_admin_user),
):
    """Resume trading — re-enables only the strategies that were active before
    the halt, leaving user-deactivated strategies untouched.
    """
    config = await _get_halt_config(db)
    if not config or not config.value.get("halted"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Trading is not currently halted.",
        )

    # Only re-enable strategies that were halted by the kill switch
    halted_ids = config.value.get("halted_strategy_ids", [])
    resumed_count = 0

    if halted_ids:
        uuid_ids = [uuid.UUID(sid) for sid in halted_ids]
        await db.execute(
            update(Strategy).where(Strategy.id.in_(uuid_ids)).values(is_active=True)
        )
        resumed_count = len(halted_ids)

    # Clear halt state
    await _upsert_halt_config(db, {"halted": False})

    # Audit log — committed atomically by get_db dependency
    await log_audit(
        db=db,
        user_id=current_admin.id,
        action="TRADING_RESUME",
        details={
            "resumed_count": resumed_count,
            "reason": body.reason,
        },
    )

    return {
        "status": "resumed",
        "strategies_resumed": resumed_count,
        "reason": body.reason,
    }
