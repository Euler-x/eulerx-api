import asyncio
import json
import logging
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.bybit_signal import BybitSignal
from app.models.enums import Exchange, SignalDirection, SignalStatus
from app.models.signal import Signal
from app.utils.helpers import calculate_risk_reward_ratio, utc_now

logger = logging.getLogger(__name__)
settings = get_settings()


ANALYSIS_PROMPT_TEMPLATE = """You are a professional crypto perpetual futures trader on {exchange_name}. Your #1 priority is capital preservation — you only take trades with a clear structural edge.

CRITICAL CONTEXT: This is perpetual futures — SHORT positions profit from price drops. You must treat BUY and SELL equally. Do NOT have a long bias. After large pumps, SHORT setups are often HIGHER probability than BUY setups.

Symbol: {symbol}
Current Price: ${price}

Market Data (1h candles, 24h):
{candle_summary}

Higher Timeframe Context:
{htf_summary}

Exhaustion & Mean-Reversion Indicators:
{exhaustion_summary}

Key Levels:
{key_levels}

Lower Timeframe Entry Data (15m + 5m):
{ltf_entry_summary}

Full Context: {context}

Respond ONLY with valid JSON (no markdown, no explanation):
{{
    "signal": "BUY" or "SELL" or "HOLD",
    "confidence": 0.0 to 1.0,
    "entry_price": number,
    "stop_loss": number,
    "take_profit": number,
    "reasoning": "brief explanation",
    "indicators": {{
        "trend": "bullish/bearish/neutral",
        "trend_strength": "strong/moderate/weak",
        "momentum": "overbought/oversold/neutral",
        "rsi_estimate": number (0-100),
        "volume_assessment": "high/normal/low",
        "volatility": "high/normal/low"
    }}
}}

MANDATORY RULES — you must follow ALL of these IN ORDER:

0. OVEREXTENSION CHECK (evaluate this FIRST — before anything else):
   - If 24h price change > +8% AND RSI (1h) > 65: the asset is OVEREXTENDED UP
     → Default bias must be SELL or HOLD — do NOT buy into a pump
     → Only BUY if price has pulled back to a key support AND RSI has cooled below 45
   - If 24h price change < -8% AND RSI (1h) < 35: the asset is OVEREXTENDED DOWN
     → Default bias must be BUY or HOLD — do NOT short into a crash
     → Only SELL if price has bounced to a key resistance AND RSI has risen above 55
   - If price is >4% above SMA20 (price_vs_sma20_pct > 4): treat as overextended up
   - If price is >4% below SMA20 (price_vs_sma20_pct < -4): treat as overextended down
   - If Bollinger Band position is "above_upper": strongly favor SELL or HOLD
   - If Bollinger Band position is "below_lower": strongly favor BUY or HOLD
   - Declining volume on a large move = exhaustion signal, favor counter-trend
   - Bearish wicks (rejection_signal = "bearish_wicks") after a pump = distribution, favor SELL
   - Bullish wicks (rejection_signal = "bullish_wicks") after a dump = accumulation, favor BUY
   - Extreme positive funding rate (> 0.01%) = crowded longs, favor SELL or HOLD
   - Extreme negative funding rate (< -0.01%) = crowded shorts, favor BUY or HOLD

1. MULTI-TIMEFRAME ALIGNMENT (4h → 1h → 15m → 5m):
   Step 1 — 4h TREND DIRECTION (bias):
   - 4h bullish (higher highs + higher lows) → only look for BUY setups
   - 4h bearish (lower highs + lower lows) → only look for SELL setups
   - 4h ranging → either direction is acceptable if lower TFs confirm
   - EXCEPTION: Counter-trend allowed ONLY if asset is overextended (Rule 0) with exhaustion

   Step 2 — 1h TREND CONFIRMATION:
   - 1h trend must align with 4h bias (both bullish for BUY, both bearish for SELL)
   - If 1h and 4h conflict and no exhaustion setup → HOLD
   - 1h RSI confirms momentum is not exhausted in the trade direction

   Step 3 — 15m ENTRY ZONE:
   - Price must be at or near a 15m key level (15m support for BUY, 15m resistance for SELL)
   - 15m trend should align OR be showing a pullback into the zone
   - If price is mid-range on 15m with no level test → HOLD

   Step 4 — 5m ENTRY TRIGGER:
   - 5m trend must start turning in the trade direction (bullish for BUY, bearish for SELL)
   - 5m last 3 candles pattern must confirm: "bullish" for BUY, "bearish" for SELL
   - If 5m shows no trigger (mixed candles, wrong direction) → HOLD
   - ALL 4 timeframes must align before taking a trade

2. STOP LOSS PLACEMENT (use lower TF structure for tighter SL):
   - Place stop loss BEHIND the 15m key level (15m support for BUY, 15m resistance for SELL)
   - BUY SL: below the 15m support level by 1x the 5m ATR
   - SELL SL: above the 15m resistance level by 1x the 5m ATR
   - This gives a tighter SL than using 1h structure, improving R:R
   - If 5m ATR is available, use it for SL padding. Otherwise use 0.5-1% from 15m level
   - Minimum SL distance: 0.5% (5m structure allows tighter stops)
   - Maximum SL distance: 2% (if wider than this, the setup is too risky)

3. TAKE PROFIT:
   - Risk:reward ratio must be at least 2.5:1 (TP distance >= 2.5x SL distance)
   - Target the nearest 1h resistance (for BUY) or 1h support (for SELL)
   - For mean-reversion trades: target SMA20 or middle of the 1h range
   - With tighter SL from 15m/5m structure, 2.5:1 R:R is more achievable
   - If the nearest 1h level doesn't offer 2.5:1 R:R, signal HOLD

4. ENTRY QUALITY FILTERS:
   - 4h RSI: avoid BUY if > 70, avoid SELL if < 30 (overextended on HTF)
   - 1h RSI: BUY only if < 55, SELL only if > 45
   - 15m RSI: BUY only if < 60 (not overbought on entry TF), SELL only if > 40
   - If 24h change > +5%: BUY requires 1h RSI < 40 (asset already ran up)
   - If 24h change < -5%: SELL requires 1h RSI > 60 (asset already fell)
   - Volume must be normal or high — avoid low-volume setups
   - 5m candle pattern must confirm direction (bullish candles for BUY, bearish for SELL)

5. CONFIDENCE SCORING:
   - 0.85+: Strong multi-timeframe alignment, clear level, volume confirms
   - 0.75-0.84: Good setup but one minor concern
   - 0.70-0.74: Marginal — only with very clear structure
   - Below 0.70: Signal HOLD regardless
   - PENALTY: If signal direction matches 24h move direction (buying a pump or shorting a dump),
     subtract 0.10 from confidence — you're chasing, not anticipating

6. DEFAULT TO HOLD:
   - When in doubt, HOLD. Missed trades cost nothing, bad trades cost capital
   - If price is mid-range with no clear level test, HOLD
   - If 1h and 4h trends conflict AND no exhaustion setup, HOLD
   - If volatility is extreme (ATR > 5%), HOLD — conditions are too choppy
   - If the asset already moved >10% in 24h and you can't find a mean-reversion setup, HOLD
"""


class AIEngineService:
    def __init__(self):
        self.api_key = settings.openrouter_api_key
        self.base_url = settings.openrouter_base_url
        self.models = settings.openrouter_model_list

    async def query_model(
        self, model_id: str, symbol: str, market_data: dict
    ) -> dict | None:
        if not self.api_key:
            logger.warning("OpenRouter API key not configured")
            return None

        candle = market_data.get("candle_summary", {})
        if candle:
            candle_lines = (
                f"- 24h Change: {candle.get('price_change_24h_pct', 'N/A')}%\n"
                f"- 24h High: ${candle.get('high_24h', 'N/A')}\n"
                f"- 24h Low: ${candle.get('low_24h', 'N/A')}\n"
                f"- 24h Volume: {candle.get('total_volume_24h', 'N/A')}\n"
                f"- Avg Hourly Volume: {candle.get('avg_hourly_volume', 'N/A')}\n"
                f"- Volatility (24h range): {candle.get('volatility_pct', 'N/A')}%\n"
                f"- ATR (14-period, 1h): {candle.get('atr_14_pct', 'N/A')}%\n"
                f"- Recent Trend (6h): {candle.get('recent_trend', 'N/A')}\n"
                f"- RSI (14, 1h): {candle.get('rsi_14', 'N/A')}"
            )

            # Higher timeframe summary
            htf_trend = candle.get("htf_trend_4h", "unknown")
            htf_rsi = candle.get("htf_rsi_4h")
            htf_lines = (
                f"- 4h Trend (market structure): {htf_trend}\n"
                f"- 4h RSI: {htf_rsi if htf_rsi is not None else 'N/A'}"
            )

            # Exhaustion / mean-reversion indicators
            regime = candle.get("regime", "unknown")
            funding = candle.get("funding_rate", 0)
            exhaustion_lines = (
                f"- Market Regime: {regime}\n"
                f"- Price vs SMA20: {candle.get('price_vs_sma20_pct', 'N/A')}% "
                f"({'above' if (candle.get('price_vs_sma20_pct', 0) or 0) > 0 else 'below'} mean)\n"
                f"- Volume Trend (recent vs prior 6h): {candle.get('volume_trend', 'N/A')}\n"
                f"- Candle Wick Analysis: {candle.get('rejection_signal', 'N/A')}\n"
                f"- Bollinger Band Position: {candle.get('bb_position', 'N/A')}\n"
                f"- Funding Rate: {funding}% "
                f"({'crowded longs — contrarian SHORT signal' if funding > 0.01 else 'crowded shorts — contrarian BUY signal' if funding < -0.01 else 'neutral'})"
            )

            # Key levels (1h)
            support = candle.get("nearest_support")
            resistance = candle.get("nearest_resistance")
            level_lines = (
                f"- 1h Support: ${support if support else 'none identified'}\n"
                f"- 1h Resistance: ${resistance if resistance else 'none identified'}\n"
                f"- 15m Support: ${candle.get('ltf_15m_support') or 'none identified'}\n"
                f"- 15m Resistance: ${candle.get('ltf_15m_resistance') or 'none identified'}\n"
                f"- 24h Low (floor): ${candle.get('low_24h', 'N/A')}\n"
                f"- 24h High (ceiling): ${candle.get('high_24h', 'N/A')}"
            )

            # Lower timeframe entry data (15m + 5m)
            ltf_lines = (
                f"15m Entry Zone:\n"
                f"  - 15m Trend: {candle.get('ltf_15m_trend', 'N/A')}\n"
                f"  - 15m RSI: {candle.get('ltf_15m_rsi', 'N/A')}\n"
                f"5m Entry Trigger:\n"
                f"  - 5m Trend (last 1h): {candle.get('ltf_5m_trend', 'N/A')}\n"
                f"  - 5m RSI: {candle.get('ltf_5m_rsi', 'N/A')}\n"
                f"  - 5m Last 3 candles: {candle.get('ltf_5m_candle_pattern', 'N/A')}\n"
                f"  - 5m ATR: {candle.get('ltf_5m_atr_pct', 'N/A')}%"
            )
        else:
            candle_lines = "No candle data available"
            htf_lines = "No higher timeframe data available"
            exhaustion_lines = "No exhaustion data available"
            level_lines = "No key levels identified"
            ltf_lines = "No lower timeframe data available"

        # Determine exchange name for prompt context
        exchange_raw = market_data.get("exchange", "hyperliquid")
        exchange_name = "Bybit" if exchange_raw == "bybit" else "HyperLiquid"

        prompt = ANALYSIS_PROMPT_TEMPLATE.format(
            exchange_name=exchange_name,
            symbol=symbol,
            price=market_data.get("mid_price", "N/A"),
            candle_summary=candle_lines,
            htf_summary=htf_lines,
            exhaustion_summary=exhaustion_lines,
            key_levels=level_lines,
            ltf_entry_summary=ltf_lines,
            context=json.dumps(market_data, default=str),
        )

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model_id,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.3,
                        "max_tokens": 500,
                    },
                )
                response.raise_for_status()
                data = response.json()

                content = data["choices"][0]["message"]["content"]
                # Strip markdown code fences if present
                content = content.strip()
                if content.startswith("```"):
                    content = (
                        content.split("\n", 1)[1] if "\n" in content else content[3:]
                    )
                if content.endswith("```"):
                    content = content[:-3]
                content = content.strip()

                parsed = json.loads(content)
                parsed["model_id"] = model_id
                return parsed

        except httpx.HTTPStatusError as e:
            logger.error(
                f"OpenRouter API error for {model_id}: {e.response.status_code}"
            )
            return None
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logger.error(f"Failed to parse response from {model_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error querying {model_id}: {e}")
            return None

    async def query_all_models(self, symbol: str, market_data: dict) -> list[dict]:
        tasks = [
            self.query_model(model_id, symbol, market_data) for model_id in self.models
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        valid_results = []
        for r in results:
            if isinstance(r, dict) and r is not None:
                valid_results.append(r)
            elif isinstance(r, Exception):
                logger.error(f"Model query exception: {r}")
        return valid_results

    def aggregate_signals(
        self, model_responses: list[dict], candle_summary: dict | None = None
    ) -> dict | None:
        if not model_responses:
            return None

        buy_count = 0
        sell_count = 0
        hold_count = 0
        buy_responses = []
        sell_responses = []

        for resp in model_responses:
            signal = resp.get("signal", "HOLD").upper()
            if signal == "BUY":
                buy_count += 1
                buy_responses.append(resp)
            elif signal == "SELL":
                sell_count += 1
                sell_responses.append(resp)
            else:
                hold_count += 1

        total = len(model_responses)
        threshold = total / 2  # majority consensus

        if buy_count > threshold:
            direction = SignalDirection.BUY
            consensus_responses = buy_responses
        elif sell_count > threshold:
            direction = SignalDirection.SELL
            consensus_responses = sell_responses
        else:
            return None  # No consensus, HOLD

        # Average the consensus responses
        avg_confidence = sum(
            r.get("confidence", 0.5) for r in consensus_responses
        ) / len(consensus_responses)
        avg_entry = sum(r.get("entry_price", 0) for r in consensus_responses) / len(
            consensus_responses
        )
        avg_sl = sum(r.get("stop_loss", 0) for r in consensus_responses) / len(
            consensus_responses
        )
        avg_tp = sum(r.get("take_profit", 0) for r in consensus_responses) / len(
            consensus_responses
        )

        # ── Phase 5: Confidence penalty when chasing the move ──────────
        if candle_summary:
            change_24h = candle_summary.get("price_change_24h_pct", 0)
            # Buying into a pump or shorting into a dump = chasing
            if direction == SignalDirection.BUY and change_24h > 5:
                penalty = min(0.15, change_24h * 0.01)  # up to 0.15
                logger.info(
                    "Confidence penalty -%.2f for BUY on %s (+%.1f%% 24h)",
                    penalty,
                    candle_summary.get("symbol", "?"),
                    change_24h,
                )
                avg_confidence -= penalty
            elif direction == SignalDirection.SELL and change_24h < -5:
                penalty = min(0.15, abs(change_24h) * 0.01)
                logger.info(
                    "Confidence penalty -%.2f for SELL on %s (%.1f%% 24h)",
                    penalty,
                    candle_summary.get("symbol", "?"),
                    change_24h,
                )
                avg_confidence -= penalty

        # Aggregate indicators from first consensus response
        indicators = consensus_responses[0].get("indicators", {})

        rr_ratio = calculate_risk_reward_ratio(avg_entry, avg_sl, avg_tp)

        return {
            "direction": direction,
            "confidence": round(avg_confidence, 3),
            "entry_price": round(avg_entry, 8),
            "stop_loss": round(avg_sl, 8),
            "take_profit": round(avg_tp, 8),
            "risk_reward_ratio": rr_ratio,
            "indicators": indicators,
            "consensus": f"{max(buy_count, sell_count)}/{total}",
            "model_responses": {
                r.get("model_id", f"model_{i}"): {
                    "signal": r.get("signal"),
                    "confidence": r.get("confidence"),
                    "reasoning": r.get("reasoning"),
                }
                for i, r in enumerate(model_responses)
            },
        }

    async def _get_active_signal_keys(self, db: AsyncSession) -> set[tuple[str, str]]:
        """Return (symbol, exchange) pairs that still have an active signal.

        Queries both Signal (HL) and BybitSignal tables.
        """
        now = utc_now()
        keys: set[tuple[str, str]] = set()

        # HL signals
        hl_result = await db.execute(
            select(Signal.symbol).where(
                Signal.status == SignalStatus.NEW,
                Signal.expires_at > now,
            )
        )
        for row in hl_result.all():
            keys.add((row[0], "hyperliquid"))

        # Bybit signals
        bb_result = await db.execute(
            select(BybitSignal.symbol).where(
                BybitSignal.status == SignalStatus.NEW,
                BybitSignal.expires_at > now,
            )
        )
        for row in bb_result.all():
            keys.add((row[0], "bybit"))

        return keys

    async def generate_signals(
        self,
        symbols: list[dict],
        db: AsyncSession,
    ) -> list[Signal]:
        """Analyze market symbols and generate strategy-independent signals.

        Signal generation is purely about market analysis — no strategy context.
        Strategies are only applied later during trade execution for risk management.
        """
        generated_signals = []

        # Only skip symbols that still have an active (non-expired) signal.
        # Expired signals no longer block re-analysis.
        active_signal_keys = await self._get_active_signal_keys(db)
        if active_signal_keys:
            logger.info(
                "Skipping %d symbols with active signals: %s",
                len(active_signal_keys),
                ", ".join(f"{s}({e})" for s, e in sorted(active_signal_keys)),
            )

        # Signal lifetime matches pipeline frequency so signals stay live
        # until the next pipeline run can replace them.
        signal_lifetime = timedelta(hours=settings.analysis_schedule_hours, minutes=15)

        buy_count = 0
        sell_count = 0
        hold_count = 0

        for symbol_data in symbols:
            symbol = symbol_data.get("symbol", "")
            if not symbol:
                continue

            exchange_str = symbol_data.get("exchange", Exchange.HYPERLIQUID.value)
            signal_key = (symbol, exchange_str)
            if signal_key in active_signal_keys:
                continue

            candle = symbol_data.get("candle_summary", {})
            model_responses = await self.query_all_models(symbol, symbol_data)
            aggregated = self.aggregate_signals(model_responses, candle_summary=candle)

            if aggregated is None:
                hold_count += 1
                logger.info("No consensus for %s, skipping", symbol)
                continue

            direction = aggregated["direction"]

            # ── Phase 4: Hard post-generation filters ──────────────────
            change_24h = candle.get("price_change_24h_pct", 0)
            rsi = candle.get("rsi_14", 50)
            funding = candle.get("funding_rate", 0)

            # Reject BUY on overbought / overextended up assets
            if direction == SignalDirection.BUY and change_24h > 10 and rsi > 65:
                logger.info(
                    "FILTER: Rejecting BUY on %s — +%.1f%% 24h, RSI %.1f (overextended up)",
                    symbol,
                    change_24h,
                    rsi,
                )
                hold_count += 1
                continue

            # Reject SELL on oversold / overextended down assets
            if direction == SignalDirection.SELL and change_24h < -10 and rsi < 35:
                logger.info(
                    "FILTER: Rejecting SELL on %s — %.1f%% 24h, RSI %.1f (overextended down)",
                    symbol,
                    change_24h,
                    rsi,
                )
                hold_count += 1
                continue

            # Reject signals against extreme funding (crowded trades)
            if direction == SignalDirection.BUY and funding > 0.05:
                logger.info(
                    "FILTER: Rejecting BUY on %s — funding rate %.3f%% (crowded longs)",
                    symbol,
                    funding,
                )
                hold_count += 1
                continue
            if direction == SignalDirection.SELL and funding < -0.05:
                logger.info(
                    "FILTER: Rejecting SELL on %s — funding rate %.3f%% (crowded shorts)",
                    symbol,
                    funding,
                )
                hold_count += 1
                continue

            from app.services.dynamic_config import get_config

            confidence_threshold = await get_config(
                "ate_confidence_threshold", settings.ate_confidence_threshold, db
            )
            if aggregated["confidence"] < confidence_threshold:
                logger.info(
                    "Skipping %s: confidence %.2f below %.2f threshold",
                    symbol,
                    aggregated["confidence"],
                    confidence_threshold,
                )
                hold_count += 1
                continue

            # Require minimum 2:1 risk:reward ratio
            rr = aggregated.get("risk_reward_ratio", 0)
            if rr and rr < 2.0:
                logger.info("Skipping %s: R:R ratio %.2f below 2.0 minimum", symbol, rr)
                hold_count += 1
                continue

            # Require non-zero entry, stop_loss, and take_profit — reject naked signals
            entry = aggregated["entry_price"]
            sl = aggregated["stop_loss"]
            tp = aggregated["take_profit"]
            if not (entry > 0 and sl > 0 and tp > 0):
                logger.info(
                    "Skipping %s: zero or missing entry/SL/TP "
                    "(entry=%.8f, sl=%.8f, tp=%.8f) — risk management required",
                    symbol,
                    entry,
                    sl,
                    tp,
                )
                hold_count += 1
                continue

            # Reject signals with stop loss too tight (< 0.5%) or too wide (> 3%)
            sl_distance_pct = abs(entry - sl) / entry * 100
            if sl_distance_pct < 0.5:
                logger.info(
                    "Skipping %s: SL too tight (%.2f%% from entry), "
                    "minimum 0.5%% required",
                    symbol,
                    sl_distance_pct,
                )
                hold_count += 1
                continue
            if sl_distance_pct > 3.0:
                logger.info(
                    "Skipping %s: SL too wide (%.2f%% from entry), "
                    "maximum 3%% — entry not precise enough",
                    symbol,
                    sl_distance_pct,
                )
                hold_count += 1
                continue

            if direction == SignalDirection.BUY:
                buy_count += 1
            else:
                sell_count += 1

            # Create the right signal model based on exchange
            is_bybit = exchange_str == Exchange.BYBIT.value
            signal_kwargs = dict(
                symbol=symbol,
                direction=aggregated["direction"],
                confidence=aggregated["confidence"],
                entry_price=aggregated["entry_price"],
                stop_loss=aggregated["stop_loss"],
                take_profit=aggregated["take_profit"],
                risk_reward_ratio=aggregated["risk_reward_ratio"],
                indicators=aggregated["indicators"],
                status=SignalStatus.NEW,
                expires_at=utc_now() + signal_lifetime,
                model_responses=aggregated["model_responses"],
            )

            if is_bybit:
                signal = BybitSignal(**signal_kwargs)
            else:
                signal = Signal(**signal_kwargs)

            db.add(signal)
            generated_signals.append(signal)

        if generated_signals:
            await db.flush()

        # Direction balance logging
        logger.info(
            "Signal generation complete: %d BUY, %d SELL, %d HOLD/filtered — "
            "total symbols analyzed: %d",
            buy_count,
            sell_count,
            hold_count,
            len(symbols),
        )

        return generated_signals
