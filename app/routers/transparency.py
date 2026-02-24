from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.middleware.auth import require_verified_email
from app.models.schemas.transparency import (
    LivePositionResponse,
    ProofOfReservesResponse,
    WalletInfoResponse,
)
from app.models.strategy import Strategy
from app.models.user import User
from app.services.transparency import TransparencyService
from app.utils.security import hash_wallet_address

router = APIRouter(prefix="/transparency", tags=["Transparency"])


def _require_wallet(user: User) -> str:
    """Extract wallet address or raise 400 if not connected."""
    if not user.wallet_address:
        raise HTTPException(
            status_code=400,
            detail="No wallet connected. Connect a Hyperliquid wallet first.",
        )
    return user.wallet_address


@router.get("/proof-of-reserves", response_model=ProofOfReservesResponse)
async def get_proof_of_reserves(
    current_user: User = Depends(require_verified_email),
    db: AsyncSession = Depends(get_db),
):
    wallet_address = _require_wallet(current_user)

    result = await db.execute(
        select(Strategy).where(Strategy.user_id == current_user.id)
    )
    strategies = list(result.scalars().all())

    service = TransparencyService()
    data = await service.get_proof_of_reserves(wallet_address, strategies)
    return ProofOfReservesResponse(**data)


@router.get("/positions", response_model=list[LivePositionResponse])
async def get_live_positions(
    current_user: User = Depends(require_verified_email),
):
    wallet_address = _require_wallet(current_user)

    service = TransparencyService()
    positions = await service.get_live_positions(wallet_address)
    return [LivePositionResponse(**p) for p in positions]


@router.get("/wallet-info", response_model=WalletInfoResponse)
async def get_wallet_info(
    current_user: User = Depends(require_verified_email),
):
    if not current_user.wallet_address:
        return WalletInfoResponse()

    address = current_user.wallet_address
    masked = f"{address[:6]}...{address[-4:]}" if len(address) > 10 else address
    wallet_type = (current_user.wallet_type or "connected").value if hasattr(
        current_user.wallet_type, "value"
    ) else str(current_user.wallet_type or "connected")

    permissions = TransparencyService.get_agent_key_permissions(wallet_type)
    explorer_link = TransparencyService.get_explorer_link(address)

    from app.models.schemas.transparency import AgentKeyPermissions

    return WalletInfoResponse(
        wallet_address=masked,
        explorer_link=explorer_link,
        agent_permissions=AgentKeyPermissions(**permissions),
    )
