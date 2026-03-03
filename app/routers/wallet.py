import logging

from fastapi import APIRouter

from app.middleware.permissions import RequireAuth, UserPermissions
from app.models.schemas.wallet import WalletBalanceResponse
from app.services.hyperliquid import HyperliquidService
from app.utils.helpers import utc_now

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wallet", tags=["Wallet"])


@router.get("/balance", response_model=WalletBalanceResponse)
async def get_wallet_balance(
    perms: UserPermissions = RequireAuth,
):
    """Fetch the user's HyperLiquid wallet balance.

    Returns zeros with has_wallet=False if no wallet is connected.
    Returns zeros with has_wallet=True if the HL API is unreachable,
    with last_synced=None to signal a sync failure.
    """
    user = perms.user

    if not user.wallet_address:
        return WalletBalanceResponse(has_wallet=False)

    address = user.wallet_address
    masked = f"{address[:6]}...{address[-4:]}" if len(address) > 10 else address

    hl = HyperliquidService()

    # Fetch perps state
    perps_balance = 0.0
    available = 0.0
    margin_used = 0.0
    unrealized_pnl = 0.0
    open_positions = 0

    try:
        user_state = await hl.get_user_state(address)
        margin_summary = user_state.get("marginSummary", {})
        perps_balance = float(margin_summary.get("accountValue", 0))
        available = float(margin_summary.get("totalRawUsd", 0))
        margin_used = float(margin_summary.get("totalMarginUsed", 0))

        positions = user_state.get("assetPositions", [])
        for p in positions:
            pos = p.get("position", {})
            size = float(pos.get("szi", 0))
            if size != 0:
                open_positions += 1
                unrealized_pnl += float(pos.get("unrealizedPnl", 0))
    except Exception as e:
        logger.error("Failed to fetch perps state for wallet balance: %s", e)
        return WalletBalanceResponse(
            has_wallet=True,
            wallet_address_masked=masked,
            last_synced=None,
        )

    # Fetch spot balances
    spot_balance = 0.0
    try:
        spot_balances = await hl.get_spot_balances(address)
        spot_balance = sum(b["total"] for b in spot_balances)
    except Exception as e:
        logger.error("Failed to fetch spot balances for wallet balance: %s", e)

    total_balance = round(perps_balance + spot_balance, 2)

    return WalletBalanceResponse(
        has_wallet=True,
        account_equity=round(perps_balance, 2),
        available_balance=round(available, 2),
        margin_used=round(margin_used, 2),
        unrealized_pnl=round(unrealized_pnl, 2),
        spot_balance=round(spot_balance, 2),
        total_balance=total_balance,
        open_positions=open_positions,
        wallet_address_masked=masked,
        last_synced=utc_now().isoformat(),
    )
