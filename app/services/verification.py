import logging
from typing import Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import TransactionCategory, TransactionStatus
from app.models.execution import Execution
from app.models.transaction import Transaction

logger = logging.getLogger(__name__)

HYPERLIQUID_EXPLORER_BASE = "https://app.hyperliquid.xyz/explorer/tx/"


class VerificationService:
    @staticmethod
    def get_verification_link(tx_hash: str) -> str:
        return f"{HYPERLIQUID_EXPLORER_BASE}{tx_hash}"

    @staticmethod
    async def log_execution_transaction(
        db: AsyncSession,
        execution: Execution,
        amount: float,
        asset: str,
    ) -> Transaction:
        verification_link = None
        if execution.tx_hash:
            verification_link = VerificationService.get_verification_link(
                execution.tx_hash
            )

        transaction = Transaction(
            user_id=execution.user_id,
            category=TransactionCategory.EXECUTION,
            amount=amount,
            asset=asset,
            wallet_address_hash=execution.wallet_address_hash,
            status=TransactionStatus.CONFIRMED
            if execution.tx_hash
            else TransactionStatus.PENDING,
            verification_link=verification_link,
            tx_hash=execution.tx_hash,
            description=f"{execution.direction.value.upper()} {asset} - {execution.order_type.value}",
        )
        db.add(transaction)
        return transaction

    @staticmethod
    async def verify_on_chain(tx_hash: str) -> dict:
        """Verify a transaction exists on-chain via the Hyperliquid explorer."""
        if not tx_hash:
            return {"verified": False, "error": "No transaction hash provided"}

        url = VerificationService.get_verification_link(tx_hash)
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url)
                verified = response.status_code == 200
                return {
                    "verified": verified,
                    "tx_hash": tx_hash,
                    "verification_link": url,
                }
        except Exception as e:
            logger.error("On-chain verification failed for %s: %s", tx_hash, e)
            return {
                "verified": False,
                "tx_hash": tx_hash,
                "verification_link": url,
                "error": str(e),
            }
