import logging
import time
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class HyperliquidService:
    def __init__(self):
        self.base_url = settings.hyperliquid_api_url
        self.is_testnet = settings.hyperliquid_testnet

    async def _post(self, endpoint: str, payload: dict) -> Any:
        url = f"{self.base_url}{endpoint}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return response.json()

    async def _get_fill_tx_hash(self, user_address: str, oid: int) -> str | None:
        """Look up the on-chain transaction hash for a filled order.

        The HL SDK only returns an order ID (oid). The actual tx hash
        is available from the userFills endpoint.
        """
        try:
            fills = await self._post(
                "/info",
                {"type": "userFills", "user": user_address},
            )
            for fill in reversed(fills):
                if fill.get("oid") == oid:
                    return fill.get("hash")
        except Exception as e:
            logger.warning("Failed to look up tx hash for oid %s: %s", oid, e)
        return None

    async def get_user_fills(self, wallet_address: str, limit: int = 200) -> list[dict]:
        """Fetch recent fills for a user."""
        try:
            data = await self._post(
                "/info",
                {"type": "userFills", "user": wallet_address},
            )
            return data[-limit:] if len(data) > limit else data
        except Exception as e:
            logger.error("Failed to fetch user fills for %s: %s", wallet_address, e)
            return []

    async def get_user_positions(self, wallet_address: str) -> dict[str, dict]:
        """Return {symbol: position_info} for non-zero positions."""
        try:
            state = await self.get_user_state(wallet_address)
            positions = {}
            for p in state.get("assetPositions", []):
                pos = p.get("position", {})
                sz = float(pos.get("szi", 0))
                if sz != 0:
                    positions[pos.get("coin", "")] = {
                        "size": sz,
                        "entry_px": float(pos.get("entryPx", 0)),
                        "unrealized_pnl": float(pos.get("unrealizedPnl", 0)),
                    }
            return positions
        except Exception as e:
            logger.error("Failed to fetch positions for %s: %s", wallet_address, e)
            return {}

    async def get_all_mids(self) -> dict[str, str]:
        data = await self._post("/info", {"type": "allMids"})
        return data

    async def get_meta(self) -> dict:
        data = await self._post("/info", {"type": "meta"})
        return data

    async def get_market_data(self) -> list[dict]:
        meta = await self.get_meta()
        mids = await self.get_all_mids()

        symbols = []
        for asset_info in meta.get("universe", []):
            symbol = asset_info.get("name", "")
            mid_price = mids.get(symbol)
            if mid_price:
                symbols.append(
                    {
                        "symbol": symbol,
                        "name": asset_info.get("name", ""),
                        "szDecimals": asset_info.get("szDecimals", 0),
                        "mid_price": float(mid_price),
                    }
                )
        return symbols

    async def get_candles(
        self, coin: str, interval: str = "1h", hours: int = 24
    ) -> list[dict]:
        """Fetch OHLCV candle data for a coin."""
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - (hours * 3600 * 1000)
        data = await self._post(
            "/info",
            {
                "type": "candleSnapshot",
                "req": {
                    "coin": coin,
                    "interval": interval,
                    "startTime": start_ms,
                    "endTime": end_ms,
                },
            },
        )
        return data

    async def get_meta_and_asset_ctxs(self) -> tuple[dict, list[dict]]:
        """Fetch metaAndAssetCtxs for funding rates and open interest."""
        data = await self._post("/info", {"type": "metaAndAssetCtxs"})
        meta = data[0] if len(data) > 0 else {}
        ctxs = data[1] if len(data) > 1 else []
        return meta, ctxs

    async def get_top_movers(self, limit: int = 20) -> list[dict]:
        """Fetch market data, enrich with candles + funding, and return a
        BALANCED mix of movers: top gainers, top losers, and high-volume
        symbols — so the AI sees both long and short opportunities.
        """
        try:
            market_data = await self.get_market_data()
            if not market_data:
                return []

            # Fetch funding rates
            _, asset_ctxs = await self.get_meta_and_asset_ctxs()
            funding_map: dict[str, float] = {}
            meta = await self.get_meta()
            universe = meta.get("universe", [])
            for i, ctx in enumerate(asset_ctxs):
                if i < len(universe):
                    sym_name = universe[i].get("name", "")
                    try:
                        funding_map[sym_name] = float(ctx.get("funding", "0"))
                    except (ValueError, TypeError):
                        pass

            # Attach funding to market data
            for sym in market_data:
                sym["funding_rate"] = funding_map.get(sym.get("symbol", ""), 0.0)

            # Enrich with candle data
            enriched = await self.enrich_with_candles(market_data)

            # Filter out symbols with no candle data
            enriched = [s for s in enriched if s.get("candle_summary")]

            import math

            def _activity_score(sym: dict) -> float:
                candle = sym.get("candle_summary", {})
                change = abs(candle.get("price_change_24h_pct", 0))
                volume = candle.get("total_volume_24h", 0)
                volatility = abs(candle.get("volatility_pct", 0))
                return (change + volatility) * math.log1p(volume)

            # Split into balanced buckets
            bucket_size = max(limit // 3, 1)

            # Bucket 1: Top gainers (potential SHORT / mean-reversion candidates)
            gainers = sorted(
                enriched,
                key=lambda s: s.get("candle_summary", {}).get(
                    "price_change_24h_pct", 0
                ),
                reverse=True,
            )[:bucket_size]

            # Bucket 2: Top losers (potential BUY / bounce candidates)
            losers = sorted(
                enriched,
                key=lambda s: s.get("candle_summary", {}).get(
                    "price_change_24h_pct", 0
                ),
            )[:bucket_size]

            # Bucket 3: Highest activity regardless of direction
            by_activity = sorted(enriched, key=_activity_score, reverse=True)

            # Merge, dedup, preserve order
            seen = set()
            merged: list[dict] = []
            for sym in gainers + losers + by_activity:
                name = sym.get("symbol", "")
                if name not in seen:
                    seen.add(name)
                    merged.append(sym)
                if len(merged) >= limit:
                    break

            # Add regime and exhaustion tags to each symbol
            for sym in merged:
                candle = sym.get("candle_summary", {})
                change = candle.get("price_change_24h_pct", 0)
                rsi = candle.get("rsi_14", 50)
                funding = sym.get("funding_rate", 0)

                # Regime classification
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
                candle["funding_rate"] = round(funding * 100, 4)  # as percentage

            logger.info(
                "Top movers: %d gainers, %d losers, %d total — regimes: %s",
                len(gainers),
                len(losers),
                len(merged),
                ", ".join(
                    f"{s.get('symbol')}={s.get('candle_summary', {}).get('regime', '?')}"
                    for s in merged[:5]
                ),
            )
            return merged
        except Exception as e:
            logger.error(f"Failed to fetch top movers: {e}")
            return []

    # Keep old name as alias for backwards compatibility
    async def get_top_gainers(self, limit: int = 20) -> list[dict]:
        return await self.get_top_movers(limit=limit)

    async def enrich_with_candles(self, symbols: list[dict]) -> list[dict]:
        """Add 24h candle summary to each symbol's market data.

        Fetches 1h candles (24h) and 4h candles (72h) for each symbol to
        provide both short-term and medium-term context to the AI engine.
        """
        import asyncio

        async def _enrich_one(sym: dict) -> None:
            coin = sym.get("symbol", "")
            if not coin:
                return
            try:
                # Fetch 4 timeframes: 4h (trend), 1h (confirmation), 15m (entry zone), 5m (trigger)
                candles_1h, candles_4h, candles_15m, candles_5m = await asyncio.gather(
                    self.get_candles(coin, interval="1h", hours=24),
                    self.get_candles(coin, interval="4h", hours=72),
                    self.get_candles(coin, interval="15m", hours=6),
                    self.get_candles(coin, interval="5m", hours=3),
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
                price_change_24h = ((close_latest - open_24h) / open_24h) * 100
                total_volume = sum(volumes)
                avg_volume = total_volume / len(volumes) if volumes else 0

                # Simple volatility: (high - low) / mid as percentage
                volatility = ((high_24h - low_24h) / close_latest) * 100

                # Recent trend: last 6 candles (1h)
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
                gains, losses = [], []
                for i in range(len(closes) - rsi_period, len(closes)):
                    diff = closes[i] - closes[i - 1]
                    gains.append(max(diff, 0))
                    losses.append(max(-diff, 0))
                avg_gain = sum(gains) / len(gains) if gains else 0
                avg_loss = sum(losses) / len(losses) if losses else 1
                rs = avg_gain / avg_loss if avg_loss > 0 else 100
                rsi = 100 - (100 / (1 + rs))

                # ── Support / Resistance from swing highs/lows ──────────
                def _find_key_levels(h: list, lo: list, c: list) -> dict:
                    """Identify support/resistance from swing points."""
                    swing_highs = []
                    swing_lows = []
                    for i in range(1, len(h) - 1):
                        if h[i] > h[i - 1] and h[i] > h[i + 1]:
                            swing_highs.append(h[i])
                        if lo[i] < lo[i - 1] and lo[i] < lo[i + 1]:
                            swing_lows.append(lo[i])

                    price = c[-1] if c else 0

                    # Nearest resistance: lowest swing high above current price
                    resistances = sorted([s for s in swing_highs if s > price])
                    nearest_resistance = resistances[0] if resistances else None

                    # Nearest support: highest swing low below current price
                    supports = sorted(
                        [s for s in swing_lows if s < price], reverse=True
                    )
                    nearest_support = supports[0] if supports else None

                    return {
                        "nearest_support": round(nearest_support, 4)
                        if nearest_support
                        else None,
                        "nearest_resistance": round(nearest_resistance, 4)
                        if nearest_resistance
                        else None,
                    }

                key_levels = _find_key_levels(highs, lows, closes)

                # ── Higher timeframe trend from 4h candles ──────────────
                htf_trend = "unknown"
                htf_rsi = None
                if candles_4h and len(candles_4h) >= 6:
                    htf_closes = [float(c["c"]) for c in candles_4h]
                    htf_highs = [float(c["h"]) for c in candles_4h]
                    htf_lows = [float(c["l"]) for c in candles_4h]

                    # Market structure: higher highs + higher lows = bullish
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

                # ── Average True Range (ATR) for volatility-based SL ────
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

                # ── Exhaustion indicators ──────────────────────────
                # SMA20 distance
                sma_period = min(20, len(closes))
                sma_20 = (
                    sum(closes[-sma_period:]) / sma_period
                    if sma_period > 0
                    else close_latest
                )
                price_vs_sma20_pct = (
                    ((close_latest - sma_20) / sma_20 * 100) if sma_20 > 0 else 0
                )

                # Volume trend: compare last 6h avg vs prior 6h avg
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

                # Wick rejection analysis (last 3 candles)
                upper_wick_ratio = 0
                lower_wick_ratio = 0
                wick_candles = min(3, len(candles_1h))
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

                # Bollinger Bands (20-period, 2 std dev)
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

                # ── 15m Entry Zone Analysis ────────────────────────
                ltf_15m_support = None
                ltf_15m_resistance = None
                ltf_15m_trend = "unknown"
                ltf_15m_rsi = None
                if candles_15m and len(candles_15m) >= 6:
                    m15_closes = [float(c["c"]) for c in candles_15m]
                    m15_highs = [float(c["h"]) for c in candles_15m]
                    m15_lows = [float(c["l"]) for c in candles_15m]

                    m15_levels = _find_key_levels(m15_highs, m15_lows, m15_closes)
                    ltf_15m_support = m15_levels["nearest_support"]
                    ltf_15m_resistance = m15_levels["nearest_resistance"]

                    # 15m trend structure
                    r_highs = m15_highs[-8:]
                    r_lows = m15_lows[-8:]
                    hh15 = sum(
                        1 for i in range(1, len(r_highs)) if r_highs[i] > r_highs[i - 1]
                    )
                    hl15 = sum(
                        1 for i in range(1, len(r_lows)) if r_lows[i] > r_lows[i - 1]
                    )
                    if hh15 >= 4 and hl15 >= 4:
                        ltf_15m_trend = "bullish"
                    elif hh15 <= 2 and hl15 <= 2:
                        ltf_15m_trend = "bearish"
                    else:
                        ltf_15m_trend = "ranging"

                    # 15m RSI
                    rsi_p15 = min(14, len(m15_closes) - 1)
                    if rsi_p15 >= 2:
                        g15, l15 = [], []
                        for i in range(len(m15_closes) - rsi_p15, len(m15_closes)):
                            d = m15_closes[i] - m15_closes[i - 1]
                            g15.append(max(d, 0))
                            l15.append(max(-d, 0))
                        ag15 = sum(g15) / len(g15) if g15 else 0
                        al15 = sum(l15) / len(l15) if l15 else 1
                        rs15 = ag15 / al15 if al15 > 0 else 100
                        ltf_15m_rsi = round(100 - (100 / (1 + rs15)), 1)

                # ── 5m Entry Trigger Analysis ─────────────────────
                ltf_5m_trend = "unknown"
                ltf_5m_rsi = None
                ltf_5m_last_3_candles = "neutral"
                ltf_5m_atr_pct = None
                if candles_5m and len(candles_5m) >= 6:
                    m5_closes = [float(c["c"]) for c in candles_5m]
                    m5_highs = [float(c["h"]) for c in candles_5m]
                    m5_lows = [float(c["l"]) for c in candles_5m]
                    m5_opens = [float(c["o"]) for c in candles_5m]

                    # 5m trend (last 12 candles = 1 hour)
                    rc5 = m5_closes[-12:] if len(m5_closes) >= 12 else m5_closes
                    up5 = sum(1 for i in range(1, len(rc5)) if rc5[i] > rc5[i - 1])
                    total5 = len(rc5) - 1
                    if total5 > 0 and up5 / total5 >= 0.65:
                        ltf_5m_trend = "bullish"
                    elif total5 > 0 and up5 / total5 <= 0.35:
                        ltf_5m_trend = "bearish"
                    else:
                        ltf_5m_trend = "mixed"

                    # 5m RSI
                    rsi_p5 = min(14, len(m5_closes) - 1)
                    if rsi_p5 >= 2:
                        g5, l5 = [], []
                        for i in range(len(m5_closes) - rsi_p5, len(m5_closes)):
                            d = m5_closes[i] - m5_closes[i - 1]
                            g5.append(max(d, 0))
                            l5.append(max(-d, 0))
                        ag5 = sum(g5) / len(g5) if g5 else 0
                        al5 = sum(l5) / len(l5) if l5 else 1
                        rs5 = ag5 / al5 if al5 > 0 else 100
                        ltf_5m_rsi = round(100 - (100 / (1 + rs5)), 1)

                    # Last 3 candles pattern (bullish/bearish engulfing-like)
                    if len(m5_closes) >= 3:
                        last3_bullish = sum(
                            1 for i in range(-3, 0) if m5_closes[i] > m5_opens[i]
                        )
                        if last3_bullish >= 2:
                            ltf_5m_last_3_candles = "bullish"
                        elif last3_bullish <= 0:
                            ltf_5m_last_3_candles = "bearish"

                    # 5m ATR for tight SL
                    m5_atr_vals = []
                    for i in range(1, len(candles_5m)):
                        tr5 = max(
                            m5_highs[i] - m5_lows[i],
                            abs(m5_highs[i] - m5_closes[i - 1]),
                            abs(m5_lows[i] - m5_closes[i - 1]),
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
                    "nearest_support": key_levels["nearest_support"],
                    "nearest_resistance": key_levels["nearest_resistance"],
                    "htf_trend_4h": htf_trend,
                    "htf_rsi_4h": htf_rsi,
                    "num_candles": len(candles_1h),
                    # Exhaustion indicators
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
                    "ltf_5m_candle_pattern": ltf_5m_last_3_candles,
                    "ltf_5m_atr_pct": ltf_5m_atr_pct,
                }
            except Exception as e:
                logger.warning(f"Failed to fetch candles for {coin}: {e}")

        # Batch in groups of 3 with a short delay to avoid HL 429 rate limits
        batch_size = 3
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i : i + batch_size]
            await asyncio.gather(*[_enrich_one(sym) for sym in batch])
            if i + batch_size < len(symbols):
                await asyncio.sleep(0.5)
        return symbols

    async def get_user_state(self, wallet_address: str) -> dict:
        data = await self._post(
            "/info",
            {
                "type": "clearinghouseState",
                "user": wallet_address,
            },
        )
        return data

    async def get_open_orders(self, wallet_address: str) -> list[dict]:
        data = await self._post(
            "/info",
            {
                "type": "openOrders",
                "user": wallet_address,
            },
        )
        return data

    async def update_leverage(
        self,
        wallet_private_key: str,
        symbol: str,
        leverage: int,
        account_address: str | None = None,
        is_cross: bool = True,
    ) -> dict:
        """Set leverage for a symbol on HyperLiquid before placing an order.

        Must be called before order placement to ensure the user's strategy
        leverage is applied on-chain.
        """
        try:
            from eth_account import Account
            from hyperliquid.exchange import Exchange
            from hyperliquid.utils import constants

            base_url = (
                constants.TESTNET_API_URL
                if self.is_testnet
                else constants.MAINNET_API_URL
            )

            key = (
                wallet_private_key
                if wallet_private_key.startswith("0x")
                else f"0x{wallet_private_key}"
            )
            wallet = Account.from_key(key)

            exchange = Exchange(
                wallet=wallet,
                base_url=base_url,
                account_address=account_address,
            )

            result = exchange.update_leverage(leverage, symbol, is_cross=is_cross)
            logger.info(
                "Set leverage for %s to %dx (cross=%s, account=%s): %s",
                symbol,
                leverage,
                is_cross,
                account_address or "direct",
                result,
            )
            return {"success": True, "result": result}
        except Exception as e:
            logger.error("Failed to set leverage for %s: %s", symbol, e)
            return {"success": False, "error": str(e)}

    async def place_order(
        self,
        wallet_private_key: str,
        symbol: str,
        is_buy: bool,
        size: float,
        account_address: str | None = None,
        price: float | None = None,
        order_type: str = "market",
        reduce_only: bool = False,
        slippage: float = 0.01,
    ) -> dict:
        """Place an order on Hyperliquid using the agent wallet pattern.

        Args:
            wallet_private_key: The agent/API wallet private key (for signing).
            symbol: Trading pair symbol (e.g. "ETH", "BTC").
            is_buy: True for buy, False for sell.
            size: Position size.
            account_address: The user's main Hyperliquid wallet address.
                If provided, trades on behalf of this account (agent wallet mode).
                If None, trades directly from the signing wallet (generated wallet mode).
            price: Limit price. If None, uses market price with slippage.
            order_type: "market" or "limit".
            reduce_only: If True, only reduces existing position.
            slippage: Slippage tolerance for market orders (default 1%).
        """
        try:
            from eth_account import Account
            from hyperliquid.exchange import Exchange
            from hyperliquid.info import Info
            from hyperliquid.utils import constants

            base_url = (
                constants.TESTNET_API_URL
                if self.is_testnet
                else constants.MAINNET_API_URL
            )

            # Create signing wallet from private key
            key = (
                wallet_private_key
                if wallet_private_key.startswith("0x")
                else f"0x{wallet_private_key}"
            )
            wallet = Account.from_key(key)

            info = Info(base_url, skip_ws=True)

            # Agent wallet mode: sign with agent key, trade on behalf of account_address
            # Generated wallet mode: sign and trade directly (no account_address)
            exchange = Exchange(
                wallet=wallet,
                base_url=base_url,
                account_address=account_address,
            )

            # Get current mid price for market orders
            if price is None:
                mids = info.all_mids()
                mid_price = float(mids.get(symbol, 0))
                if mid_price == 0:
                    return {"success": False, "error": f"No price found for {symbol}"}
                # Apply slippage for market orders
                if is_buy:
                    price = mid_price * (1 + slippage)
                else:
                    price = mid_price * (1 - slippage)

            # Round price to 5 significant figures (HyperLiquid requirement)
            if price > 0:
                from math import floor, log10

                sig_figs = 5
                magnitude = floor(log10(abs(price)))
                price = round(price, sig_figs - 1 - magnitude)

            logger.info(
                "Submitting order to HL: %s %s size=%s price=%s "
                "type=%s reduce_only=%s account=%s",
                "BUY" if is_buy else "SELL",
                symbol,
                size,
                price,
                order_type,
                reduce_only,
                account_address or "direct",
            )

            order_result = exchange.order(
                symbol,
                is_buy,
                size,
                price,
                {"limit": {"tif": "Ioc"}}
                if order_type == "market"
                else {"limit": {"tif": "Gtc"}},
                reduce_only=reduce_only,
            )

            # Check for top-level error (response is a string, not dict)
            if order_result.get("status") == "err":
                err_msg = order_result.get("response", "Unknown error")
                logger.error("HyperLiquid order error: %s", err_msg)
                return {"success": False, "error": str(err_msg)}

            # Extract order ID from response
            oid = None
            response = order_result.get("response", {})
            if isinstance(response, dict):
                statuses = response.get("data", {}).get("statuses", [])
                if statuses:
                    first = statuses[0]
                    if isinstance(first, dict):
                        oid = first.get("resting", {}).get("oid") or first.get(
                            "filled", {}
                        ).get("oid")
                        if "error" in first:
                            logger.error(
                                "HyperLiquid order rejected: %s",
                                first["error"],
                            )
                            return {
                                "success": False,
                                "error": first["error"],
                            }
                    elif isinstance(first, str):
                        logger.error("HyperLiquid order rejected: %s", first)
                        return {"success": False, "error": first}

            # Look up the real on-chain tx hash from user fills
            tx_hash = None
            if oid and account_address:
                tx_hash = await self._get_fill_tx_hash(account_address, oid)
            elif oid:
                signer_address = wallet.address
                tx_hash = await self._get_fill_tx_hash(signer_address, oid)

            return {
                "success": True,
                "data": order_result,
                "tx_hash": tx_hash,
                "oid": oid,
            }

        except ImportError:
            logger.warning(
                "Hyperliquid SDK not fully configured. Returning mock response."
            )
            return {
                "success": False,
                "error": "Hyperliquid SDK not configured for live trading",
                "mock": True,
            }
        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            return {"success": False, "error": str(e)}

    async def place_tp_sl_orders(
        self,
        wallet_private_key: str,
        symbol: str,
        size: float,
        is_buy: bool,
        take_profit_price: float | None = None,
        stop_loss_price: float | None = None,
        account_address: str | None = None,
    ) -> dict:
        """Place native TP and SL trigger orders on HyperLiquid.

        These are reduce-only orders that fire when the trigger price is hit.
        For a BUY position: TP sells when price rises, SL sells when price drops.
        For a SELL position: TP buys when price drops, SL buys when price rises.

        HyperLiquid trigger order params:
        - triggerPx: the price at which the order activates
        - isMarket: True = fill at market once triggered
        - tpsl: "tp" or "sl" — tells HL which direction to watch
        - price: for isMarket triggers, HL ignores this but requires a value;
                 we pass the triggerPx as a placeholder
        """
        try:
            from eth_account import Account
            from hyperliquid.exchange import Exchange
            from hyperliquid.utils import constants

            base_url = (
                constants.TESTNET_API_URL
                if self.is_testnet
                else constants.MAINNET_API_URL
            )

            key = (
                wallet_private_key
                if wallet_private_key.startswith("0x")
                else f"0x{wallet_private_key}"
            )
            wallet = Account.from_key(key)

            exchange = Exchange(
                wallet=wallet,
                base_url=base_url,
                account_address=account_address,
            )

            # TP/SL orders close the position (opposite direction, reduce_only)
            close_is_buy = not is_buy
            results = {"tp": None, "sl": None}
            tp_ok = False
            sl_ok = False

            # Round trigger prices to 5 significant figures (HL requirement)
            from math import floor, log10

            def _round_price(p: float) -> float:
                if p <= 0:
                    return p
                sig_figs = 5
                magnitude = floor(log10(abs(p)))
                return round(p, sig_figs - 1 - magnitude)

            # ── Place Take Profit ──────────────────────────────────────
            if take_profit_price:
                tp_trigger = _round_price(take_profit_price)
                for attempt in range(2):
                    try:
                        tp_result = exchange.order(
                            symbol,
                            close_is_buy,
                            size,
                            tp_trigger,
                            {
                                "trigger": {
                                    "triggerPx": tp_trigger,
                                    "isMarket": True,
                                    "tpsl": "tp",
                                }
                            },
                            reduce_only=True,
                        )

                        # Check for HL-level errors in the response
                        tp_status = tp_result.get("status")
                        if tp_status == "err":
                            err = tp_result.get("response", "Unknown")
                            logger.error(
                                "TP order rejected by HL for %s (attempt %d): %s",
                                symbol,
                                attempt + 1,
                                err,
                            )
                            results["tp"] = {"error": str(err)}
                            continue

                        # Check for per-order error in statuses
                        statuses = (
                            tp_result.get("response", {})
                            .get("data", {})
                            .get("statuses", [])
                        )
                        if (
                            statuses
                            and isinstance(statuses[0], dict)
                            and "error" in statuses[0]
                        ):
                            err = statuses[0]["error"]
                            logger.error(
                                "TP order error for %s (attempt %d): %s",
                                symbol,
                                attempt + 1,
                                err,
                            )
                            results["tp"] = {"error": str(err)}
                            continue

                        results["tp"] = tp_result
                        tp_ok = True
                        logger.info(
                            "TP order placed: %s %s size=%s trigger=$%s",
                            "BUY" if close_is_buy else "SELL",
                            symbol,
                            size,
                            tp_trigger,
                        )
                        break
                    except Exception as e:
                        logger.error(
                            "Failed to place TP order for %s (attempt %d): %s",
                            symbol,
                            attempt + 1,
                            e,
                        )
                        results["tp"] = {"error": str(e)}

            # ── Place Stop Loss ────────────────────────────────────────
            if stop_loss_price:
                sl_trigger = _round_price(stop_loss_price)
                for attempt in range(2):
                    try:
                        sl_result = exchange.order(
                            symbol,
                            close_is_buy,
                            size,
                            sl_trigger,
                            {
                                "trigger": {
                                    "triggerPx": sl_trigger,
                                    "isMarket": True,
                                    "tpsl": "sl",
                                }
                            },
                            reduce_only=True,
                        )

                        sl_status = sl_result.get("status")
                        if sl_status == "err":
                            err = sl_result.get("response", "Unknown")
                            logger.error(
                                "SL order rejected by HL for %s (attempt %d): %s",
                                symbol,
                                attempt + 1,
                                err,
                            )
                            results["sl"] = {"error": str(err)}
                            continue

                        statuses = (
                            sl_result.get("response", {})
                            .get("data", {})
                            .get("statuses", [])
                        )
                        if (
                            statuses
                            and isinstance(statuses[0], dict)
                            and "error" in statuses[0]
                        ):
                            err = statuses[0]["error"]
                            logger.error(
                                "SL order error for %s (attempt %d): %s",
                                symbol,
                                attempt + 1,
                                err,
                            )
                            results["sl"] = {"error": str(err)}
                            continue

                        results["sl"] = sl_result
                        sl_ok = True
                        logger.info(
                            "SL order placed: %s %s size=%s trigger=$%s",
                            "BUY" if close_is_buy else "SELL",
                            symbol,
                            size,
                            sl_trigger,
                        )
                        break
                    except Exception as e:
                        logger.error(
                            "Failed to place SL order for %s (attempt %d): %s",
                            symbol,
                            attempt + 1,
                            e,
                        )
                        results["sl"] = {"error": str(e)}

            # Report granular success
            tp_requested = take_profit_price is not None
            sl_requested = stop_loss_price is not None
            all_ok = (not tp_requested or tp_ok) and (not sl_requested or sl_ok)

            if not all_ok:
                logger.error(
                    "TP/SL placement incomplete for %s: tp=%s sl=%s",
                    symbol,
                    "OK" if tp_ok else f"FAILED ({results.get('tp')})",
                    "OK" if sl_ok else f"FAILED ({results.get('sl')})",
                )

            return {
                "success": all_ok,
                "tp_placed": tp_ok,
                "sl_placed": sl_ok,
                "results": results,
            }

        except ImportError:
            logger.warning("Hyperliquid SDK not configured for TP/SL orders")
            return {
                "success": False,
                "tp_placed": False,
                "sl_placed": False,
                "error": "SDK not configured",
            }
        except Exception as e:
            logger.error("Failed to place TP/SL orders for %s: %s", symbol, e)
            return {
                "success": False,
                "tp_placed": False,
                "sl_placed": False,
                "error": str(e),
            }

    async def close_position(
        self,
        wallet_private_key: str,
        symbol: str,
        size: float,
        is_buy: bool,
        account_address: str | None = None,
    ) -> dict:
        return await self.place_order(
            wallet_private_key=wallet_private_key,
            symbol=symbol,
            is_buy=not is_buy,
            size=size,
            account_address=account_address,
            order_type="market",
            reduce_only=True,
        )

    async def get_spot_state(self, wallet_address: str) -> dict:
        """Fetch the spot clearinghouse state (USDC, USDE, etc.)."""
        data = await self._post(
            "/info",
            {
                "type": "spotClearinghouseState",
                "user": wallet_address,
            },
        )
        return data

    async def get_spot_balances(self, wallet_address: str) -> list[dict]:
        """Return non-zero spot balances as [{coin, total, hold}]."""
        try:
            state = await self.get_spot_state(wallet_address)
            balances = state.get("balances", [])
            return [
                {
                    "coin": b.get("coin", ""),
                    "total": float(b.get("total", 0)),
                    "hold": float(b.get("hold", 0)),
                }
                for b in balances
                if float(b.get("total", 0)) > 0
            ]
        except Exception as e:
            logger.error(f"Failed to get spot balances: {e}")
            return []

    async def get_account_value(self, wallet_address: str) -> float:
        try:
            state = await self.get_user_state(wallet_address)
            return float(state.get("marginSummary", {}).get("accountValue", 0))
        except Exception as e:
            logger.error(f"Failed to get account value: {e}")
            return 0.0

    async def validate_agent_wallet(
        self,
        agent_private_key: str,
        account_address: str,
    ) -> tuple[bool, str]:
        """Verify that an agent wallet key can trade on behalf of account_address.

        Does a lightweight check by attempting to fetch user state and verifying
        the agent wallet address is valid. The actual approval check happens
        on the first order attempt, but this catches common misconfigurations.
        """
        try:
            from eth_account import Account

            key = (
                agent_private_key
                if agent_private_key.startswith("0x")
                else f"0x{agent_private_key}"
            )
            wallet = Account.from_key(key)
            agent_address = wallet.address

            # Check that the main account exists on HyperLiquid
            state = await self.get_user_state(account_address)
            if not state or not state.get("marginSummary"):
                return False, (
                    f"Main wallet {account_address} has no perps account on HyperLiquid. "
                    "Please deposit funds to the perps margin first."
                )

            logger.info(
                "Agent wallet %s validated for account %s",
                agent_address,
                account_address,
            )
            return True, "OK"

        except ImportError:
            return False, "eth_account not installed"
        except Exception as e:
            return False, f"Agent wallet validation failed: {e}"
