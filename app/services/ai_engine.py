import asyncio
import json
import logging
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.enums import SignalDirection, SignalStatus
from app.models.signal import Signal
from app.utils.helpers import calculate_risk_reward_ratio, utc_now

logger = logging.getLogger(__name__)
settings = get_settings()


ANALYSIS_PROMPT_TEMPLATE = """You are a professional crypto perpetual futures trader on HyperLiquid. Your #1 priority is capital preservation — you only take trades with a clear structural edge.

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

1. HIGHER TIMEFRAME ALIGNMENT:
   - BUY only when 4h trend is "bullish" or "ranging" — NEVER buy into a bearish 4h trend
   - SELL only when 4h trend is "bearish" or "ranging" — NEVER sell into a bullish 4h trend
   - EXCEPTION: If the asset is overextended (Rule 0), a COUNTER-TREND trade against
     the 4h trend is acceptable if exhaustion indicators confirm (declining volume + wick
     rejection + extreme RSI)
   - If the 4h trend contradicts the 1h signal and no exhaustion is present, output HOLD

2. STOP LOSS PLACEMENT (critical for survival):
   - Place stop loss BEHIND the nearest key level (support for BUY, resistance for SELL)
   - BUY stop loss must be BELOW the nearest support level — not at or above it
   - SELL stop loss must be ABOVE the nearest resistance level — not at or below it
   - Minimum SL distance: 1.5x the ATR (use ATR % provided) — this avoids wick-hunts
   - If no clear support/resistance exists, use 2-3% from entry as SL
   - NEVER use a stop loss tighter than 1.5% — crypto wicks will hunt it

3. TAKE PROFIT:
   - Risk:reward ratio must be at least 2.5:1 (TP distance >= 2.5x SL distance)
   - Target the nearest resistance (for BUY) or support (for SELL) as first TP
   - For mean-reversion trades: target the SMA20 or middle of the range as TP
   - If the nearest level doesn't offer 2.5:1 R:R, signal HOLD

4. ENTRY QUALITY:
   - For TREND-FOLLOWING entries:
     BUY only if RSI < 55, SELL only if RSI > 45
   - For MEAN-REVERSION entries (after overextension):
     BUY only if RSI < 35 (oversold), SELL only if RSI > 65 (overbought)
   - If 24h change > +5%: BUY requires RSI < 40 (stricter — asset already ran)
   - If 24h change < -5%: SELL requires RSI > 60 (stricter — asset already fell)
   - 4h RSI should confirm: avoid BUY if 4h RSI > 70, avoid SELL if 4h RSI < 30
   - Volume must be normal or high — avoid low-volume setups
   - Price should be near a key level (within 1.5% of support for BUY, resistance for SELL)

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

            # Key levels
            support = candle.get("nearest_support")
            resistance = candle.get("nearest_resistance")
            level_lines = (
                f"- Nearest Support: ${support if support else 'none identified'}\n"
                f"- Nearest Resistance: ${resistance if resistance else 'none identified'}\n"
                f"- 24h Low (floor): ${candle.get('low_24h', 'N/A')}\n"
                f"- 24h High (ceiling): ${candle.get('high_24h', 'N/A')}"
            )
        else:
            candle_lines = "No candle data available"
            htf_lines = "No higher timeframe data available"
            exhaustion_lines = "No exhaustion data available"
            level_lines = "No key levels identified"

        prompt = ANALYSIS_PROMPT_TEMPLATE.format(
            symbol=symbol,
            price=market_data.get("mid_price", "N/A"),
            candle_summary=candle_lines,
            htf_summary=htf_lines,
            exhaustion_summary=exhaustion_lines,
            key_levels=level_lines,
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

    async def _get_active_signal_symbols(self, db: AsyncSession) -> set[str]:
        """Return symbols that still have an active (non-expired) signal.

        Only skips symbols with a live signal — expired signals don't block
        re-analysis, so each pipeline run can re-evaluate the market fresh.
        """
        now = utc_now()
        query = select(Signal.symbol).where(
            Signal.status == SignalStatus.NEW,
            Signal.expires_at > now,
        )
        result = await db.execute(query)
        return {row[0] for row in result.all()}

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
        active_symbols = await self._get_active_signal_symbols(db)
        if active_symbols:
            logger.info(
                "Skipping %d symbols with active signals: %s",
                len(active_symbols),
                ", ".join(sorted(active_symbols)),
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

            if symbol in active_symbols:
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

            if aggregated["confidence"] < settings.ate_confidence_threshold:
                logger.info(
                    "Skipping %s: confidence %.2f below %.2f threshold",
                    symbol,
                    aggregated["confidence"],
                    settings.ate_confidence_threshold,
                )
                hold_count += 1
                continue

            # Require minimum 2:1 risk:reward ratio
            rr = aggregated.get("risk_reward_ratio", 0)
            if rr and rr < 2.0:
                logger.info("Skipping %s: R:R ratio %.2f below 2.0 minimum", symbol, rr)
                hold_count += 1
                continue

            # Reject signals with stop loss too tight (< 1.5% from entry)
            entry = aggregated["entry_price"]
            sl = aggregated["stop_loss"]
            if entry > 0 and sl > 0:
                sl_distance_pct = abs(entry - sl) / entry * 100
                if sl_distance_pct < 1.5:
                    logger.info(
                        "Skipping %s: SL too tight (%.2f%% from entry), "
                        "minimum 1.5%% required",
                        symbol,
                        sl_distance_pct,
                    )
                    hold_count += 1
                    continue

            if direction == SignalDirection.BUY:
                buy_count += 1
            else:
                sell_count += 1

            signal = Signal(
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
