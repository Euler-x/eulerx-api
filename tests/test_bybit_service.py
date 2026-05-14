from unittest.mock import AsyncMock

import pytest

from app.services.bybit import BybitService, settings as bybit_settings


def test_init_respects_explicit_testnet_flag(monkeypatch):
    monkeypatch.setattr(bybit_settings, "bybit_testnet", True)

    default_service = BybitService()
    explicit_mainnet = BybitService(testnet=False)
    explicit_testnet = BybitService(testnet=True)

    assert default_service.testnet is True
    assert explicit_mainnet.testnet is False
    assert explicit_testnet.testnet is True


@pytest.mark.asyncio
async def test_get_lot_size_info_prefers_market_max_qty(monkeypatch):
    service = BybitService(testnet=True)

    async def fake_instrument_info(_symbol: str):
        return {
            "lotSizeFilter": {
                "qtyStep": "0.1",
                "minOrderQty": "0.1",
                "maxOrderQty": "1250000",
                "maxMktOrderQty": "250000",
            }
        }

    monkeypatch.setattr(service, "get_instrument_info", fake_instrument_info)

    market_info = await service.get_lot_size_info("SAGAUSDT", order_type="market")
    limit_info = await service.get_lot_size_info("SAGAUSDT", order_type="limit")

    assert market_info["max_order_qty"] == 250000.0
    assert market_info["max_market_order_qty"] == 250000.0
    assert limit_info["max_order_qty"] == 1250000.0
    assert limit_info["max_limit_order_qty"] == 1250000.0


@pytest.mark.asyncio
async def test_place_tp_sl_orders_uses_mark_price_triggers(monkeypatch):
    service = BybitService(testnet=True)
    captured: dict = {}

    class FakeSession:
        def set_trading_stop(self, **kwargs):
            captured.update(kwargs)
            return {"retCode": 0, "result": {}}

    monkeypatch.setattr(
        service, "_get_session", lambda *_args, **_kwargs: FakeSession()
    )
    monkeypatch.setattr(service, "get_tick_size", AsyncMock(return_value=0.1))

    result = await service.place_tp_sl_orders(
        api_key="key",
        api_secret="secret",
        symbol="BTCUSDT",
        is_buy=True,
        take_profit_price=101.27,
        stop_loss_price=98.74,
    )

    assert result["success"] is True
    assert captured["category"] == "linear"
    assert captured["symbol"] == "BTCUSDT"
    assert captured["tpslMode"] == "Full"
    assert captured["positionIdx"] == 0
    assert captured["tpOrderType"] == "Market"
    assert captured["slOrderType"] == "Market"
    assert captured["tpTriggerBy"] == "MarkPrice"
    assert captured["slTriggerBy"] == "MarkPrice"
    assert captured["takeProfit"] == "101.3"
    assert captured["stopLoss"] == "98.7"


@pytest.mark.asyncio
async def test_wait_for_order_fill_returns_avg_price_and_qty(monkeypatch):
    service = BybitService(testnet=True)
    service.get_order_fill_summary = AsyncMock(
        side_effect=[
            None,
            {
                "order_id": "123",
                "avg_price": 1.2345,
                "executed_qty": 250.0,
                "status": "Filled",
            },
        ]
    )

    result = await service.wait_for_order_fill(
        api_key="key",
        api_secret="secret",
        symbol="QUSDT",
        order_id="123",
        attempts=2,
        delay_seconds=0,
    )

    assert result is not None
    assert result["avg_price"] == pytest.approx(1.2345)
    assert result["executed_qty"] == pytest.approx(250.0)
