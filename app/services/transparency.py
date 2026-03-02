import logging

from app.models.strategy import Strategy
from app.services.hyperliquid import HyperliquidService
from app.utils.helpers import utc_now

logger = logging.getLogger(__name__)


class TransparencyService:
    """On-chain transparency data from Hyperliquid."""

    def __init__(self):
        self.hyperliquid = HyperliquidService()

    async def get_proof_of_reserves(
        self,
        wallet_address: str,
        strategies: list[Strategy],
    ) -> dict:
        user_state: dict = {}
        spot_balances: list[dict] = []

        try:
            user_state = await self.hyperliquid.get_user_state(wallet_address)
        except Exception as e:
            logger.error("Failed to fetch perps state for reserves: %s", e)

        try:
            spot_balances = await self.hyperliquid.get_spot_balances(wallet_address)
        except Exception as e:
            logger.error("Failed to fetch spot balances for reserves: %s", e)

        margin_summary = user_state.get("marginSummary", {})
        perps_balance = float(margin_summary.get("accountValue", 0))
        margin_used = float(margin_summary.get("totalMarginUsed", 0))
        free_collateral = float(margin_summary.get("totalRawUsd", 0))

        # Sum all spot balances (USDC, USDE, etc.)
        spot_balance = sum(b["total"] for b in spot_balances)

        # Total on-chain balance = perps + spot
        on_chain_balance = perps_balance + spot_balance

        total_allocated = sum(float(s.capital_allocation) for s in strategies)

        return {
            "on_chain_balance": round(on_chain_balance, 2),
            "perps_balance": round(perps_balance, 2),
            "spot_balance": round(spot_balance, 2),
            "spot_balances": spot_balances,
            "total_allocated": round(total_allocated, 2),
            "margin_used": round(margin_used, 2),
            "free_collateral": round(free_collateral, 2),
            "surplus_deficit": round(on_chain_balance - total_allocated, 2),
            "verification_timestamp": utc_now().isoformat(),
        }

    async def get_live_positions(self, wallet_address: str) -> list[dict]:
        try:
            user_state = await self.hyperliquid.get_user_state(wallet_address)
        except Exception as e:
            logger.error("Failed to fetch positions: %s", e)
            return []

        positions = user_state.get("assetPositions", [])
        result = []
        for p in positions:
            pos = p.get("position", {})
            size = float(pos.get("szi", 0))
            if size == 0:
                continue

            leverage_info = pos.get("leverage", {})
            leverage_val = (
                leverage_info.get("value", 1) if isinstance(leverage_info, dict) else 1
            )

            result.append(
                {
                    "symbol": pos.get("coin", ""),
                    "size": size,
                    "entry_price": float(pos.get("entryPx", 0)),
                    "unrealized_pnl": float(pos.get("unrealizedPnl", 0)),
                    "leverage": float(leverage_val),
                    "liquidation_price": (
                        float(pos["liquidationPx"])
                        if pos.get("liquidationPx")
                        else None
                    ),
                    "margin_used": float(pos.get("marginUsed", 0)),
                    "position_value": float(pos.get("positionValue", 0)),
                }
            )
        return result

    @staticmethod
    def get_explorer_link(wallet_address: str) -> str:
        return f"https://app.hyperliquid.xyz/explorer/address/{wallet_address}"

    @staticmethod
    def get_agent_key_permissions(wallet_type: str) -> dict:
        if wallet_type == "connected":
            return {
                "wallet_type": "connected",
                "can_trade": True,
                "can_withdraw": False,
                "can_transfer": False,
                "can_modify_agent": False,
                "description": (
                    "Agent key can only place and close trades. "
                    "It cannot withdraw funds, transfer assets, or modify account permissions."
                ),
                "verified": True,
            }
        return {
            "wallet_type": "generated",
            "can_trade": True,
            "can_withdraw": True,
            "can_transfer": True,
            "can_modify_agent": True,
            "description": (
                "Generated wallet has full access. "
                "Funds are controlled by the platform-managed key."
            ),
            "verified": True,
        }
