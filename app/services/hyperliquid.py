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

    async def get_top_gainers(self, limit: int = 20) -> list[dict]:
        """Fetch market data, enrich with 24h candles, and return the most
        active symbols sorted by absolute price change * volume score.

        This ensures the pipeline analyses volatile, high-volume symbols
        rather than returning an arbitrary fixed slice of the metadata list.
        """
        try:
            market_data = await self.get_market_data()
            if not market_data:
                return []

            # Enrich with candle data so we can rank
            enriched = await self.enrich_with_candles(market_data)

            # Score each symbol: |price_change_24h%| * log(volume + 1)
            import math

            def _activity_score(sym: dict) -> float:
                candle = sym.get("candle_summary", {})
                change = abs(candle.get("price_change_24h_pct", 0))
                volume = candle.get("total_volume_24h", 0)
                volatility = abs(candle.get("volatility_pct", 0))
                # Combine change, volatility and volume into a single score
                return (change + volatility) * math.log1p(volume)

            enriched.sort(key=_activity_score, reverse=True)
            return enriched[:limit]
        except Exception as e:
            logger.error(f"Failed to fetch top gainers: {e}")
            return []

    async def enrich_with_candles(self, symbols: list[dict]) -> list[dict]:
        """Add 24h candle summary to each symbol's market data."""
        for sym in symbols:
            coin = sym.get("symbol", "")
            if not coin:
                continue
            try:
                candles = await self.get_candles(coin, interval="1h", hours=24)
                if not candles:
                    continue

                closes = [float(c["c"]) for c in candles]
                opens = [float(c["o"]) for c in candles]
                highs = [float(c["h"]) for c in candles]
                lows = [float(c["l"]) for c in candles]
                volumes = [float(c["v"]) for c in candles]

                high_24h = max(highs)
                low_24h = min(lows)
                open_24h = opens[0]
                close_latest = closes[-1]
                price_change_24h = ((close_latest - open_24h) / open_24h) * 100
                total_volume = sum(volumes)
                avg_volume = total_volume / len(volumes) if volumes else 0

                # Simple volatility: (high - low) / mid as percentage
                volatility = ((high_24h - low_24h) / close_latest) * 100

                # Recent trend: last 6 candles
                recent_closes = closes[-6:]
                trend_up = sum(
                    1
                    for i in range(1, len(recent_closes))
                    if recent_closes[i] > recent_closes[i - 1]
                )
                trend_direction = (
                    "up" if trend_up >= 4 else "down" if trend_up <= 1 else "mixed"
                )

                # Rough RSI approximation (14-period if available)
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
                    "num_candles": len(candles),
                }
            except Exception as e:
                logger.warning(f"Failed to fetch candles for {coin}: {e}")

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

            # Extract tx hash from successful response
            tx_hash = None
            response = order_result.get("response", {})
            if isinstance(response, dict):
                statuses = response.get("data", {}).get("statuses", [])
                if statuses:
                    first = statuses[0]
                    if isinstance(first, dict):
                        tx_hash = first.get("resting", {}).get("oid") or first.get(
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

            return {
                "success": True,
                "data": order_result,
                "tx_hash": tx_hash,
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
