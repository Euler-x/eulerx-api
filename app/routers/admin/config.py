"""Admin config router — CRUD for system configuration key-value pairs."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.middleware.audit import log_audit
from app.middleware.auth import get_admin_user
from app.models.admin_config import AdminConfig
from app.models.schemas.admin import AdminConfigResponse, AdminConfigUpdate
from app.models.schemas.common import MessageResponse
from app.models.user import User

router = APIRouter()

PROTECTED_KEYS = {"trading_halt"}


@router.get("/config", response_model=list[AdminConfigResponse])
async def admin_list_config(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AdminConfig))
    configs = result.scalars().all()
    return [AdminConfigResponse.model_validate(c) for c in configs]


@router.get("/config/{key}", response_model=AdminConfigResponse)
async def admin_get_config(key: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AdminConfig).where(AdminConfig.key == key))
    config = result.scalar_one_or_none()
    if config is None:
        raise HTTPException(status_code=404, detail="Config key not found")
    return AdminConfigResponse.model_validate(config)


@router.put("/config/{key}", response_model=AdminConfigResponse)
async def admin_set_config(
    key: str,
    data: AdminConfigUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(get_admin_user),
):
    result = await db.execute(select(AdminConfig).where(AdminConfig.key == key))
    config = result.scalar_one_or_none()

    if config is None:
        config = AdminConfig(key=key, value=data.value, description=data.description)
        db.add(config)
    else:
        config.value = data.value
        if data.description is not None:
            config.description = data.description

    await db.flush()

    await log_audit(
        db=db,
        user_id=admin_user.id,
        action="admin_set_config",
        resource_type="admin_config",
        resource_id=key,
        ip_address=request.client.host if request.client else None,
    )

    return AdminConfigResponse.model_validate(config)


@router.delete("/config/{key}", response_model=MessageResponse)
async def admin_delete_config(
    key: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(get_admin_user),
):
    if key in PROTECTED_KEYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Config key '{key}' is protected and cannot be deleted",
        )

    result = await db.execute(select(AdminConfig).where(AdminConfig.key == key))
    config = result.scalar_one_or_none()
    if config is None:
        raise HTTPException(status_code=404, detail="Config key not found")

    await db.delete(config)

    await log_audit(
        db=db,
        user_id=admin_user.id,
        action="admin_delete_config",
        resource_type="admin_config",
        resource_id=key,
        ip_address=request.client.host if request.client else None,
    )

    return MessageResponse(message=f"Config key '{key}' deleted successfully")
