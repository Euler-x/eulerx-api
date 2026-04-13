import asyncio
import logging
import math
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class BybitService:
    """Bybit V5 API service for linear perpetual futures."""

    def __init__(self, api_key: str = "", api_secret: str = "", testnet: bool = False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet or settings.bybit_testnet
        self.base_url = (
            "https://api-testnet.bybit.com" if self.testnet else "https://api.bybit.com"
        )

    def _get_session(self, api_key: str | None = None, api_secret: str | None = None):
        """Create a pybit HTTP session. Use user-specific keys if provided."""
        from pybit.unified_trading import HTTP

        return HTTP(
            testnet=self.testnet,
            api_key=api_key or self.api_key,
            api_secret=api_secret or self.api_secret,
        )

    # ── Market Data (public, no auth) ──────────────────────────────

    async def get_all_tickers(self) -> list[dict]:
        """Fetch all linear perpetual tickers."""

        def _fetch():
            session = self._get_session()
            resp = session.get_tickers(category="linear")
            if resp["retCode"] != 0:
                raise Exception(f"Bybit API error: {resp['retMsg']}")
            return resp["result"]["list"]

        return await asyncio.to_thread(_fetch)

    async def get_market_data(self) -> list[dict]:
        """Fetch all linear perp tickers and normalize to our format."""
        tickers = await self.get_all_tickers()
        symbols = []
        for t in tickers:
            symbol = t.get("symbol", "")
            # Only USDT perpetuals
            if not symbol.endswith("USDT"):
                continue
            try:
                mid_price = (
                    float(t.get("bid1Price", 0)) + float(t.get("ask1Price", 0))
                ) / 2
                if mid_price <= 0:
                    mid_price = float(t.get("lastPrice", 0))
                if mid_price <= 0:
                    continue
                symbols.append(
                    {
                        "symbol": symbol,  # e.g. "BTCUSDT"
                        "name": symbol.replace("USDT", ""),  # e.g. "BTC"
                        "mid_price": mid_price,
                        "last_price": float(t.get("lastPrice", 0)),
                        "price_change_24h_pct": float(t.get("price24hPcnt", 0)) * 100,
                        "high_24h": float(t.get("highPrice24h", 0)),
                        "low_24h": float(t.get("lowPrice24h", 0)),
                        "volume_24h": float(t.get("volume24h", 0)),
                        "turnover_24h": float(t.get("turnover24h", 0)),
                        "funding_rate": float(t.get("fundingRate", 0)),
                        "open_interest": float(t.get("openInterest", 0)),
                        "exchange": "bybit",
                    }
                )
            except (ValueError, TypeError):
                continue
        return symbols

    async def get_ticker(self, symbol: str) -> dict | None:
        """Fetch single ticker for a symbol."""

        def _fetch():
            session = self._get_session()
            resp = session.get_tickers(category="linear", symbol=symbol)
            if resp["retCode"] != 0:
                return None
            tickers = resp["result"]["list"]
            return tickers[0] if tickers else None

        return await asyncio.to_thread(_fetch)

    async def get_candles(
        self, symbol: str, interval: str = "60", limit: int = 24
    ) -> list[dict]:
        """Fetch kline data. Bybit intervals: 1,3,5,15,30,60,120,240,360,720,D,W,M"""

        def _fetch():
            import time

            session = self._get_session()
            for attempt in range(3):
                try:
                    resp = session.get_kline(
                        category="linear",
                        symbol=symbol,
                        interval=interval,
                        limit=limit,
                    )
                    if resp["retCode"] != 0:
                        raise Exception(f"Bybit kline error: {resp['retMsg']}")
                    # Bybit returns newest first, reverse for chronological order
                    raw = resp["result"]["list"]
                    raw.reverse()
                    candles = []
                    for c in raw:
                        candles.append(
                            {
                                "t": int(c[0]),
                                "o": c[1],
                                "h": c[2],
                                "l": c[3],
                                "c": c[4],
                                "v": c[5],
                            }
                        )
                    return candles
                except Exception as e:
                    err = str(e)
                    if "limit-reset" in err or "429" in err or "Too Many" in err:
                        wait = 1.0 * (attempt + 1)
                        time.sleep(wait)
                        continue
                    raise
            return []

        return await asyncio.to_thread(_fetch)

    async def get_top_movers(self, limit: int = 20) -> list[dict]:
        """Get balanced top movers from Bybit and enrich with candle data.

        IMPORTANT: Pre-select candidates from ticker data FIRST, then only
        enrich the shortlist with candles. This avoids 2000+ API calls.
        """
        try:
            market_data = await self.get_market_data()
            if not market_data:
                return []

            # Filter: must have volume > $100K turnover
            candidates = [s for s in market_data if s.get("turnover_24h", 0) > 100_000]

            def _ticker_activity(sym: dict) -> float:
                change = abs(sym.get("price_change_24h_pct", 0))
                vol = sym.get("turnover_24h", 0)
                return change * math.log1p(vol)

            bucket_size = max(limit // 3, 1)

            # Pre-select from ticker data (no candle fetch needed)
            gainers = sorted(
                candidates,
                key=lambda s: s.get("price_change_24h_pct", 0),
                reverse=True,
            )[:bucket_size]

            losers = sorted(
                candidates,
                key=lambda s: s.get("price_change_24h_pct", 0),
            )[:bucket_size]

            by_activity = sorted(candidates, key=_ticker_activity, reverse=True)

            seen = set()
            shortlist: list[dict] = []
            for sym in gainers + losers + by_activity:
                name = sym.get("symbol", "")
                if name not in seen:
                    seen.add(name)
                    shortlist.append(sym)
                if len(shortlist) >= limit:
                    break

            # NOW enrich only the shortlist with candle data (limit × 4 TF calls)
            enriched = await self.enrich_with_candles(shortlist)
            enriched = [s for s in enriched if s.get("candle_summary")]

            # Add regime tags
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
                "Bybit top movers: %d symbols selected from %d total",
                len(enriched),
                len(market_data),
            )
            return enriched
        except Exception as e:
            logger.error("Failed to fetch Bybit top movers: %s", e)
            return []

    # Keep same alias
    async def get_top_gainers(self, limit: int = 20) -> list[dict]:
        return await self.get_top_movers(limit=limit)

    async def enrich_with_candles(self, symbols: list[dict]) -> list[dict]:
        """Add candle summary to each symbol — same output format as HyperliquidService."""

        async def _enrich_one(sym: dict) -> None:
            symbol = sym.get("symbol", "")
            if not symbol:
                return
            try:
                candles_1h, candles_4h, candles_15m, candles_5m = await asyncio.gather(
                    self.get_candles(symbol, interval="60", limit=24),
                    self.get_candles(symbol, interval="240", limit=18),
                    self.get_candles(symbol, interval="15", limit=24),
                    self.get_candles(symbol, interval="5", limit=36),
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

                # Recent trend
                recent_closes = closes[-6:]
                trend_up = sum(
                    1
                    for i in range(1, len(recent_closes))
                    if recent_closes[i] > recent_closes[i - 1]
                )
                trend_direction = (
                    "up" if trend_up >= 4 else "down" if trend_up <= 1 else "mixed"
                )

                # RSI (14-period)
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

                # Support/Resistance
                swing_highs = []
                swing_lows = []
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

                # 4h trend
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
                    if hh >= 3 and hl >= 3:
                        htf_trend = "bullish"
                    elif hh <= 1 and hl <= 1:
                        htf_trend = "bearish"
                    else:
                        htf_trend = "ranging"
                    # 4h RSI
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

                # ATR
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

                # Exhaustion indicators
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
                upper_wick_ratio = 0
                lower_wick_ratio = 0
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
                    if close_latest > bb_upper:
                        bb_position = "above_upper"
                    elif close_latest < bb_lower:
                        bb_position = "below_lower"
                    else:
                        bb_position = "middle"
                else:
                    bb_position = "unknown"

                # ── 15m + 5m lower TF analysis ─────────────────────
                def _compute_rsi(closes_list, period=14):
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
                    # Support/resistance from 15m swings
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
                    # Trend
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
                    # 15m entry zone
                    "ltf_15m_support": ltf_15m_support,
                    "ltf_15m_resistance": ltf_15m_resistance,
                    "ltf_15m_trend": ltf_15m_trend,
                    "ltf_15m_rsi": ltf_15m_rsi,
                    # 5m entry trigger
                    "ltf_5m_trend": ltf_5m_trend,
                    "ltf_5m_rsi": ltf_5m_rsi,
                    "ltf_5m_candle_pattern": ltf_5m_pattern,
                    "ltf_5m_atr_pct": ltf_5m_atr_pct,
                }
            except Exception as e:
                logger.warning("Failed to fetch Bybit candles for %s: %s", symbol, e)

        # Batch in groups of 2 with delay — each symbol needs 4 TF calls
        # Bybit rate limit: ~120 req/5s, so 2 symbols × 4 TFs = 8 per batch
        batch_size = 2
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i : i + batch_size]
            await asyncio.gather(*[_enrich_one(sym) for sym in batch])
            if i + batch_size < len(symbols):
                await asyncio.sleep(1.0)
        return symbols

    # ── Authenticated Endpoints (user-specific keys) ───────────────

    async def get_user_state(self, api_key: str, api_secret: str) -> dict:
        """Fetch wallet balance for authenticated user."""

        def _fetch():
            session = self._get_session(api_key, api_secret)
            resp = session.get_wallet_balance(accountType="UNIFIED")
            if resp["retCode"] != 0:
                raise Exception(f"Bybit wallet error: {resp['retMsg']}")
            accounts = resp["result"]["list"]
            if not accounts:
                return {}
            return accounts[0]

        return await asyncio.to_thread(_fetch)

    async def get_account_value(self, api_key: str, api_secret: str) -> float:
        """Get total equity from Bybit unified account."""
        try:
            state = await self.get_user_state(api_key, api_secret)
            return float(state.get("totalEquity", 0))
        except Exception as e:
            logger.error("Failed to get Bybit account value: %s", e)
            return 0.0

    async def get_available_balance(self, api_key: str, api_secret: str) -> float:
        """Get available balance for trading."""
        try:
            state = await self.get_user_state(api_key, api_secret)
            return float(state.get("totalAvailableBalance", 0))
        except Exception as e:
            logger.error("Failed to get Bybit available balance: %s", e)
            return 0.0

    async def get_user_positions(
        self, api_key: str, api_secret: str
    ) -> dict[str, dict]:
        """Return {symbol: position_info} for non-zero positions."""

        def _fetch():
            session = self._get_session(api_key, api_secret)
            resp = session.get_positions(category="linear", settleCoin="USDT")
            if resp["retCode"] != 0:
                raise Exception(f"Bybit positions error: {resp['retMsg']}")
            positions = {}
            for p in resp["result"]["list"]:
                size = float(p.get("size", 0))
                if size != 0:
                    positions[p["symbol"]] = {
                        "size": size if p.get("side") == "Buy" else -size,
                        "entry_px": float(p.get("avgPrice", 0)),
                        "unrealized_pnl": float(p.get("unrealisedPnl", 0)),
                        "side": p.get("side"),
                        "mark_price": float(p.get("markPrice", 0)),
                        "leverage": float(p.get("leverage", 0)),
                        "liq_price": float(p.get("liqPrice", 0))
                        if p.get("liqPrice")
                        else None,
                        "position_value": float(p.get("positionValue", 0)),
                        "margin_used": float(p.get("positionIM", 0)),
                    }
            return positions

        return await asyncio.to_thread(_fetch)

    async def get_closed_pnl(
        self, api_key: str, api_secret: str, symbol: str
    ) -> dict | None:
        """Get the most recent closed PnL record for a symbol."""

        def _fetch():
            session = self._get_session(api_key, api_secret)
            resp = session.get_closed_pnl(category="linear", symbol=symbol, limit=1)
            if resp["retCode"] != 0:
                return None
            records = resp["result"]["list"]
            if not records:
                return None
            r = records[0]
            return {
                "pnl": float(r.get("closedPnl", 0)),
                "exit_price": float(r.get("avgExitPrice", 0)),
                "entry_price": float(r.get("avgEntryPrice", 0)),
                "symbol": r.get("symbol", ""),
            }

        return await asyncio.to_thread(_fetch)

    async def update_leverage(
        self, api_key: str, api_secret: str, symbol: str, leverage: int
    ) -> dict:
        """Set leverage for a symbol on Bybit."""

        def _set():
            session = self._get_session(api_key, api_secret)
            try:
                resp = session.set_leverage(
                    category="linear",
                    symbol=symbol,
                    buyLeverage=str(leverage),
                    sellLeverage=str(leverage),
                )
                # retCode 110043 means leverage not modified (already set)
                if resp["retCode"] == 0 or resp["retCode"] == 110043:
                    return {"success": True, "result": resp}
                return {"success": False, "error": resp["retMsg"]}
            except Exception as e:
                return {"success": False, "error": str(e)}

        return await asyncio.to_thread(_set)

    @staticmethod
    def round_to_tick(price: float, tick_size: float) -> float:
        """Round a price to the nearest valid tick size.

        Bybit rejects TP/SL prices that aren't exact multiples of the
        instrument's tickSize (e.g. 0.10 for BTCUSDT).
        """
        if tick_size <= 0:
            return price
        # Determine decimal precision from tick_size itself
        tick_str = f"{tick_size:.10f}".rstrip("0")
        decimals = len(tick_str.split(".")[-1]) if "." in tick_str else 0
        rounded = round(round(price / tick_size) * tick_size, decimals)
        return rounded

    async def get_tick_size(self, symbol: str) -> float:
        """Get the price tick size for a symbol (for TP/SL rounding)."""
        try:
            info = await self.get_instrument_info(symbol)
            price_filter = info.get("priceFilter", {})
            return float(price_filter.get("tickSize", "0.01"))
        except Exception:
            return 0.01

    async def place_order(
        self,
        api_key: str,
        api_secret: str,
        symbol: str,
        is_buy: bool,
        size: float,
        price: float | None = None,
        order_type: str = "market",
        reduce_only: bool = False,
        take_profit: float | None = None,
        stop_loss: float | None = None,
    ) -> dict:
        """Place an order on Bybit."""

        def _place():
            session = self._get_session(api_key, api_secret)
            params: dict[str, Any] = {
                "category": "linear",
                "symbol": symbol,
                "side": "Buy" if is_buy else "Sell",
                "orderType": "Limit" if order_type == "limit" else "Market",
                "qty": str(size),
                "positionIdx": 0,  # one-way mode
            }
            if order_type == "limit" and price:
                params["price"] = str(price)
                params["timeInForce"] = "GTC"
            if reduce_only:
                params["reduceOnly"] = True
            if take_profit is not None:
                params["takeProfit"] = str(take_profit)
            if stop_loss is not None:
                params["stopLoss"] = str(stop_loss)

            try:
                resp = session.place_order(**params)
                if resp["retCode"] == 0:
                    return {
                        "success": True,
                        "data": resp["result"],
                        "tx_hash": resp["result"].get("orderId", ""),
                        "oid": resp["result"].get("orderId"),
                    }
                return {"success": False, "error": resp["retMsg"]}
            except Exception as e:
                return {"success": False, "error": str(e)}

        return await asyncio.to_thread(_place)

    async def place_tp_sl_orders(
        self,
        api_key: str,
        api_secret: str,
        symbol: str,
        is_buy: bool,
        take_profit_price: float | None = None,
        stop_loss_price: float | None = None,
    ) -> dict:
        """Set TP/SL on an existing position via trading-stop endpoint.

        Prices are rounded to the instrument's tick size before submission.
        The `size` parameter was removed — Bybit's Full tpslMode applies
        TP/SL to the entire position automatically.
        """
        # Round prices to tick size to prevent Bybit rejection
        tick_size = await self.get_tick_size(symbol)
        if take_profit_price is not None:
            take_profit_price = self.round_to_tick(take_profit_price, tick_size)
        if stop_loss_price is not None:
            stop_loss_price = self.round_to_tick(stop_loss_price, tick_size)

        def _set():
            session = self._get_session(api_key, api_secret)
            params: dict[str, Any] = {
                "category": "linear",
                "symbol": symbol,
                "tpslMode": "Full",
                "positionIdx": 0,
            }
            if take_profit_price is not None:
                params["takeProfit"] = str(take_profit_price)
            if stop_loss_price is not None:
                params["stopLoss"] = str(stop_loss_price)

            try:
                resp = session.set_trading_stop(**params)
                if resp["retCode"] == 0:
                    return {
                        "success": True,
                        "tp_placed": take_profit_price is not None,
                        "sl_placed": stop_loss_price is not None,
                        "results": resp["result"],
                    }
                return {
                    "success": False,
                    "tp_placed": False,
                    "sl_placed": False,
                    "error": resp["retMsg"],
                }
            except Exception as e:
                return {
                    "success": False,
                    "tp_placed": False,
                    "sl_placed": False,
                    "error": str(e),
                }

        return await asyncio.to_thread(_set)

    async def close_position(
        self,
        api_key: str,
        api_secret: str,
        symbol: str,
        size: float,
        is_buy: bool,
    ) -> dict:
        """Close a position by placing an opposite market order."""
        return await self.place_order(
            api_key=api_key,
            api_secret=api_secret,
            symbol=symbol,
            is_buy=not is_buy,
            size=size,
            order_type="market",
            reduce_only=True,
        )

    async def validate_api_keys(
        self, api_key: str, api_secret: str
    ) -> tuple[bool, str]:
        """Validate Bybit API keys by attempting to fetch wallet balance."""
        try:
            state = await self.get_user_state(api_key, api_secret)
            if state:
                equity = float(state.get("totalEquity", 0))
                return True, f"Valid. Account equity: ${equity:.2f}"
            return False, "No account data returned"
        except Exception as e:
            error_msg = str(e)
            if "10003" in error_msg or "10004" in error_msg:
                return False, "Invalid API key or secret"
            if "33004" in error_msg:
                return False, "API key does not have trading permissions"
            return False, f"Validation failed: {error_msg}"

    async def get_instrument_info(self, symbol: str) -> dict:
        """Get instrument details (min qty, tick size, etc.) for position sizing."""

        def _fetch():
            session = self._get_session()
            resp = session.get_instruments_info(category="linear", symbol=symbol)
            if resp["retCode"] != 0:
                return {}
            instruments = resp["result"]["list"]
            return instruments[0] if instruments else {}

        return await asyncio.to_thread(_fetch)

    async def get_qty_step(self, symbol: str) -> float:
        """Get the minimum quantity step for a symbol (for rounding)."""
        try:
            info = await self.get_instrument_info(symbol)
            lot_filter = info.get("lotSizeFilter", {})
            return float(lot_filter.get("qtyStep", "0.001"))
        except Exception:
            return 0.001
