import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.models.billing import Plan
from app.models.enums import PlanStatus
from app.models.schemas.billing import PlanCreate, PlanResponse, PlanUpdate
from app.models.schemas.common import MessageResponse

router = APIRouter()


@router.get("/plans", response_model=list[PlanResponse])
async def admin_list_plans(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Plan).order_by(Plan.created_at.desc()))
    plans = result.scalars().all()
    return [PlanResponse.model_validate(p) for p in plans]


@router.post("/plans", response_model=PlanResponse, status_code=201)
async def admin_create_plan(
    data: PlanCreate, db: AsyncSession = Depends(get_db)
):
    existing = await db.execute(select(Plan).where(Plan.name == data.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Plan name already exists")

    plan = Plan(**data.model_dump())
    db.add(plan)
    await db.flush()
    return PlanResponse.model_validate(plan)


@router.put("/plans/{plan_id}", response_model=PlanResponse)
async def admin_update_plan(
    plan_id: uuid.UUID,
    data: PlanUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Plan).where(Plan.id == plan_id))
    plan = result.scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(plan, key, value)

    await db.flush()
    return PlanResponse.model_validate(plan)


@router.delete("/plans/{plan_id}", response_model=MessageResponse)
async def admin_delete_plan(
    plan_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Plan).where(Plan.id == plan_id))
    plan = result.scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")

    plan.status = PlanStatus.ARCHIVED
    return MessageResponse(message="Plan archived successfully")
