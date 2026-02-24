"""Admin executions router — read-only cross-user access to all executions."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.models.enums import ExecutionStatus, SignalDirection
from app.models.execution import Execution
from app.models.schemas.common import PaginatedResponse
from app.models.schemas.execution import ExecutionResponse

router = APIRouter()


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
    query = select(Execution).order_by(Execution.created_at.desc())
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
        items=[ExecutionResponse.model_validate(e) for e in executions],
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
