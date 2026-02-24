import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.middleware.auth import get_current_user
from app.models.content import LearningContent
from app.models.schemas.content import LearningContentResponse
from app.models.user import User

router = APIRouter(prefix="/learning", tags=["Learning Hub"])


@router.get("/content", response_model=list[LearningContentResponse])
async def list_published_content(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LearningContent)
        .where(LearningContent.is_published == True)  # noqa: E712
        .order_by(LearningContent.display_order.asc())
    )
    content = result.scalars().all()
    return [LearningContentResponse.model_validate(c) for c in content]


@router.get("/content/{content_id}", response_model=LearningContentResponse)
async def get_content_detail(
    content_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LearningContent).where(
            LearningContent.id == content_id,
            LearningContent.is_published == True,  # noqa: E712
        )
    )
    content = result.scalar_one_or_none()
    if content is None:
        raise HTTPException(status_code=404, detail="Content not found")
    return LearningContentResponse.model_validate(content)
