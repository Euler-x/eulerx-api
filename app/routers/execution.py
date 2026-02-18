import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.middleware.auth import require_verified_email
from app.models.enums import ExecutionStatus
from app.models.execution import Execution
from app.models.schemas.common import PaginatedResponse
from app.models.schemas.execution import ExecutionResponse, ExecutionVerifyResponse
from app.models.user import User
from app.services.verification import VerificationService

router = APIRouter(prefix="/executions", tags=["Executions"])


@router.get("", response_model=PaginatedResponse)
async def list_executions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: ExecutionStatus | None = None,
    current_user: User = Depends(require_verified_email),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(Execution)
        .where(Execution.user_id == current_user.id)
        .order_by(Execution.created_at.desc())
    )
    count_query = select(func.count(Execution.id)).where(
        Execution.user_id == current_user.id
    )

    if status:
        query = query.where(Execution.status == status)
        count_query = count_query.where(Execution.status == status)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    executions = result.scalars().all()

    return PaginatedResponse(
        items=[ExecutionResponse.model_validate(e) for e in executions],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


@router.get("/{execution_id}", response_model=ExecutionResponse)
async def get_execution(
    execution_id: uuid.UUID,
    current_user: User = Depends(require_verified_email),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Execution).where(
            Execution.id == execution_id,
            Execution.user_id == current_user.id,
        )
    )
    execution = result.scalar_one_or_none()
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    return ExecutionResponse.model_validate(execution)


@router.get("/{execution_id}/verify", response_model=ExecutionVerifyResponse)
async def verify_execution(
    execution_id: uuid.UUID,
    current_user: User = Depends(require_verified_email),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Execution).where(
            Execution.id == execution_id,
            Execution.user_id == current_user.id,
        )
    )
    execution = result.scalar_one_or_none()
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")

    if not execution.tx_hash:
        return ExecutionVerifyResponse(
            execution_id=execution.id,
            tx_hash=None,
            verified=False,
            verification_link=None,
        )

    verification = await VerificationService.verify_on_chain(execution.tx_hash)

    return ExecutionVerifyResponse(
        execution_id=execution.id,
        tx_hash=execution.tx_hash,
        verified=verification.get("verified", False),
        verification_link=verification.get("verification_link"),
    )
