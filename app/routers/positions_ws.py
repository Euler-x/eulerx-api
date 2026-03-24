"""Live positions WebSocket — streams HL + Bybit positions per authenticated user."""

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.db.base import async_session_factory
from app.models.user import User
from app.services.bybit import BybitService
from app.services.hyperliquid import HyperliquidService
from app.utils.helpers import utc_now
from app.utils.security import decrypt_private_key, verify_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transparency", tags=["Transparency"])

POLL_INTERVAL = 5  # seconds between position refreshes


async def _get_user_from_token(token: str) -> User | None:
    """Validate JWT and return User from DB."""
    try:
        payload = verify_token(token, expected_type="access")
        user_id = payload.get("sub")
        if not user_id:
            return None
    except Exception:
        return None

    try:
        async with async_session_factory() as db:
            result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
            return result.scalar_one_or_none()
    except Exception:
        return None


async def _fetch_hl_positions(user: User) -> list[dict]:
    """Fetch HyperLiquid positions for user."""
    if not user.wallet_address:
        return []

    try:
        hl = HyperliquidService()
        user_state = await hl.get_user_state(user.wallet_address)
        positions = []
        for p in user_state.get("assetPositions", []):
            pos = p.get("position", {})
            size = float(pos.get("szi", 0))
            if size == 0:
                continue

            leverage_info = pos.get("leverage", {})
            leverage_val = (
                leverage_info.get("value", 1) if isinstance(leverage_info, dict) else 1
            )

            positions.append(
                {
                    "exchange": "hyperliquid",
                    "symbol": pos.get("coin", ""),
                    "side": "long" if size > 0 else "short",
                    "size": abs(size),
                    "entry_price": float(pos.get("entryPx", 0)),
                    "mark_price": float(pos.get("positionValue", 0)) / abs(size)
                    if abs(size) > 0
                    else 0,
                    "unrealized_pnl": float(pos.get("unrealizedPnl", 0)),
                    "leverage": float(leverage_val),
                    "liquidation_price": (
                        float(pos["liquidationPx"])
                        if pos.get("liquidationPx")
                        else None
                    ),
                    "margin_used": float(pos.get("marginUsed", 0)),
                    "position_value": float(pos.get("positionValue", 0)),
                }
            )
        return positions
    except Exception as e:
        logger.error("WS: Failed to fetch HL positions: %s", e)
        return []


async def _fetch_bybit_positions(user: User) -> list[dict]:
    """Fetch Bybit positions for user."""
    if not user.bybit_api_key_encrypted or not user.bybit_api_secret_encrypted:
        return []

    try:
        api_key = decrypt_private_key(user.bybit_api_key_encrypted)
        api_secret = decrypt_private_key(user.bybit_api_secret_encrypted)
    except Exception:
        return []

    try:
        svc = BybitService(testnet=user.bybit_testnet)
        raw_positions = await svc.get_user_positions(api_key, api_secret)
        positions = []
        for symbol, pos in raw_positions.items():
            size = abs(pos.get("size", 0))
            if size == 0:
                continue

            positions.append(
                {
                    "exchange": "bybit",
                    "symbol": symbol,
                    "side": "long" if pos.get("side") == "Buy" else "short",
                    "size": size,
                    "entry_price": pos.get("entry_px", 0),
                    "mark_price": pos.get("mark_price", 0),
                    "unrealized_pnl": pos.get("unrealized_pnl", 0),
                    "leverage": pos.get("leverage", 0),
                    "liquidation_price": pos.get("liq_price"),
                    "margin_used": pos.get("margin_used", 0),
                    "position_value": pos.get("position_value", 0),
                }
            )
        return positions
    except Exception as e:
        logger.error("WS: Failed to fetch Bybit positions: %s", e)
        return []


@router.websocket("/ws/positions")
async def positions_ws(websocket: WebSocket, token: str = ""):
    """Stream live positions from HL + Bybit for an authenticated user.

    Connect with: ws://host/api/v1/transparency/ws/positions?token=<JWT>
    Sends JSON every 5s with structure:
    {
      "positions": [...],
      "timestamp": "2026-03-24T...",
      "hl_count": 2,
      "bybit_count": 1
    }
    """
    # Authenticate
    user = await _get_user_from_token(token)
    if not user:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await websocket.accept()
    logger.info("Positions WS connected for user %s", user.id)

    try:
        while True:
            # Fetch positions from both exchanges concurrently
            hl_positions, bybit_positions = await asyncio.gather(
                _fetch_hl_positions(user),
                _fetch_bybit_positions(user),
                return_exceptions=True,
            )

            # Handle exceptions from gather
            if isinstance(hl_positions, Exception):
                logger.error("WS HL positions error: %s", hl_positions)
                hl_positions = []
            if isinstance(bybit_positions, Exception):
                logger.error("WS Bybit positions error: %s", bybit_positions)
                bybit_positions = []

            all_positions = hl_positions + bybit_positions

            payload = {
                "positions": all_positions,
                "timestamp": utc_now().isoformat(),
                "hl_count": len(hl_positions),
                "bybit_count": len(bybit_positions),
            }

            await websocket.send_text(json.dumps(payload))
            await asyncio.sleep(POLL_INTERVAL)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("Positions WS disconnected for user %s: %s", user.id, e)
