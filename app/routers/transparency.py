from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.middleware.permissions import RequireVerified, UserPermissions
from app.models.schemas.transparency import (
    LivePositionResponse,
    ProofOfReservesResponse,
    WalletInfoResponse,
)
from app.models.strategy import Strategy
from app.services.transparency import TransparencyService

router = APIRouter(prefix="/transparency", tags=["Transparency"])


def _require_wallet(perms: UserPermissions) -> str:
    """Extract wallet address or raise 400 if not connected."""
    if not perms.user.wallet_address:
        raise HTTPException(
            status_code=400,
            detail="No wallet connected. Connect a Hyperliquid wallet first.",
        )
    return perms.user.wallet_address


@router.get("/proof-of-reserves", response_model=ProofOfReservesResponse)
async def get_proof_of_reserves(
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    wallet_address = _require_wallet(perms)

    result = await db.execute(select(Strategy).where(Strategy.user_id == perms.id))
    strategies = list(result.scalars().all())

    service = TransparencyService()
    data = await service.get_proof_of_reserves(wallet_address, strategies)
    return ProofOfReservesResponse(**data)


@router.get("/positions", response_model=list[LivePositionResponse])
async def get_live_positions(
    perms: UserPermissions = RequireVerified,
):
    wallet_address = _require_wallet(perms)

    service = TransparencyService()
    positions = await service.get_live_positions(wallet_address)
    return [LivePositionResponse(**p) for p in positions]


@router.get("/wallet-info", response_model=WalletInfoResponse)
async def get_wallet_info(
    perms: UserPermissions = RequireVerified,
):
    if not perms.user.wallet_address:
        return WalletInfoResponse()

    address = perms.user.wallet_address
    masked = f"{address[:6]}...{address[-4:]}" if len(address) > 10 else address
    wallet_type = (
        (perms.user.wallet_type or "connected").value
        if hasattr(perms.user.wallet_type, "value")
        else str(perms.user.wallet_type or "connected")
    )

    permissions = TransparencyService.get_agent_key_permissions(wallet_type)
    explorer_link = TransparencyService.get_explorer_link(address)

    from app.models.schemas.transparency import AgentKeyPermissions

    return WalletInfoResponse(
        wallet_address=masked,
        explorer_link=explorer_link,
        agent_permissions=AgentKeyPermissions(**permissions),
    )
