"""Admin ambassadors router — manage ambassador records."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.middleware.audit import log_audit
from app.middleware.auth import get_admin_user
from app.models.ambassador import Ambassador
from app.models.enums import AmbassadorRank
from app.models.schemas.admin import AdminAmbassadorUpdate
from app.models.schemas.ambassador import AmbassadorResponse
from app.models.schemas.common import PaginatedResponse
from app.models.user import User

router = APIRouter()


@router.get("/ambassadors", response_model=PaginatedResponse)
async def admin_list_ambassadors(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    rank: Optional[AmbassadorRank] = None,
    min_referrals: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(Ambassador).order_by(Ambassador.created_at.desc())
    count_query = select(func.count(Ambassador.id))

    if rank:
        query = query.where(Ambassador.rank == rank)
        count_query = count_query.where(Ambassador.rank == rank)
    if min_referrals is not None:
        query = query.where(Ambassador.total_referrals >= min_referrals)
        count_query = count_query.where(Ambassador.total_referrals >= min_referrals)

    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    ambassadors = result.scalars().all()

    return PaginatedResponse(
        items=[AmbassadorResponse.model_validate(a) for a in ambassadors],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


@router.get("/ambassadors/{ambassador_id}", response_model=AmbassadorResponse)
async def admin_get_ambassador(
    ambassador_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Ambassador).where(Ambassador.id == ambassador_id)
    )
    ambassador = result.scalar_one_or_none()
    if ambassador is None:
        raise HTTPException(status_code=404, detail="Ambassador not found")
    return AmbassadorResponse.model_validate(ambassador)


@router.put("/ambassadors/{ambassador_id}", response_model=AmbassadorResponse)
async def admin_update_ambassador(
    ambassador_id: uuid.UUID,
    data: AdminAmbassadorUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(get_admin_user),
):
    result = await db.execute(
        select(Ambassador).where(Ambassador.id == ambassador_id)
    )
    ambassador = result.scalar_one_or_none()
    if ambassador is None:
        raise HTTPException(status_code=404, detail="Ambassador not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(ambassador, key, value)

    await db.flush()
    await db.refresh(ambassador)

    await log_audit(
        db=db,
        user_id=admin_user.id,
        action="admin_update_ambassador",
        resource_type="ambassador",
        resource_id=str(ambassador_id),
        details=update_data,
        ip_address=request.client.host if request.client else None,
    )

    return AmbassadorResponse.model_validate(ambassador)
