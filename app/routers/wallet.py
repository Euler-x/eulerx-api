import logging

from fastapi import APIRouter

from app.middleware.permissions import RequireAuth, UserPermissions
from app.models.schemas.wallet import BybitBalanceResponse, WalletBalanceResponse
from app.services.bybit import BybitService
from app.services.hyperliquid import HyperliquidService
from app.utils.helpers import utc_now
from app.utils.security import decrypt_private_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wallet", tags=["Wallet"])


@router.get("/balance", response_model=WalletBalanceResponse)
async def get_wallet_balance(
    perms: UserPermissions = RequireAuth,
):
    """Fetch the user's HyperLiquid wallet balance."""
    user = perms.user

    if not user.wallet_address:
        return WalletBalanceResponse(has_wallet=False)

    address = user.wallet_address
    masked = f"{address[:6]}...{address[-4:]}" if len(address) > 10 else address

    hl = HyperliquidService()

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


@router.get("/bybit-balance", response_model=BybitBalanceResponse)
async def get_bybit_balance(
    perms: UserPermissions = RequireAuth,
):
    """Fetch the user's Bybit account balance."""
    user = perms.user

    if not user.bybit_api_key_encrypted or not user.bybit_api_secret_encrypted:
        return BybitBalanceResponse(connected=False)

    try:
        api_key = decrypt_private_key(user.bybit_api_key_encrypted)
        api_secret = decrypt_private_key(user.bybit_api_secret_encrypted)
    except Exception as e:
        logger.error("Failed to decrypt Bybit keys for user %s: %s", user.id, e)
        return BybitBalanceResponse(
            connected=True, testnet=user.bybit_testnet, last_synced=None
        )

    key_masked = f"{api_key[:6]}...{api_key[-4:]}"
    svc = BybitService(testnet=user.bybit_testnet)

    try:
        state = await svc.get_user_state(api_key, api_secret)
        if not state:
            return BybitBalanceResponse(
                connected=True,
                testnet=user.bybit_testnet,
                api_key_masked=key_masked,
                last_synced=None,
            )

        equity = float(state.get("totalEquity", 0))
        available = float(state.get("totalAvailableBalance", 0))

        # Count open positions and unrealized PnL
        open_positions = 0
        unrealized_pnl = 0.0
        try:
            positions = await svc.get_user_positions(api_key, api_secret)
            open_positions = len(positions)
            for pos in positions.values():
                unrealized_pnl += pos.get("unrealized_pnl", 0.0)
        except Exception as e:
            logger.error("Failed to fetch Bybit positions: %s", e)

        return BybitBalanceResponse(
            connected=True,
            testnet=user.bybit_testnet,
            account_equity=round(equity, 2),
            available_balance=round(available, 2),
            unrealized_pnl=round(unrealized_pnl, 2),
            total_balance=round(equity, 2),
            open_positions=open_positions,
            api_key_masked=key_masked,
            last_synced=utc_now().isoformat(),
        )
    except Exception as e:
        logger.error("Failed to fetch Bybit balance: %s", e)
        return BybitBalanceResponse(
            connected=True,
            testnet=user.bybit_testnet,
            api_key_masked=key_masked,
            last_synced=None,
        )
