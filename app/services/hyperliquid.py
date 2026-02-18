import logging
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
                symbols.append({
                    "symbol": symbol,
                    "name": asset_info.get("name", ""),
                    "szDecimals": asset_info.get("szDecimals", 0),
                    "mid_price": float(mid_price),
                })
        return symbols

    async def get_top_gainers(self, limit: int = 20) -> list[dict]:
        try:
            market_data = await self.get_market_data()
            return market_data[:limit]
        except Exception as e:
            logger.error(f"Failed to fetch top gainers: {e}")
            return []

    async def get_user_state(self, wallet_address: str) -> dict:
        data = await self._post("/info", {
            "type": "clearinghouseState",
            "user": wallet_address,
        })
        return data

    async def get_open_orders(self, wallet_address: str) -> list[dict]:
        data = await self._post("/info", {
            "type": "openOrders",
            "user": wallet_address,
        })
        return data

    async def place_order(
        self,
        wallet_private_key: str,
        symbol: str,
        is_buy: bool,
        size: float,
        price: float | None = None,
        order_type: str = "market",
        reduce_only: bool = False,
        slippage: float = 0.01,
    ) -> dict:
        """Place an order on Hyperliquid.

        For live trading, this uses the Hyperliquid SDK.
        Currently returns a structured response for the ATE to process.
        """
        try:
            from hyperliquid.utils import constants
            from hyperliquid.exchange import Exchange
            from hyperliquid.info import Info

            base_url = (
                constants.TESTNET_API_URL
                if self.is_testnet
                else constants.MAINNET_API_URL
            )

            info = Info(base_url, skip_ws=True)
            exchange = Exchange(
                wallet=None,
                base_url=base_url,
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

            order_result = exchange.order(
                symbol,
                is_buy,
                size,
                price,
                {"limit": {"tif": "Ioc"}} if order_type == "market" else {"limit": {"tif": "Gtc"}},
            )

            return {
                "success": True,
                "data": order_result,
                "tx_hash": order_result.get("response", {}).get("data", {}).get("statuses", [{}])[0].get("resting", {}).get("oid"),
            }

        except ImportError:
            logger.warning("Hyperliquid SDK not fully configured. Returning mock response.")
            return {
                "success": False,
                "error": "Hyperliquid SDK not configured for live trading",
                "mock": True,
            }
        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            return {"success": False, "error": str(e)}

    async def close_position(
        self,
        wallet_private_key: str,
        symbol: str,
        size: float,
        is_buy: bool,
    ) -> dict:
        return await self.place_order(
            wallet_private_key=wallet_private_key,
            symbol=symbol,
            is_buy=not is_buy,
            size=size,
            order_type="market",
            reduce_only=True,
        )

    async def get_account_value(self, wallet_address: str) -> float:
        try:
            state = await self.get_user_state(wallet_address)
            return float(state.get("marginSummary", {}).get("accountValue", 0))
        except Exception as e:
            logger.error(f"Failed to get account value: {e}")
            return 0.0
