from unittest.mock import AsyncMock

import pytest

from app.services.binance import BinanceService


@pytest.mark.asyncio
async def test_get_lot_size_info_prefers_market_lot_size(monkeypatch):
    service = BinanceService(testnet=True)

    async def fake_exchange_info():
        return {
            "SAGAUSDT": {
                "step_size": 0.1,
                "min_qty": 0.1,
                "max_qty": 1_000_000.0,
                "market_step_size": 0.1,
                "market_min_qty": 0.1,
                "market_max_qty": 30_000.0,
            }
        }

    monkeypatch.setattr(service, "get_exchange_info", fake_exchange_info)

    market_info = await service.get_lot_size_info("SAGAUSDT", order_type="market")
    limit_info = await service.get_lot_size_info("SAGAUSDT", order_type="limit")

    assert market_info["max_order_qty"] == 30_000.0
    assert limit_info["max_order_qty"] == 1_000_000.0


def test_summarize_close_trade_group_uses_weighted_average_price():
    trades = [
        {
            "qty": "2",
            "price": "1.10",
            "quoteQty": "2.20",
            "realizedPnl": "0.50",
            "time": 1000,
            "orderId": 321,
        },
        {
            "qty": "3",
            "price": "1.30",
            "quoteQty": "3.90",
            "realizedPnl": "0.75",
            "time": 2000,
            "orderId": 321,
        },
    ]

    summary = BinanceService._summarize_close_trade_group(trades)

    assert summary is not None
    assert summary["close_hash"] == "321"
    assert summary["pnl"] == pytest.approx(1.25)
    assert summary["exit_price"] == pytest.approx(1.22)


@pytest.mark.asyncio
async def test_get_close_trade_summary_prefers_realized_group_over_noise():
    service = BinanceService(testnet=True)
    service.get_user_trades = AsyncMock(
        return_value=[
            {
                "orderId": 100,
                "side": "BUY",
                "positionSide": "BOTH",
                "qty": "10",
                "price": "1.00",
                "quoteQty": "10.00",
                "realizedPnl": "0",
                "time": 100,
            },
            {
                "orderId": 200,
                "side": "SELL",
                "positionSide": "BOTH",
                "qty": "10",
                "price": "1.15",
                "quoteQty": "11.50",
                "realizedPnl": "1.50",
                "time": 200,
            },
            {
                "orderId": 201,
                "side": "SELL",
                "positionSide": "BOTH",
                "qty": "1",
                "price": "1.16",
                "quoteQty": "1.16",
                "realizedPnl": "0",
                "time": 300,
            },
        ]
    )

    summary = await service.get_close_trade_summary(
        "key",
        "secret",
        "ATAUSDT",
        start_time_ms=150,
        expected_close_side="SELL",
        expected_position_side="BOTH",
        entry_order_id="100",
        expected_quantity=10,
    )

    assert summary is not None
    assert summary["close_hash"] == "200"
    assert summary["exit_price"] == pytest.approx(1.15)
    assert summary["pnl"] == pytest.approx(1.5)
