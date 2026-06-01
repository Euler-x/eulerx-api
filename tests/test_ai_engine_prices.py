from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.models.enums import SignalDirection
from app.services.ai_engine import AIEngineService
from tests.conftest import TestSessionFactory


def test_normalize_signal_prices_anchors_entry_and_repairs_buy_stack():
    engine = AIEngineService()

    result = engine._normalize_signal_prices(
        symbol="BTCUSDT",
        direction=SignalDirection.BUY,
        market_data={"mid_price": 100.0},
        candle_summary={
            "ltf_15m_support": 99.2,
            "nearest_resistance": 105.0,
            "ltf_5m_atr_pct": 0.6,
        },
        entry_price=103.0,
        stop_loss=101.0,
        take_profit=102.0,
    )

    assert result is not None
    entry, stop_loss, take_profit, rr = result
    assert entry == pytest.approx(100.0)
    assert stop_loss == pytest.approx(98.6)
    assert take_profit >= 103.5
    assert rr >= 2.5


def test_normalize_signal_prices_anchors_entry_and_repairs_sell_stack():
    engine = AIEngineService()

    result = engine._normalize_signal_prices(
        symbol="ETHUSDT",
        direction=SignalDirection.SELL,
        market_data={"mid_price": 100.0},
        candle_summary={
            "ltf_15m_resistance": 100.8,
            "nearest_support": 95.0,
            "ltf_5m_atr_pct": 0.6,
        },
        entry_price=97.0,
        stop_loss=99.0,
        take_profit=101.0,
    )

    assert result is not None
    entry, stop_loss, take_profit, rr = result
    assert entry == pytest.approx(100.0)
    assert stop_loss == pytest.approx(101.4)
    assert take_profit <= 96.5
    assert rr >= 2.5


@pytest.mark.asyncio
async def test_generate_signals_persists_normalized_prices(setup_db):
    engine = AIEngineService()
    market_data = [
        {
            "symbol": "BTCUSDT",
            "exchange": "binance",
            "mid_price": 100.0,
            "candle_summary": {
                "price_change_24h_pct": 1.0,
                "rsi_14": 50.0,
                "funding_rate": 0.0,
                "ltf_15m_support": 99.2,
                "nearest_resistance": 105.0,
                "ltf_5m_atr_pct": 0.6,
            },
        }
    ]
    model_responses = [
        {
            "model_id": "model-a",
            "signal": "BUY",
            "confidence": 0.91,
            "entry_price": 104.0,
            "stop_loss": 101.0,
            "take_profit": 102.0,
            "reasoning": "test",
        },
        {
            "model_id": "model-b",
            "signal": "BUY",
            "confidence": 0.89,
            "entry_price": 103.0,
            "stop_loss": 101.5,
            "take_profit": 102.5,
            "reasoning": "test",
        },
    ]

    async with TestSessionFactory() as session:
        with (
            patch.object(
                engine,
                "query_all_models",
                new=AsyncMock(return_value=model_responses),
            ),
            patch(
                "app.services.dynamic_config.get_config",
                new=AsyncMock(return_value=0.7),
            ),
        ):
            signals = await engine.generate_signals(market_data, session)
            await session.commit()

    assert len(signals) == 1
    signal = signals[0]
    assert float(signal.entry_price) == pytest.approx(100.0)
    assert float(signal.stop_loss) == pytest.approx(98.6)
    assert float(signal.take_profit) >= 103.5
    assert signal.risk_reward_ratio >= 2.5
    assert signal.direction == SignalDirection.BUY
    assert Decimal(str(signal.entry_price)) != Decimal("103.5")
