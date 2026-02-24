"""Market data endpoints — WebSocket stream and REST fallback."""

import asyncio
import json
import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.services.market_ws import market_data_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/market", tags=["market"])


@router.websocket("/ws")
async def market_ws(websocket: WebSocket):
    """Stream live market data to the frontend via WebSocket.

    Sends JSON arrays of token objects sorted by 24h change (top gainers first).
    Each token object: {symbol, midPrice, markPx, prevDayPx, change24h,
    funding, openInterest, dayNtlVlm, szDecimals, maxLeverage}
    """
    await websocket.accept()
    queue = market_data_manager.subscribe()
    try:
        while True:
            snapshot = await queue.get()
            await websocket.send_text(json.dumps(snapshot))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("Market WS client disconnected: %s", e)
    finally:
        market_data_manager.unsubscribe(queue)


@router.get("/top-gainers")
async def top_gainers(limit: int = Query(default=50, ge=1, le=200)):
    """REST fallback — return current top gainers snapshot."""
    snapshot = market_data_manager.get_snapshot()
    return snapshot[:limit]


@router.get("/all")
async def all_market_data():
    """REST fallback — return full market snapshot."""
    return market_data_manager.get_snapshot()
