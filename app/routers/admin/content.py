import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.models.content import LearningContent
from app.models.schemas.common import MessageResponse
from app.models.schemas.content import (
    LearningContentCreate,
    LearningContentResponse,
    LearningContentUpdate,
)

router = APIRouter()


@router.get("/content", response_model=list[LearningContentResponse])
async def admin_list_content(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(LearningContent).order_by(LearningContent.display_order.asc())
    )
    content = result.scalars().all()
    return [LearningContentResponse.model_validate(c) for c in content]


@router.post("/content", response_model=LearningContentResponse, status_code=201)
async def admin_create_content(
    data: LearningContentCreate, db: AsyncSession = Depends(get_db)
):
    content = LearningContent(**data.model_dump())
    db.add(content)
    await db.flush()
    return LearningContentResponse.model_validate(content)


@router.put("/content/{content_id}", response_model=LearningContentResponse)
async def admin_update_content(
    content_id: uuid.UUID,
    data: LearningContentUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LearningContent).where(LearningContent.id == content_id)
    )
    content = result.scalar_one_or_none()
    if content is None:
        raise HTTPException(status_code=404, detail="Content not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(content, key, value)

    await db.flush()
    return LearningContentResponse.model_validate(content)


@router.delete("/content/{content_id}", response_model=MessageResponse)
async def admin_delete_content(
    content_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(LearningContent).where(LearningContent.id == content_id)
    )
    content = result.scalar_one_or_none()
    if content is None:
        raise HTTPException(status_code=404, detail="Content not found")

    await db.delete(content)
    return MessageResponse(message="Content deleted successfully")
