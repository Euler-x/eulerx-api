import asyncio
import json
import logging
from datetime import timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.enums import SignalDirection, SignalStatus
from app.models.signal import Signal
from app.utils.helpers import calculate_risk_reward_ratio, utc_now

logger = logging.getLogger(__name__)
settings = get_settings()


ANALYSIS_PROMPT_TEMPLATE = """You are a professional cryptocurrency perpetual futures analyst on HyperLiquid. Analyze the following market data and provide a trading signal.

Symbol: {symbol}
Current Price: ${price}

24h Market Data:
{candle_summary}

Full Context: {context}

Respond ONLY with valid JSON in this exact format (no markdown, no explanation):
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

Rules:
- Use the 24h candle data (price change, RSI, volume, trend) to make informed decisions
- Stop loss should be 1-3% from entry for conservative trades
- Take profit should target 1.5-5% from entry
- Signal BUY if trend is bullish with strong momentum and RSI is not overbought (< 70)
- Signal SELL if trend is bearish with strong momentum and RSI is not oversold (> 30)
- Signal HOLD only if there is genuinely no clear directional bias
- Be decisive — if data shows a clear trend, commit to a signal with appropriate confidence
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
                f"- Volatility: {candle.get('volatility_pct', 'N/A')}%\n"
                f"- Recent Trend (6h): {candle.get('recent_trend', 'N/A')}\n"
                f"- RSI (14): {candle.get('rsi_14', 'N/A')}"
            )
        else:
            candle_lines = "No candle data available"

        prompt = ANALYSIS_PROMPT_TEMPLATE.format(
            symbol=symbol,
            price=market_data.get("mid_price", "N/A"),
            candle_summary=candle_lines,
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

    def aggregate_signals(self, model_responses: list[dict]) -> dict | None:
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

    async def generate_signals(
        self,
        symbols: list[dict],
        db: AsyncSession,
        strategy_id: str | None = None,
    ) -> list[Signal]:
        generated_signals = []

        for symbol_data in symbols:
            symbol = symbol_data.get("symbol", "")
            if not symbol:
                continue

            model_responses = await self.query_all_models(symbol, symbol_data)
            aggregated = self.aggregate_signals(model_responses)

            if aggregated is None:
                continue

            if aggregated["confidence"] < settings.ate_confidence_threshold:
                continue

            signal = Signal(
                strategy_id=strategy_id,
                symbol=symbol,
                direction=aggregated["direction"],
                confidence=aggregated["confidence"],
                entry_price=aggregated["entry_price"],
                stop_loss=aggregated["stop_loss"],
                take_profit=aggregated["take_profit"],
                risk_reward_ratio=aggregated["risk_reward_ratio"],
                indicators=aggregated["indicators"],
                status=SignalStatus.NEW,
                expires_at=utc_now() + timedelta(hours=1),
                model_responses=aggregated["model_responses"],
            )

            db.add(signal)
            generated_signals.append(signal)

        if generated_signals:
            await db.flush()

        return generated_signals
