"""Admin config router — CRUD for system configuration key-value pairs."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.middleware.audit import log_audit
from app.middleware.permissions import RequireAdmin, UserPermissions
from app.models.admin_config import AdminConfig
from app.models.schemas.admin import AdminConfigResponse, AdminConfigUpdate
from app.models.schemas.common import MessageResponse

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
    admin_perms: UserPermissions = RequireAdmin,
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
        user_id=admin_perms.user.id,
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
    admin_perms: UserPermissions = RequireAdmin,
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
        user_id=admin_perms.user.id,
        action="admin_delete_config",
        resource_type="admin_config",
        resource_id=key,
        ip_address=request.client.host if request.client else None,
    )

    return MessageResponse(message=f"Config key '{key}' deleted successfully")


@router.get("/settings")
async def admin_get_settings(db: AsyncSession = Depends(get_db)):
    """Return all configurable settings with metadata, grouped by category.

    Merges DB overrides with defaults from config.py.
    """
    from app.services.dynamic_config import get_all_config

    return await get_all_config(db)


@router.put("/settings/{key}")
async def admin_set_setting(
    key: str,
    data: AdminConfigUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_perms: UserPermissions = RequireAdmin,
):
    """Set a single setting value. Stores in admin_config table."""
    from app.services.dynamic_config import CONFIG_DEFAULTS

    if key not in CONFIG_DEFAULTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown setting: {key}",
        )

    result = await db.execute(select(AdminConfig).where(AdminConfig.key == key))
    config = result.scalar_one_or_none()

    if config is None:
        config = AdminConfig(
            key=key,
            value=data.value,
            description=CONFIG_DEFAULTS[key]["description"],
        )
        db.add(config)
    else:
        config.value = data.value

    await db.flush()

    await log_audit(
        db=db,
        user_id=admin_perms.user.id,
        action="admin_set_setting",
        resource_type="setting",
        resource_id=key,
        details={"value": data.value},
        ip_address=request.client.host if request.client else None,
    )

    meta = CONFIG_DEFAULTS[key]
    raw = config.value
    val = raw.get("value", raw) if isinstance(raw, dict) else raw

    return {
        "key": key,
        "value": val,
        "category": meta["category"],
        "label": meta["label"],
        "source": "database",
    }
