"""Admin system router -- health checks and pipeline status."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.models.enums import ExecutionStatus
from app.models.execution import Execution
from app.models.signal import Signal
from app.models.strategy import Strategy
from app.utils.helpers import utc_now

router = APIRouter()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    database: str
    timestamp: datetime
    version: str
    uptime_info: dict


class PipelineStatusResponse(BaseModel):
    last_signal_at: Optional[datetime] = None
    last_execution_at: Optional[datetime] = None
    total_signals_today: int
    total_executions_today: int
    active_strategies: int
    pending_executions: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/system/health", response_model=HealthResponse)
async def system_health(db: AsyncSession = Depends(get_db)):
    """Return basic system health information."""
    now = utc_now()

    # Database connectivity check
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"

    return HealthResponse(
        database=db_status,
        timestamp=now,
        version="1.0.0",
        uptime_info={"server_started": now.isoformat()},
    )


@router.get("/system/pipeline", response_model=PipelineStatusResponse)
async def pipeline_status(db: AsyncSession = Depends(get_db)):
    """Return current pipeline run status and daily counters."""
    now = utc_now()

    # Most recent signal
    last_signal_at = (
        await db.execute(
            select(Signal.created_at).order_by(Signal.created_at.desc()).limit(1)
        )
    ).scalar()

    # Most recent execution
    last_execution_at = (
        await db.execute(
            select(Execution.created_at).order_by(Execution.created_at.desc()).limit(1)
        )
    ).scalar()

    # Signals created today
    total_signals_today = (
        await db.execute(
            select(func.count(Signal.id)).where(
                func.date(Signal.created_at) == func.date(now)
            )
        )
    ).scalar() or 0

    # Executions created today
    total_executions_today = (
        await db.execute(
            select(func.count(Execution.id)).where(
                func.date(Execution.created_at) == func.date(now)
            )
        )
    ).scalar() or 0

    # Active strategies
    active_strategies = (
        await db.execute(
            select(func.count(Strategy.id)).where(
                Strategy.is_active == True  # noqa: E712
            )
        )
    ).scalar() or 0

    # Pending executions
    pending_executions = (
        await db.execute(
            select(func.count(Execution.id)).where(
                Execution.status == ExecutionStatus.PENDING
            )
        )
    ).scalar() or 0

    return PipelineStatusResponse(
        last_signal_at=last_signal_at,
        last_execution_at=last_execution_at,
        total_signals_today=total_signals_today,
        total_executions_today=total_executions_today,
        active_strategies=active_strategies,
        pending_executions=pending_executions,
    )
