"""Binance USDⓈ-M Perpetual Futures service.

Uses the Binance Futures REST API directly via httpx with HMAC-SHA256 signing.
All authenticated endpoints require timestamp + signature in the query string.

Key API facts (as of Dec 2025):
- Entry orders: POST /fapi/v1/order  (MARKET or LIMIT only)
- TP/SL orders: POST /fapi/v1/algoOrder  (STOP_MARKET / TAKE_PROFIT_MARKET)
- Positions:    GET  /fapi/v2/positionRisk  (v1 is deprecated)
- Balance:      GET  /fapi/v2/balance
- Closed PnL:   GET  /fapi/v1/income?incomeType=REALIZED_PNL
"""

import asyncio
import hashlib
import hmac
import logging
import math
import time
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_PROD_BASE = "https://fapi.binance.com"
_TEST_BASE = "https://testnet.binancefuture.com"


class BinanceService:
    """Binance USDⓈ-M Futures API service for linear perpetual contracts."""

    def __init__(self, testnet: bool = False):
        self.testnet = testnet or getattr(settings, "binance_testnet", False)
        self.base_url = _TEST_BASE if self.testnet else _PROD_BASE

    # ── Signing helpers ────────────────────────────────────────────

    def _sign(self, api_secret: str, params: dict) -> str:
        """Return HMAC-SHA256 hex signature for the given params dict."""
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        return hmac.new(
            api_secret.encode(),
            qs.encode(),
            hashlib.sha256,
        ).hexdigest()

    def _auth_params(self, api_secret: str, extra: dict | None = None) -> dict:
        """Build timestamp + recvWindow + signature params.

        recvWindow is set to 10s (10000ms) — Binance allows up to 60000ms.
        The previous 5000ms was too tight for cross-region requests during
        volatile markets, producing spurious -1021 timestamp errors that
        caused the ATE to abort the order (and any attached TP/SL).
        """
        params: dict[str, Any] = {
            **(extra or {}),
            "timestamp": int(time.time() * 1000),
            "recvWindow": 10000,
        }
        params["signature"] = self._sign(api_secret, params)
        return params

    def _headers(self, api_key: str) -> dict:
        return {
            "X-MBX-APIKEY": api_key,
            "Content-Type": "application/x-www-form-urlencoded",
        }

    @staticmethod
    def _format_api_error(response: httpx.Response) -> str:
        """Return an actionable Binance credential validation error."""
        status_code = response.status_code
        try:
            payload = response.json()
        except Exception:
            payload = {}

        binance_code = payload.get("code")
        binance_msg = str(payload.get("msg") or response.text[:200] or "").strip()

        if binance_code == -2015:
            return (
                "Binance rejected this key for the selected environment. "
                "Use a Binance.com USD-M Futures API key for mainnet, confirm your "
                "Futures account is activated, enable Futures permission on the key, "
                "and if IP restriction is enabled whitelist the server IP. "
                f"Binance message: {binance_msg}"
            )
        if binance_code == -2014:
            return "Invalid Binance API key format. Check that the full API key was copied."
        if binance_code == -1022:
            return "Invalid Binance API secret or signature. Check that the full secret was copied."
        if binance_code == -1021:
            return (
                "Binance rejected the request timestamp. Please try again; if it persists, "
                "the server clock needs to be synchronized."
            )
        if binance_code == -1100:
            return "Invalid characters in the Binance API key or secret."
        if status_code == 403:
            return (
                "Binance blocked this request. Check Futures permissions, IP whitelist "
                "settings, and regional availability for Binance Futures."
            )
        if status_code == 401:
            return (
                "Binance rejected this API key. Check that it belongs to the selected "
                "mainnet/testnet environment and has USD-M Futures access."
            )
        if binance_msg:
            return f"Binance HTTP {status_code}: {binance_msg}"
        return f"Binance HTTP {status_code}"

    # ── Precision helpers ──────────────────────────────────────────

    @staticmethod
    def _round_step(value: float, step: float) -> float:
        """Round down to the nearest valid step size (Binance LOT_SIZE/PRICE_FILTER)."""
        if step <= 0:
            return value
        d = Decimal(str(value))
        s = Decimal(str(step))
        return float((d / s).quantize(Decimal("1"), rounding=ROUND_DOWN) * s)

    @staticmethod
    def _step_decimals(step: float) -> int:
        """Return the number of decimal places implied by a step size string."""
        step_str = f"{step:.10f}".rstrip("0")
        return len(step_str.split(".")[-1]) if "." in step_str else 0

    # ── Exchange info cache ────────────────────────────────────────

    _exchange_info_cache: dict[str, dict] = {}

    async def get_exchange_info(self) -> dict:
        """Fetch /fapi/v1/exchangeInfo and return parsed symbol map.

        Result is cached in-process for the lifetime of the worker to avoid
        hitting the endpoint on every order. Restart the worker to force refresh.
        """
        cache_key = self.base_url
        if cache_key in self._exchange_info_cache:
            return self._exchange_info_cache[cache_key]

        def _fetch():
            with httpx.Client(timeout=10.0) as client:
                r = client.get(f"{self.base_url}/fapi/v1/exchangeInfo")
                r.raise_for_status()
                return r.json()

        data = await asyncio.to_thread(_fetch)
        symbol_map: dict[str, dict] = {}
        for sym in data.get("symbols", []):
            if (
                sym.get("contractType") == "PERPETUAL"
                and sym.get("quoteAsset") == "USDT"
            ):
                filters: dict[str, dict] = {}
                for f in sym.get("filters", []):
                    filters[f["filterType"]] = f
                lot_size = filters.get("LOT_SIZE", {})
                market_lot_size = filters.get("MARKET_LOT_SIZE", {}) or lot_size
                symbol_map[sym["symbol"]] = {
                    "tick_size": float(
                        filters.get("PRICE_FILTER", {}).get("tickSize", "0.01")
                    ),
                    "step_size": float(lot_size.get("stepSize", "0.001")),
                    "min_qty": float(lot_size.get("minQty", "0.001")),
                    "max_qty": float(lot_size.get("maxQty", "0") or "inf"),
                    "market_step_size": float(
                        market_lot_size.get(
                            "stepSize", lot_size.get("stepSize", "0.001")
                        )
                    ),
                    "market_min_qty": float(
                        market_lot_size.get("minQty", lot_size.get("minQty", "0.001"))
                    ),
                    "market_max_qty": float(
                        market_lot_size.get("maxQty", lot_size.get("maxQty", "0"))
                        or "inf"
                    ),
                    "min_notional": float(
                        filters.get("MIN_NOTIONAL", {}).get("notional", "5")
                    ),
                    "trigger_protect": float(sym.get("triggerProtect", "0") or "0"),
                }
        self._exchange_info_cache[cache_key] = symbol_map
        return symbol_map

    async def get_lot_size_info(self, symbol: str, order_type: str = "market") -> dict:
        """Return qty_step, min_order_qty, max_order_qty for a symbol."""
        try:
            info = await self.get_exchange_info()
            sym = info.get(symbol, {})
            is_market = order_type.lower() == "market"
            return {
                "qty_step": (
                    sym.get("market_step_size", sym.get("step_size", 0.001))
                    if is_market
                    else sym.get("step_size", 0.001)
                ),
                "min_order_qty": (
                    sym.get("market_min_qty", sym.get("min_qty", 0.001))
                    if is_market
                    else sym.get("min_qty", 0.001)
                ),
                "max_order_qty": (
                    sym.get("market_max_qty", sym.get("max_qty", float("inf")))
                    if is_market
                    else sym.get("max_qty", float("inf"))
                ),
            }
        except Exception:
            return {
                "qty_step": 0.001,
                "min_order_qty": 0.001,
                "max_order_qty": float("inf"),
            }

    async def get_symbol_rules(self, symbol: str) -> dict:
        """Return cached trading rules for a symbol."""
        try:
            info = await self.get_exchange_info()
            return info.get(symbol, {})
        except Exception:
            return {}

    async def get_tick_size(self, symbol: str) -> float:
        """Return the price tick size for a symbol."""
        rules = await self.get_symbol_rules(symbol)
        return rules.get("tick_size", 0.01)

    # ── Position mode ──────────────────────────────────────────────

    async def get_position_mode(self, api_key: str, api_secret: str) -> bool:
        """Return True if account is in HEDGE mode (dualSidePosition=true)."""

        def _fetch():
            params = self._auth_params(api_secret)
            with httpx.Client(timeout=10.0) as client:
                r = client.get(
                    f"{self.base_url}/fapi/v1/positionSide/dual",
                    params=params,
                    headers=self._headers(api_key),
                )
                r.raise_for_status()
                return r.json().get("dualSidePosition", False)

        try:
            return await asyncio.to_thread(_fetch)
        except Exception as e:
            logger.warning(
                "Could not fetch Binance position mode: %s — assuming ONE_WAY", e
            )
            return False

    # ── Market data (public, no auth) ──────────────────────────────

    async def get_market_data(self) -> list[dict]:
        """Fetch all USDT-M perpetual tickers and normalise to our format."""

        def _fetch():
            with httpx.Client(timeout=10.0) as client:
                r = client.get(f"{self.base_url}/fapi/v1/ticker/24hr")
                r.raise_for_status()
                return r.json()

        tickers = await asyncio.to_thread(_fetch)
        symbols: list[dict] = []
        for t in tickers:
            symbol = t.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            try:
                last_price = float(t.get("lastPrice", 0))
                if last_price <= 0:
                    continue
                quote_vol = float(t.get("quoteVolume", 0))
                if quote_vol < 100_000:
                    continue
                symbols.append(
                    {
                        "symbol": symbol,
                        "name": symbol.replace("USDT", ""),
                        "mid_price": last_price,
                        "last_price": last_price,
                        "price_change_24h_pct": float(t.get("priceChangePercent", 0)),
                        "high_24h": float(t.get("highPrice", 0)),
                        "low_24h": float(t.get("lowPrice", 0)),
                        "volume_24h": float(t.get("volume", 0)),
                        "turnover_24h": quote_vol,
                        "funding_rate": 0.0,  # fetched separately if needed
                        "open_interest": 0.0,
                        "exchange": "binance",
                    }
                )
            except (ValueError, TypeError):
                continue
        return symbols

    async def get_ticker(self, symbol: str) -> dict | None:
        """Fetch single 24hr ticker for a symbol."""

        def _fetch():
            with httpx.Client(timeout=10.0) as client:
                r = client.get(
                    f"{self.base_url}/fapi/v1/ticker/24hr",
                    params={"symbol": symbol},
                )
                r.raise_for_status()
                return r.json()

        try:
            return await asyncio.to_thread(_fetch)
        except Exception:
            return None

    async def get_candles(
        self, symbol: str, interval: str = "1h", limit: int = 24
    ) -> list[dict]:
        """Fetch kline data. Binance intervals: 1m,5m,15m,1h,4h,1d …"""

        def _fetch():
            with httpx.Client(timeout=15.0) as client:
                for attempt in range(3):
                    try:
                        r = client.get(
                            f"{self.base_url}/fapi/v1/klines",
                            params={
                                "symbol": symbol,
                                "interval": interval,
                                "limit": limit,
                            },
                        )
                        r.raise_for_status()
                        raw = r.json()
                        # Format: [OpenTime, O, H, L, C, Volume, CloseTime, ...]
                        return [
                            {
                                "t": int(c[0]),
                                "o": str(c[1]),
                                "h": str(c[2]),
                                "l": str(c[3]),
                                "c": str(c[4]),
                                "v": str(c[5]),
                            }
                            for c in raw
                        ]
                    except httpx.HTTPStatusError as e:
                        if e.response.status_code == 429 and attempt < 2:
                            time.sleep(1.0 * (attempt + 1))
                            continue
                        raise
            return []

        try:
            return await asyncio.to_thread(_fetch)
        except Exception as e:
            logger.warning("Binance klines failed for %s %s: %s", symbol, interval, e)
            return []

    async def get_top_movers(self, limit: int = 6) -> list[dict]:
        """Get balanced top movers from Binance and enrich with candle data."""
        try:
            market_data = await self.get_market_data()
            if not market_data:
                return []

            def _ticker_activity(sym: dict) -> float:
                change = abs(sym.get("price_change_24h_pct", 0))
                vol = sym.get("turnover_24h", 0)
                return change * math.log1p(vol)

            bucket_size = max(limit // 3, 1)

            gainers = sorted(
                market_data,
                key=lambda s: s.get("price_change_24h_pct", 0),
                reverse=True,
            )[:bucket_size]
            losers = sorted(
                market_data, key=lambda s: s.get("price_change_24h_pct", 0)
            )[:bucket_size]
            by_activity = sorted(market_data, key=_ticker_activity, reverse=True)

            seen: set[str] = set()
            shortlist: list[dict] = []
            for sym in gainers + losers + by_activity:
                name = sym.get("symbol", "")
                if name not in seen:
                    seen.add(name)
                    shortlist.append(sym)
                if len(shortlist) >= limit:
                    break

            enriched = await self.enrich_with_candles(shortlist)
            enriched = [s for s in enriched if s.get("candle_summary")]

            for sym in enriched:
                candle = sym.get("candle_summary", {})
                change = candle.get("price_change_24h_pct", 0)
                rsi = candle.get("rsi_14", 50)
                funding = sym.get("funding_rate", 0)

                if change > 8 and rsi > 65:
                    regime = "extended_up"
                elif change < -8 and rsi < 35:
                    regime = "extended_down"
                elif change > 3:
                    regime = "trending_up"
                elif change < -3:
                    regime = "trending_down"
                else:
                    regime = "ranging"

                candle["regime"] = regime
                candle["funding_rate"] = round(funding * 100, 4)

            logger.info(
                "Binance top movers: %d symbols selected from %d total",
                len(enriched),
                len(market_data),
            )
            return enriched
        except Exception as e:
            logger.error("Failed to fetch Binance top movers: %s", e)
            return []

    async def enrich_with_candles(self, symbols: list[dict]) -> list[dict]:
        """Add candle_summary to each symbol — same output format as Bybit/HL."""

        async def _enrich_one(sym: dict) -> None:
            symbol = sym.get("symbol", "")
            if not symbol:
                return
            try:
                candles_1h, candles_4h, candles_15m, candles_5m = await asyncio.gather(
                    self.get_candles(symbol, "1h", 24),
                    self.get_candles(symbol, "4h", 18),
                    self.get_candles(symbol, "15m", 24),
                    self.get_candles(symbol, "5m", 36),
                )
                if not candles_1h:
                    return

                closes = [float(c["c"]) for c in candles_1h]
                opens = [float(c["o"]) for c in candles_1h]
                highs = [float(c["h"]) for c in candles_1h]
                lows = [float(c["l"]) for c in candles_1h]
                volumes = [float(c["v"]) for c in candles_1h]

                high_24h = max(highs)
                low_24h = min(lows)
                open_24h = opens[0]
                close_latest = closes[-1]
                price_change_24h = (
                    ((close_latest - open_24h) / open_24h) * 100 if open_24h > 0 else 0
                )
                total_volume = sum(volumes)
                avg_volume = total_volume / len(volumes) if volumes else 0
                volatility = (
                    ((high_24h - low_24h) / close_latest) * 100
                    if close_latest > 0
                    else 0
                )

                recent_closes = closes[-6:]
                trend_up = sum(
                    1
                    for i in range(1, len(recent_closes))
                    if recent_closes[i] > recent_closes[i - 1]
                )
                trend_direction = (
                    "up" if trend_up >= 4 else "down" if trend_up <= 1 else "mixed"
                )

                rsi_period = min(14, len(closes) - 1)
                gains, losses_list = [], []
                for i in range(len(closes) - rsi_period, len(closes)):
                    diff = closes[i] - closes[i - 1]
                    gains.append(max(diff, 0))
                    losses_list.append(max(-diff, 0))
                avg_gain = sum(gains) / len(gains) if gains else 0
                avg_loss = sum(losses_list) / len(losses_list) if losses_list else 1
                rs = avg_gain / avg_loss if avg_loss > 0 else 100
                rsi = 100 - (100 / (1 + rs))

                swing_highs, swing_lows = [], []
                for i in range(1, len(highs) - 1):
                    if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
                        swing_highs.append(highs[i])
                    if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
                        swing_lows.append(lows[i])
                resistances = sorted([s for s in swing_highs if s > close_latest])
                nearest_resistance = round(resistances[0], 4) if resistances else None
                supports = sorted(
                    [s for s in swing_lows if s < close_latest], reverse=True
                )
                nearest_support = round(supports[0], 4) if supports else None

                htf_trend = "unknown"
                htf_rsi = None
                if candles_4h and len(candles_4h) >= 6:
                    htf_closes = [float(c["c"]) for c in candles_4h]
                    htf_highs = [float(c["h"]) for c in candles_4h]
                    htf_lows = [float(c["l"]) for c in candles_4h]
                    recent_htf_highs = htf_highs[-6:]
                    recent_htf_lows = htf_lows[-6:]
                    hh = sum(
                        1
                        for i in range(1, len(recent_htf_highs))
                        if recent_htf_highs[i] > recent_htf_highs[i - 1]
                    )
                    hl = sum(
                        1
                        for i in range(1, len(recent_htf_lows))
                        if recent_htf_lows[i] > recent_htf_lows[i - 1]
                    )
                    htf_trend = (
                        "bullish"
                        if hh >= 3 and hl >= 3
                        else "bearish"
                        if hh <= 1 and hl <= 1
                        else "ranging"
                    )
                    rsi_p = min(14, len(htf_closes) - 1)
                    if rsi_p >= 2:
                        g2, l2 = [], []
                        for i in range(len(htf_closes) - rsi_p, len(htf_closes)):
                            d = htf_closes[i] - htf_closes[i - 1]
                            g2.append(max(d, 0))
                            l2.append(max(-d, 0))
                        ag2 = sum(g2) / len(g2) if g2 else 0
                        al2 = sum(l2) / len(l2) if l2 else 1
                        rs2 = ag2 / al2 if al2 > 0 else 100
                        htf_rsi = round(100 - (100 / (1 + rs2)), 1)

                atr_values = []
                for i in range(1, len(candles_1h)):
                    tr = max(
                        highs[i] - lows[i],
                        abs(highs[i] - closes[i - 1]),
                        abs(lows[i] - closes[i - 1]),
                    )
                    atr_values.append(tr)
                atr_14 = (
                    sum(atr_values[-14:]) / min(14, len(atr_values))
                    if atr_values
                    else 0
                )
                atr_pct = (atr_14 / close_latest * 100) if close_latest > 0 else 0

                sma_period = min(20, len(closes))
                sma_20 = (
                    sum(closes[-sma_period:]) / sma_period
                    if sma_period > 0
                    else close_latest
                )
                price_vs_sma20_pct = (
                    ((close_latest - sma_20) / sma_20 * 100) if sma_20 > 0 else 0
                )

                vol_recent = volumes[-6:] if len(volumes) >= 6 else volumes
                vol_prior = (
                    volumes[-12:-6]
                    if len(volumes) >= 12
                    else volumes[: len(volumes) // 2]
                    if volumes
                    else [1]
                )
                avg_vol_recent = sum(vol_recent) / len(vol_recent) if vol_recent else 0
                avg_vol_prior = sum(vol_prior) / len(vol_prior) if vol_prior else 1
                if avg_vol_prior > 0 and avg_vol_recent > avg_vol_prior * 1.3:
                    volume_trend = "increasing"
                elif avg_vol_prior > 0 and avg_vol_recent < avg_vol_prior * 0.7:
                    volume_trend = "decreasing"
                else:
                    volume_trend = "flat"

                wick_candles = min(3, len(candles_1h))
                upper_wick_ratio = 0.0
                lower_wick_ratio = 0.0
                for ci in range(-wick_candles, 0):
                    c_range = highs[ci] - lows[ci]
                    if c_range > 0:
                        body_top = max(opens[ci], closes[ci])
                        body_bot = min(opens[ci], closes[ci])
                        upper_wick_ratio += (highs[ci] - body_top) / c_range
                        lower_wick_ratio += (body_bot - lows[ci]) / c_range
                upper_wick_ratio /= wick_candles if wick_candles > 0 else 1
                lower_wick_ratio /= wick_candles if wick_candles > 0 else 1
                if upper_wick_ratio > 0.45:
                    rejection_signal = "bearish_wicks"
                elif lower_wick_ratio > 0.45:
                    rejection_signal = "bullish_wicks"
                else:
                    rejection_signal = "neutral"

                bb_data = closes[-sma_period:]
                if len(bb_data) >= 2:
                    bb_mean = sum(bb_data) / len(bb_data)
                    bb_std = (
                        sum((x - bb_mean) ** 2 for x in bb_data) / len(bb_data)
                    ) ** 0.5
                    bb_upper = bb_mean + 2 * bb_std
                    bb_lower = bb_mean - 2 * bb_std
                    bb_position = (
                        "above_upper"
                        if close_latest > bb_upper
                        else "below_lower"
                        if close_latest < bb_lower
                        else "middle"
                    )
                else:
                    bb_position = "unknown"

                def _compute_rsi(
                    closes_list: list[float], period: int = 14
                ) -> float | None:
                    p = min(period, len(closes_list) - 1)
                    if p < 2:
                        return None
                    g, lo = [], []
                    for idx in range(len(closes_list) - p, len(closes_list)):
                        d = closes_list[idx] - closes_list[idx - 1]
                        g.append(max(d, 0))
                        lo.append(max(-d, 0))
                    ag = sum(g) / len(g) if g else 0
                    al = sum(lo) / len(lo) if lo else 1
                    rs = ag / al if al > 0 else 100
                    return round(100 - (100 / (1 + rs)), 1)

                ltf_15m_support = None
                ltf_15m_resistance = None
                ltf_15m_trend = "unknown"
                ltf_15m_rsi = None
                if candles_15m and len(candles_15m) >= 6:
                    m15c = [float(c["c"]) for c in candles_15m]
                    m15h = [float(c["h"]) for c in candles_15m]
                    m15l = [float(c["l"]) for c in candles_15m]
                    s_highs = [
                        m15h[i]
                        for i in range(1, len(m15h) - 1)
                        if m15h[i] > m15h[i - 1] and m15h[i] > m15h[i + 1]
                    ]
                    s_lows = [
                        m15l[i]
                        for i in range(1, len(m15l) - 1)
                        if m15l[i] < m15l[i - 1] and m15l[i] < m15l[i + 1]
                    ]
                    res_above = sorted([s for s in s_highs if s > close_latest])
                    sup_below = sorted(
                        [s for s in s_lows if s < close_latest], reverse=True
                    )
                    ltf_15m_resistance = round(res_above[0], 4) if res_above else None
                    ltf_15m_support = round(sup_below[0], 4) if sup_below else None
                    rh = m15h[-8:]
                    rl = m15l[-8:]
                    hh15 = sum(1 for i in range(1, len(rh)) if rh[i] > rh[i - 1])
                    hl15 = sum(1 for i in range(1, len(rl)) if rl[i] > rl[i - 1])
                    ltf_15m_trend = (
                        "bullish"
                        if hh15 >= 4 and hl15 >= 4
                        else "bearish"
                        if hh15 <= 2 and hl15 <= 2
                        else "ranging"
                    )
                    ltf_15m_rsi = _compute_rsi(m15c)

                ltf_5m_trend = "unknown"
                ltf_5m_rsi = None
                ltf_5m_pattern = "neutral"
                ltf_5m_atr_pct = None
                if candles_5m and len(candles_5m) >= 6:
                    m5c = [float(c["c"]) for c in candles_5m]
                    m5h = [float(c["h"]) for c in candles_5m]
                    m5l = [float(c["l"]) for c in candles_5m]
                    m5o = [float(c["o"]) for c in candles_5m]
                    rc5 = m5c[-12:] if len(m5c) >= 12 else m5c
                    up5 = sum(1 for i in range(1, len(rc5)) if rc5[i] > rc5[i - 1])
                    t5 = len(rc5) - 1
                    ltf_5m_trend = (
                        "bullish"
                        if t5 > 0 and up5 / t5 >= 0.65
                        else "bearish"
                        if t5 > 0 and up5 / t5 <= 0.35
                        else "mixed"
                    )
                    ltf_5m_rsi = _compute_rsi(m5c)
                    if len(m5c) >= 3:
                        b3 = sum(1 for i in range(-3, 0) if m5c[i] > m5o[i])
                        ltf_5m_pattern = (
                            "bullish"
                            if b3 >= 2
                            else "bearish"
                            if b3 <= 0
                            else "neutral"
                        )
                    m5_atr_vals = []
                    for i in range(1, len(candles_5m)):
                        tr5 = max(
                            m5h[i] - m5l[i],
                            abs(m5h[i] - m5c[i - 1]),
                            abs(m5l[i] - m5c[i - 1]),
                        )
                        m5_atr_vals.append(tr5)
                    if m5_atr_vals:
                        m5_atr = sum(m5_atr_vals[-14:]) / min(14, len(m5_atr_vals))
                        ltf_5m_atr_pct = round(
                            (m5_atr / close_latest * 100) if close_latest > 0 else 0, 3
                        )

                sym["candle_summary"] = {
                    "high_24h": round(high_24h, 4),
                    "low_24h": round(low_24h, 4),
                    "open_24h": round(open_24h, 4),
                    "price_change_24h_pct": round(price_change_24h, 2),
                    "total_volume_24h": round(total_volume, 2),
                    "avg_hourly_volume": round(avg_volume, 2),
                    "volatility_pct": round(volatility, 2),
                    "recent_trend": trend_direction,
                    "rsi_14": round(rsi, 1),
                    "atr_14_pct": round(atr_pct, 2),
                    "nearest_support": nearest_support,
                    "nearest_resistance": nearest_resistance,
                    "htf_trend_4h": htf_trend,
                    "htf_rsi_4h": htf_rsi,
                    "num_candles": len(candles_1h),
                    "price_vs_sma20_pct": round(price_vs_sma20_pct, 2),
                    "volume_trend": volume_trend,
                    "rejection_signal": rejection_signal,
                    "bb_position": bb_position,
                    "ltf_15m_support": ltf_15m_support,
                    "ltf_15m_resistance": ltf_15m_resistance,
                    "ltf_15m_trend": ltf_15m_trend,
                    "ltf_15m_rsi": ltf_15m_rsi,
                    "ltf_5m_trend": ltf_5m_trend,
                    "ltf_5m_rsi": ltf_5m_rsi,
                    "ltf_5m_candle_pattern": ltf_5m_pattern,
                    "ltf_5m_atr_pct": ltf_5m_atr_pct,
                }
            except Exception as e:
                logger.warning("Failed to fetch Binance candles for %s: %s", symbol, e)

        # Batch in groups of 2 — each symbol needs 4 TF calls
        batch_size = 2
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i : i + batch_size]
            await asyncio.gather(*[_enrich_one(sym) for sym in batch])
            if i + batch_size < len(symbols):
                await asyncio.sleep(0.5)
        return symbols

    # ── Authenticated: account & balance ───────────────────────────

    async def get_balance(self, api_key: str, api_secret: str) -> list[dict]:
        """GET /fapi/v2/balance — returns all asset balances."""

        def _fetch():
            params = self._auth_params(api_secret)
            with httpx.Client(timeout=10.0) as client:
                r = client.get(
                    f"{self.base_url}/fapi/v2/balance",
                    params=params,
                    headers=self._headers(api_key),
                )
                r.raise_for_status()
                return r.json()

        return await asyncio.to_thread(_fetch)

    async def get_available_balance(self, api_key: str, api_secret: str) -> float:
        """Return available USDT balance for new positions."""
        try:
            balances = await self.get_balance(api_key, api_secret)
            for b in balances:
                if b.get("asset") == "USDT":
                    return float(b.get("availableBalance", 0))
            return 0.0
        except Exception as e:
            logger.error("Failed to get Binance available balance: %s", e)
            return 0.0

    async def get_account_value(self, api_key: str, api_secret: str) -> float:
        """Return total wallet balance (equity) in USDT."""
        try:
            balances = await self.get_balance(api_key, api_secret)
            for b in balances:
                if b.get("asset") == "USDT":
                    return float(b.get("balance", 0))
            return 0.0
        except Exception as e:
            logger.error("Failed to get Binance account value: %s", e)
            return 0.0

    async def get_user_positions(
        self, api_key: str, api_secret: str
    ) -> dict[str, dict]:
        """GET /fapi/v2/positionRisk — return {symbol: position_info} for non-zero positions."""

        def _fetch():
            params = self._auth_params(api_secret)
            with httpx.Client(timeout=10.0) as client:
                r = client.get(
                    f"{self.base_url}/fapi/v2/positionRisk",
                    params=params,
                    headers=self._headers(api_key),
                )
                r.raise_for_status()
                return r.json()

        raw = await asyncio.to_thread(_fetch)
        positions: dict[str, dict] = {}
        for p in raw:
            amt = float(p.get("positionAmt", 0))
            if amt == 0:
                continue
            symbol = p["symbol"]
            positions[symbol] = {
                "size": amt,
                "entry_px": float(p.get("entryPrice", 0)),
                "unrealized_pnl": float(p.get("unRealizedProfit", 0)),
                "side": "Buy" if amt > 0 else "Sell",
                "position_side": p.get("positionSide", "BOTH"),
                "mark_price": float(p.get("markPrice", 0)),
                "leverage": float(p.get("leverage", 1)),
                "liq_price": float(p.get("liquidationPrice", 0)) or None,
                "position_value": float(p.get("notional", 0)),
                "margin_used": float(p.get("isolatedWallet", 0)),
            }
        return positions

    # ── Authenticated: trading ──────────────────────────────────────

    async def update_leverage(
        self, api_key: str, api_secret: str, symbol: str, leverage: int
    ) -> dict:
        """POST /fapi/v1/leverage — set leverage for a symbol.

        Binance returns error code -4028 ("No need to change leverage")
        when the leverage is already at the requested value. This is NOT
        a failure — treat it as success so the caller doesn't unnecessarily
        retry with lower leverage values.
        """

        def _set():
            body = self._auth_params(
                api_secret, {"symbol": symbol, "leverage": leverage}
            )
            with httpx.Client(timeout=10.0) as client:
                try:
                    r = client.post(
                        f"{self.base_url}/fapi/v1/leverage",
                        data=body,
                        headers=self._headers(api_key),
                    )
                    r.raise_for_status()
                    data = r.json()
                    return {"success": True, "result": data}
                except httpx.HTTPStatusError as e:
                    err = e.response.json() if e.response.content else {}
                    code = err.get("code")
                    msg = str(err.get("msg", ""))
                    # "No need to change leverage" — already at this value
                    if code == -4028 or "no need to change" in msg.lower():
                        return {"success": True, "result": err, "unchanged": True}
                    return {"success": False, "error": msg, "code": code}
                except Exception as e:
                    return {"success": False, "error": str(e)}

        return await asyncio.to_thread(_set)

    async def place_order(
        self,
        api_key: str,
        api_secret: str,
        symbol: str,
        is_buy: bool,
        size: float,
        order_type: str = "market",
        reduce_only: bool = False,
        price: float | None = None,
        position_side: str = "BOTH",
    ) -> dict:
        """POST /fapi/v1/order — place a MARKET or LIMIT entry/close order."""

        def _place():
            normalized_type = "LIMIT" if order_type == "limit" else "MARKET"
            params: dict[str, Any] = {
                "symbol": symbol,
                "side": "BUY" if is_buy else "SELL",
                "type": normalized_type,
                "quantity": size,
            }
            # In HEDGE mode, positionSide is REQUIRED and must match the side
            # (BUY -> LONG, SELL -> SHORT). In ONE_WAY mode, positionSide is
            # either omitted or set to BOTH.
            if position_side == "BOTH":
                # ONE_WAY mode: positionSide=BOTH is the default and required
                # for reduceOnly to work. Binance rejects BOTH in HEDGE mode.
                params["positionSide"] = "BOTH"
                if reduce_only:
                    params["reduceOnly"] = "true"
            else:
                # HEDGE mode: LONG/SHORT, no reduceOnly needed
                params["positionSide"] = position_side

            if order_type == "limit" and price:
                params["price"] = price
                params["timeInForce"] = "GTC"
            if normalized_type == "MARKET":
                params["newOrderRespType"] = "RESULT"

            body = self._auth_params(api_secret, params)
            with httpx.Client(timeout=15.0) as client:
                try:
                    r = client.post(
                        f"{self.base_url}/fapi/v1/order",
                        data=body,
                        headers=self._headers(api_key),
                    )
                    r.raise_for_status()
                    data = r.json()
                    return {
                        "success": True,
                        "data": data,
                        "tx_hash": str(data.get("orderId", "")),
                        "oid": data.get("orderId"),
                        "avg_price": float(data.get("avgPrice", 0) or 0),
                        "executed_qty": float(data.get("executedQty", 0) or 0),
                        "status": data.get("status"),
                    }
                except httpx.HTTPStatusError as e:
                    err = e.response.json() if e.response.content else {}
                    return {
                        "success": False,
                        "error": err.get("msg", str(e)),
                        "code": err.get("code"),
                    }
                except Exception as e:
                    return {"success": False, "error": str(e)}

        return await asyncio.to_thread(_place)

    async def cancel_algo_order(
        self,
        api_key: str,
        api_secret: str,
        *,
        algo_id: int | None = None,
        client_algo_id: str | None = None,
    ) -> dict:
        """DELETE /fapi/v1/algoOrder â€” cancel an active conditional order."""

        if algo_id is None and not client_algo_id:
            return {"success": False, "error": "algo_id or client_algo_id is required"}

        def _cancel():
            params: dict[str, Any] = {}
            if algo_id is not None:
                params["algoId"] = algo_id
            if client_algo_id:
                params["clientAlgoId"] = client_algo_id
            body = self._auth_params(api_secret, params)
            with httpx.Client(timeout=15.0) as client:
                try:
                    r = client.delete(
                        f"{self.base_url}/fapi/v1/algoOrder",
                        params=body,
                        headers=self._headers(api_key),
                    )
                    r.raise_for_status()
                    return {"success": True, "data": r.json()}
                except httpx.HTTPStatusError as e:
                    err = e.response.json() if e.response.content else {}
                    return {
                        "success": False,
                        "error": err.get("msg", str(e)),
                        "code": err.get("code"),
                    }
                except Exception as e:
                    return {"success": False, "error": str(e)}

        return await asyncio.to_thread(_cancel)

    async def place_tp_sl_orders(
        self,
        api_key: str,
        api_secret: str,
        symbol: str,
        is_buy: bool,
        take_profit_price: float | None = None,
        stop_loss_price: float | None = None,
        position_side: str = "BOTH",
    ) -> dict:
        """Place TP and SL via POST /fapi/v1/algoOrder (CONDITIONAL type).

        Binance requires TP/SL as separate algo orders.
        Both use workingType=MARK_PRICE to prevent false triggers from wicks.
        The closing side is opposite to the position direction.

        NOTE on priceProtect: enabling priceProtect ("true") causes Binance to
        REJECT the SL if the mark price has already moved past the trigger
        between the time the order is signed and when it reaches the matching
        engine. In volatile markets, this means the SL "hits" the moment the
        order is received — the trigger price has already been crossed. We
        therefore disable priceProtect to keep the conditional order resting
        on the book. False-triggers from wicks are still mitigated by using
        MARK_PRICE (not last-trade) as the trigger reference.
        """
        tick_size = await self.get_tick_size(symbol)

        close_side = "SELL" if is_buy else "BUY"

        tp_placed = False
        sl_placed = False
        results: dict[str, Any] = {}

        async def _place_algo(order_type: str, trigger_price: float) -> dict:
            rounded = self._round_step(trigger_price, tick_size)
            algo_tag = "tp" if order_type == "TAKE_PROFIT_MARKET" else "sl"
            client_algo_id = (
                f"ex-{symbol.lower()[:10]}-{algo_tag}-{int(time.time() * 1000)}"
            )

            def _post():
                params: dict[str, Any] = {
                    "algoType": "CONDITIONAL",
                    "symbol": symbol,
                    "side": close_side,
                    "positionSide": position_side,
                    "type": order_type,
                    "workingType": "MARK_PRICE",
                    "triggerPrice": rounded,
                    "closePosition": "true",
                    "clientAlgoId": client_algo_id,
                }
                body = self._auth_params(api_secret, params)
                with httpx.Client(timeout=15.0) as client:
                    try:
                        r = client.post(
                            f"{self.base_url}/fapi/v1/algoOrder",
                            data=body,
                            headers=self._headers(api_key),
                        )
                        r.raise_for_status()
                        return {"success": True, "data": r.json()}
                    except httpx.HTTPStatusError as e:
                        err = e.response.json() if e.response.content else {}
                        return {
                            "success": False,
                            "error": err.get("msg", str(e)),
                            "code": err.get("code"),
                            "clientAlgoId": client_algo_id,
                        }
                    except Exception as e:
                        return {"success": False, "error": str(e)}

            return await asyncio.to_thread(_post)

        if take_profit_price is not None:
            tp_result = await _place_algo("TAKE_PROFIT_MARKET", take_profit_price)
            tp_placed = tp_result.get("success", False)
            results["tp"] = tp_result

        if stop_loss_price is not None:
            sl_result = await _place_algo("STOP_MARKET", stop_loss_price)
            sl_placed = sl_result.get("success", False)
            results["sl"] = sl_result

        success = (take_profit_price is None or tp_placed) and (
            stop_loss_price is None or sl_placed
        )
        if not success:
            cleanup: dict[str, Any] = {}
            for key in ("tp", "sl"):
                result = results.get(key)
                if not result or not result.get("success"):
                    continue
                payload = result.get("data", {})
                cleanup[f"cancel_{key}"] = await self.cancel_algo_order(
                    api_key,
                    api_secret,
                    algo_id=payload.get("algoId"),
                    client_algo_id=payload.get("clientAlgoId"),
                )
            if cleanup:
                results["cleanup"] = cleanup

        return {
            "success": success,
            "tp_placed": tp_placed,
            "sl_placed": sl_placed,
            "results": results,
        }

    async def close_position(
        self,
        api_key: str,
        api_secret: str,
        symbol: str,
        size: float,
        is_buy: bool,
        position_side: str = "BOTH",
    ) -> dict:
        """Close a position by placing an opposite-side market order with reduceOnly."""
        return await self.place_order(
            api_key=api_key,
            api_secret=api_secret,
            symbol=symbol,
            is_buy=not is_buy,
            size=size,
            order_type="market",
            reduce_only=True,
            position_side=position_side,
        )

    # ── Authenticated: closed PnL history ──────────────────────────

    async def get_user_trades(
        self,
        api_key: str,
        api_secret: str,
        symbol: str,
        *,
        order_id: int | None = None,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """GET /fapi/v1/userTrades â€” fetch recent trades for a symbol."""

        def _fetch():
            extra: dict[str, Any] = {"symbol": symbol, "limit": limit}
            if order_id is not None:
                extra["orderId"] = order_id

            now_ms = int(time.time() * 1000)
            if start_time_ms and now_ms - start_time_ms <= 7 * 24 * 60 * 60 * 1000:
                extra["startTime"] = start_time_ms
                if end_time_ms:
                    extra["endTime"] = end_time_ms

            params = self._auth_params(api_secret, extra)
            with httpx.Client(timeout=10.0) as client:
                r = client.get(
                    f"{self.base_url}/fapi/v1/userTrades",
                    params=params,
                    headers=self._headers(api_key),
                )
                r.raise_for_status()
                return r.json()

        return await asyncio.to_thread(_fetch)

    @classmethod
    def _summarize_close_trade_group(cls, trades: list[dict[str, Any]]) -> dict | None:
        if not trades:
            return None

        total_qty = 0.0
        total_quote = 0.0
        total_pnl = 0.0
        latest_time = 0
        latest_order_id = None

        for trade in trades:
            qty = float(trade.get("qty", 0) or 0)
            price = float(trade.get("price", 0) or 0)
            quote_qty = float(trade.get("quoteQty", 0) or 0)
            total_qty += qty
            total_quote += quote_qty if quote_qty > 0 else price * qty
            total_pnl += float(trade.get("realizedPnl", 0) or 0)
            trade_time = int(trade.get("time", 0) or 0)
            if trade_time >= latest_time:
                latest_time = trade_time
                latest_order_id = trade.get("orderId")

        if total_qty <= 0:
            return None

        return {
            "pnl": total_pnl,
            "exit_price": total_quote / total_qty if total_quote > 0 else 0.0,
            "entry_price": 0.0,
            "close_hash": str(latest_order_id or ""),
            "closed_at": (
                datetime.fromtimestamp(latest_time / 1000, tz=timezone.utc)
                if latest_time
                else None
            ),
            "quantity": total_qty,
            "raw_trades": trades,
        }

    async def get_close_trade_summary(
        self,
        api_key: str,
        api_secret: str,
        symbol: str,
        *,
        start_time_ms: int | None = None,
        expected_close_side: str | None = None,
        expected_position_side: str | None = None,
        entry_order_id: str | None = None,
        expected_quantity: float | None = None,
        limit: int = 100,
    ) -> dict | None:
        """Summarize the close trade group for a recently closed position."""

        try:
            trades = await self.get_user_trades(
                api_key,
                api_secret,
                symbol,
                start_time_ms=start_time_ms,
                limit=limit,
            )
        except Exception as e:
            logger.warning("Failed to fetch Binance userTrades for %s: %s", symbol, e)
            return None

        groups: dict[int, list[dict[str, Any]]] = {}
        for trade in trades:
            trade_time = int(trade.get("time", 0) or 0)
            if start_time_ms and trade_time and trade_time < start_time_ms:
                continue
            if expected_close_side and trade.get("side") != expected_close_side:
                continue
            if (
                expected_position_side
                and trade.get("positionSide")
                and trade.get("positionSide") != expected_position_side
            ):
                continue
            order_id = trade.get("orderId")
            if entry_order_id is not None and str(order_id) == str(entry_order_id):
                continue
            if order_id is None:
                continue
            groups.setdefault(int(order_id), []).append(trade)

        if not groups:
            return None

        target_qty = float(expected_quantity or 0)
        best_score = -1
        best_summary: dict | None = None
        for grouped_trades in groups.values():
            summary = self._summarize_close_trade_group(grouped_trades)
            if summary is None:
                continue
            latest_time = max(int(t.get("time", 0) or 0) for t in grouped_trades)
            qty_ratio = (
                min(summary["quantity"] / target_qty, 1.0) if target_qty > 0 else 0.0
            )
            realized_flag = any(
                abs(float(t.get("realizedPnl", 0) or 0)) > 1e-12 for t in grouped_trades
            )
            score = latest_time
            if realized_flag:
                score += 10_000_000_000_000
            score += int(qty_ratio * 1_000_000_000_000)

            if score > best_score:
                best_score = score
                best_summary = summary

        return best_summary

    async def get_closed_pnl(
        self,
        api_key: str,
        api_secret: str,
        symbol: str,
        start_time_ms: int | None = None,
        limit: int = 20,
    ) -> dict | None:
        """GET /fapi/v1/income?incomeType=REALIZED_PNL — find close record for symbol."""

        def _fetch():
            extra: dict[str, Any] = {
                "incomeType": "REALIZED_PNL",
                "symbol": symbol,
                "limit": limit,
            }
            if start_time_ms:
                extra["startTime"] = start_time_ms
            params = self._auth_params(api_secret, extra)
            with httpx.Client(timeout=10.0) as client:
                r = client.get(
                    f"{self.base_url}/fapi/v1/income",
                    params=params,
                    headers=self._headers(api_key),
                )
                r.raise_for_status()
                return r.json()

        try:
            records = await asyncio.to_thread(_fetch)
            if not records:
                return None

            # Pick the most recent qualifying record
            best: dict | None = None
            best_time = 0
            for rec in records:
                t = int(rec.get("time", 0))
                if start_time_ms and t < start_time_ms:
                    continue
                if t > best_time:
                    best_time = t
                    best = rec

            if not best:
                return None

            return {
                "pnl": float(best.get("income", 0)),
                "exit_price": 0.0,  # income record doesn't carry exit price
                "entry_price": 0.0,
                "symbol": best.get("symbol", symbol),
                "close_hash": str(best.get("tranId", "")),
                "closed_at": (
                    datetime.fromtimestamp(best_time / 1000, tz=timezone.utc)
                    if best_time
                    else None
                ),
            }
        except Exception as e:
            logger.warning("Failed to get Binance income history for %s: %s", symbol, e)
            return None

    # ── Validation ─────────────────────────────────────────────────

    async def validate_api_keys(
        self, api_key: str, api_secret: str
    ) -> tuple[bool, str]:
        """Validate Binance Futures API keys by fetching balance."""
        try:
            balances = await self.get_balance(api_key, api_secret)
            if isinstance(balances, list):
                usdt = next((b for b in balances if b.get("asset") == "USDT"), None)
                equity = float(usdt.get("balance", 0)) if usdt else 0.0
                return True, f"Valid. USDT balance: ${equity:.2f}"
            return False, "Unexpected response from Binance"
        except httpx.HTTPStatusError as e:
            return False, self._format_api_error(e.response)
        except Exception as e:
            err = str(e)
            if "-2014" in err or "-1100" in err:
                return False, "Invalid API key format"
            return False, f"Validation failed: {err}"
