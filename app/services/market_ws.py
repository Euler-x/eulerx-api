"""Hyperliquid WebSocket manager for live market data.

Connects to Hyperliquid's WebSocket feed, subscribes to allMids for live
mid-prices, and periodically fetches full asset contexts via REST.  Relays
combined market snapshots to all connected frontend clients.
"""

import asyncio
import json
import logging
import time
from typing import Any

import httpx
import websockets
from websockets.exceptions import ConnectionClosed

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Hyperliquid WebSocket endpoints
_WS_MAINNET = "wss://api.hyperliquid.xyz/ws"
_WS_TESTNET = "wss://api.hyperliquid-testnet.xyz/ws"

# REST endpoints
_REST_MAINNET = "https://api.hyperliquid.xyz"
_REST_TESTNET = "https://api.hyperliquid-testnet.xyz"

# How often to refresh full metadata via REST (seconds)
_META_REFRESH_INTERVAL = 60
# Ping interval to keep Hyperliquid WS alive (< 60s timeout)
_PING_INTERVAL = 50
# Reconnect delay on failure
_RECONNECT_DELAY = 5
# How often to push snapshots to frontend clients
_BROADCAST_INTERVAL = 2


class MarketDataManager:
    """Singleton manager for Hyperliquid market data streaming."""

    def __init__(self) -> None:
        self._ws_url = _WS_TESTNET if settings.hyperliquid_testnet else _WS_MAINNET
        self._rest_url = _REST_TESTNET if settings.hyperliquid_testnet else _REST_MAINNET
        self._mids: dict[str, str] = {}
        self._meta: list[dict] = []  # universe metadata
        self._asset_ctxs: list[dict] = []  # live asset contexts
        self._last_meta_fetch: float = 0
        self._snapshot: list[dict[str, Any]] = []  # computed market snapshot
        self._clients: set[asyncio.Queue] = set()
        self._running = False
        self._tasks: list[asyncio.Task] = []

    # ── Client management ──────────────────────────────────────

    def subscribe(self) -> asyncio.Queue:
        """Register a frontend client and return its message queue."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        self._clients.add(queue)
        # Send current snapshot immediately
        if self._snapshot:
            try:
                queue.put_nowait(self._snapshot)
            except asyncio.QueueFull:
                pass
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._clients.discard(queue)

    # ── Lifecycle ──────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        logger.info("MarketDataManager starting (ws=%s)", self._ws_url)
        self._tasks = [
            asyncio.create_task(self._ws_loop(), name="hl-ws"),
            asyncio.create_task(self._meta_loop(), name="hl-meta"),
            asyncio.create_task(self._broadcast_loop(), name="hl-broadcast"),
        ]

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._clients.clear()
        logger.info("MarketDataManager stopped")

    # ── REST: fetch metadata + asset contexts ──────────────────

    async def _fetch_meta_and_ctxs(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{self._rest_url}/info",
                    json={"type": "metaAndAssetCtxs"},
                )
                resp.raise_for_status()
                data = resp.json()
            if isinstance(data, list) and len(data) >= 2:
                self._meta = data[0].get("universe", [])
                self._asset_ctxs = data[1]
                self._last_meta_fetch = time.monotonic()
                logger.debug("Fetched meta: %d assets", len(self._meta))
        except Exception as e:
            logger.error("Failed to fetch metaAndAssetCtxs: %s: %s", type(e).__name__, e)

    async def _meta_loop(self) -> None:
        """Periodically refresh metadata via REST."""
        while self._running:
            await self._fetch_meta_and_ctxs()
            await asyncio.sleep(_META_REFRESH_INTERVAL)

    # ── WebSocket: stream allMids ──────────────────────────────

    async def _ws_loop(self) -> None:
        """Connect to Hyperliquid WS, subscribe to allMids, and maintain connection."""
        while self._running:
            try:
                async with websockets.connect(self._ws_url) as ws:
                    logger.info("Connected to Hyperliquid WebSocket")
                    # Subscribe to allMids
                    await ws.send(json.dumps({
                        "method": "subscribe",
                        "subscription": {"type": "allMids"},
                    }))

                    ping_task = asyncio.create_task(self._ping_loop(ws))
                    try:
                        async for raw in ws:
                            msg = json.loads(raw)
                            channel = msg.get("channel", "")
                            if channel == "allMids":
                                self._mids = msg.get("data", {}).get("mids", {})
                                self._rebuild_snapshot()
                            elif channel == "pong" or channel == "subscriptionResponse":
                                pass
                            elif channel == "error":
                                logger.warning("WS error: %s", msg.get("data"))
                    finally:
                        ping_task.cancel()

            except ConnectionClosed as e:
                logger.warning("Hyperliquid WS closed: %s", e)
            except Exception as e:
                logger.error("Hyperliquid WS error: %s", e)

            if self._running:
                logger.info("Reconnecting in %ds...", _RECONNECT_DELAY)
                await asyncio.sleep(_RECONNECT_DELAY)

    async def _ping_loop(self, ws) -> None:
        """Send pings to keep the connection alive."""
        while True:
            await asyncio.sleep(_PING_INTERVAL)
            try:
                await ws.send(json.dumps({"method": "ping"}))
            except Exception:
                break

    # ── Snapshot computation ───────────────────────────────────

    def _rebuild_snapshot(self) -> None:
        """Combine mids + metadata into a sorted market snapshot."""
        if not self._meta or not self._mids:
            return

        tokens: list[dict[str, Any]] = []
        for i, asset in enumerate(self._meta):
            symbol = asset.get("name", "")
            mid_str = self._mids.get(symbol)
            if not mid_str:
                continue

            mid_price = float(mid_str)
            ctx = self._asset_ctxs[i] if i < len(self._asset_ctxs) else {}

            prev_day_px_str = ctx.get("prevDayPx", "0")
            prev_day_px = float(prev_day_px_str) if prev_day_px_str else 0
            change_24h = (
                ((mid_price - prev_day_px) / prev_day_px * 100)
                if prev_day_px > 0
                else 0
            )

            tokens.append({
                "symbol": symbol,
                "midPrice": mid_price,
                "markPx": float(ctx.get("markPx", mid_str)),
                "prevDayPx": prev_day_px,
                "change24h": round(change_24h, 2),
                "funding": ctx.get("funding", "0"),
                "openInterest": ctx.get("openInterest", "0"),
                "dayNtlVlm": ctx.get("dayNtlVlm", "0"),
                "szDecimals": asset.get("szDecimals", 0),
                "maxLeverage": asset.get("maxLeverage", 50),
            })

        # Sort by 24h change descending (top gainers first)
        tokens.sort(key=lambda t: t["change24h"], reverse=True)
        self._snapshot = tokens

    # ── Broadcast to frontend clients ──────────────────────────

    async def _broadcast_loop(self) -> None:
        """Push snapshots to all connected clients at a fixed interval."""
        while self._running:
            await asyncio.sleep(_BROADCAST_INTERVAL)
            if not self._snapshot or not self._clients:
                continue
            dead: list[asyncio.Queue] = []
            for queue in self._clients:
                try:
                    # Replace stale data — drop old if full
                    if queue.full():
                        try:
                            queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    queue.put_nowait(self._snapshot)
                except Exception:
                    dead.append(queue)
            for q in dead:
                self._clients.discard(q)

    # ── REST fallback ──────────────────────────────────────────

    def get_snapshot(self) -> list[dict[str, Any]]:
        """Return the latest market snapshot (for REST fallback)."""
        return self._snapshot


# Module-level singleton
market_data_manager = MarketDataManager()
