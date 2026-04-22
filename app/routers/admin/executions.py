"""Admin executions router — cross-user access to all executions + manual trigger."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.base import get_db
from app.middleware.audit import log_audit
from app.middleware.permissions import RequireAdmin, UserPermissions
from app.models.enums import ExecutionStatus, SignalDirection
from app.models.execution import Execution
from app.models.schemas.common import PaginatedResponse
from app.models.schemas.execution import CloseExecutionResponse, ExecutionResponse

router = APIRouter()


def _exec_response(ex: Execution) -> ExecutionResponse:
    """Build ExecutionResponse with user_email from loaded relationship."""
    resp = ExecutionResponse.model_validate(ex)
    if ex.user is not None:
        resp.user_email = ex.user.email
    return resp


@router.get("/executions", response_model=PaginatedResponse)
async def admin_list_executions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_id: Optional[uuid.UUID] = None,
    strategy_id: Optional[uuid.UUID] = None,
    status: Optional[ExecutionStatus] = None,
    direction: Optional[SignalDirection] = None,
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(Execution)
        .options(selectinload(Execution.user))
        .order_by(Execution.created_at.desc())
    )
    count_query = select(func.count(Execution.id))

    if user_id:
        query = query.where(Execution.user_id == user_id)
        count_query = count_query.where(Execution.user_id == user_id)
    if strategy_id:
        query = query.where(Execution.strategy_id == strategy_id)
        count_query = count_query.where(Execution.strategy_id == strategy_id)
    if status:
        query = query.where(Execution.status == status)
        count_query = count_query.where(Execution.status == status)
    if direction:
        query = query.where(Execution.direction == direction)
        count_query = count_query.where(Execution.direction == direction)

    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    executions = result.scalars().all()

    return PaginatedResponse(
        items=[_exec_response(e) for e in executions],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


@router.get("/executions/{execution_id}", response_model=ExecutionResponse)
async def admin_get_execution(
    execution_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Execution).where(Execution.id == execution_id))
    execution = result.scalar_one_or_none()
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    return ExecutionResponse.model_validate(execution)


@router.post("/executions/{execution_id}/close", response_model=CloseExecutionResponse)
async def admin_close_execution(
    execution_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    """Admin: manually close any open position regardless of ownership."""
    from app.routers.execution import close_execution as _user_close
    from app.middleware.permissions import UserPermissions as _UP

    # Verify the execution exists and load its owner
    result = await db.execute(
        select(Execution)
        .options(selectinload(Execution.user))
        .where(Execution.id == execution_id)
    )
    execution = result.scalar_one_or_none()
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    if execution.status != ExecutionStatus.FILLED:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot close execution with status '{execution.status.value}'. Only FILLED positions can be closed.",
        )

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="ADMIN_MANUAL_CLOSE",
        details={
            "execution_id": str(execution_id),
            "target_user_id": str(execution.user_id),
        },
    )

    # Delegate to user close endpoint, impersonating the execution's owner
    owner_perms = _UP(user=execution.user)
    return await _user_close(execution_id=execution_id, perms=owner_perms, db=db)


# ── Manual execution trigger ────────────────────────────────────────


class ManualExecuteRequest(BaseModel):
    signal_id: uuid.UUID
    user_email: str
    strategy_id: Optional[uuid.UUID] = None


@router.post("/executions/trigger", response_model=ExecutionResponse)
async def admin_trigger_execution(
    body: ManualExecuteRequest,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    """Manually execute a signal for a specific user (admin testing tool).

    If strategy_id is omitted, uses the user's first active strategy.
    """
    from app.models.signal import Signal
    from app.models.strategy import Strategy
    from app.models.user import User
    from app.services.ate import ATEService

    # Load signal
    sig_result = await db.execute(select(Signal).where(Signal.id == body.signal_id))
    signal = sig_result.scalar_one_or_none()
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")

    # Load user
    user_result = await db.execute(select(User).where(User.email == body.user_email))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Load strategy
    if body.strategy_id:
        strat_result = await db.execute(
            select(Strategy).where(
                Strategy.id == body.strategy_id,
                Strategy.user_id == user.id,
            )
        )
    else:
        strat_result = await db.execute(
            select(Strategy)
            .where(Strategy.user_id == user.id, Strategy.is_active == True)  # noqa: E712
            .order_by(Strategy.created_at.desc())
            .limit(1)
        )
    strategy = strat_result.scalar_one_or_none()
    if strategy is None:
        raise HTTPException(status_code=404, detail="No active strategy found for user")

    # Execute
    ate = ATEService()
    execution = await ate.execute_signal(
        db=db,
        signal=signal,
        strategy=strategy,
        user=user,
    )

    if execution is None:
        raise HTTPException(
            status_code=409,
            detail="Execution skipped (rate limit or idempotency check)",
        )

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="MANUAL_EXECUTION_TRIGGER",
        details={
            "signal_id": str(signal.id),
            "target_user": body.user_email,
            "strategy_id": str(strategy.id),
            "result_status": execution.status.value,
            "error_message": execution.error_message,
        },
    )

    await db.commit()
    return ExecutionResponse.model_validate(execution)
