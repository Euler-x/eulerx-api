import asyncio
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.portfolio_snapshot import PortfolioSnapshot
from app.models.user import User
from app.services.bybit import BybitService
from app.services.hyperliquid import HyperliquidService
from app.utils.helpers import utc_now
from app.utils.security import decrypt_private_key

logger = logging.getLogger(__name__)


class PortfolioSnapshotService:
    @staticmethod
    async def _fetch_hyperliquid_snapshot(user: User) -> dict[str, Any] | None:
        if not user.wallet_address:
            return None

        hl = HyperliquidService()

        try:
            user_state, spot_balances = await asyncio.gather(
                hl.get_user_state(user.wallet_address),
                hl.get_spot_balances(user.wallet_address),
            )
            margin_summary = user_state.get("marginSummary", {})
            account_equity = float(margin_summary.get("accountValue", 0))
            available_balance = float(margin_summary.get("totalRawUsd", 0))
            margin_used = float(margin_summary.get("totalMarginUsed", 0))
            unrealized_pnl = 0.0
            open_positions = 0

            for pos_wrapper in user_state.get("assetPositions", []):
                position = pos_wrapper.get("position", {})
                if float(position.get("szi", 0)) == 0:
                    continue
                open_positions += 1
                unrealized_pnl += float(position.get("unrealizedPnl", 0))

            spot_balance = sum(
                float(balance.get("total", 0)) for balance in spot_balances
            )

            return {
                "connected": True,
                "account_equity": round(account_equity, 8),
                "available_balance": round(available_balance, 8),
                "margin_used": round(margin_used, 8),
                "unrealized_pnl": round(unrealized_pnl, 8),
                "spot_balance": round(spot_balance, 8),
                "total_balance": round(account_equity + spot_balance, 8),
                "open_positions": open_positions,
            }
        except Exception as exc:
            logger.warning(
                "Failed to capture Hyperliquid snapshot for user %s: %s",
                user.id,
                exc,
            )
            return {
                "connected": True,
                "error": str(exc),
            }

    @staticmethod
    async def _fetch_bybit_snapshot(user: User) -> dict[str, Any] | None:
        if not user.bybit_configured:
            return None

        try:
            api_key = decrypt_private_key(user.bybit_api_key_encrypted)
            api_secret = decrypt_private_key(user.bybit_api_secret_encrypted)
        except Exception as exc:
            logger.warning(
                "Failed to decrypt Bybit keys for user %s while capturing snapshot: %s",
                user.id,
                exc,
            )
            return {
                "connected": True,
                "error": "Failed to decrypt Bybit credentials",
            }

        bybit = BybitService(testnet=user.bybit_testnet)

        try:
            state, positions = await asyncio.gather(
                bybit.get_user_state(api_key, api_secret),
                bybit.get_user_positions(api_key, api_secret),
            )
            account_equity = float(state.get("totalEquity", 0))
            available_balance = float(state.get("totalAvailableBalance", 0))
            unrealized_pnl = sum(
                float(position.get("unrealized_pnl", 0))
                for position in positions.values()
            )
            return {
                "connected": True,
                "testnet": user.bybit_testnet,
                "account_equity": round(account_equity, 8),
                "available_balance": round(available_balance, 8),
                "unrealized_pnl": round(unrealized_pnl, 8),
                "total_balance": round(account_equity, 8),
                "open_positions": len(positions),
            }
        except Exception as exc:
            logger.warning(
                "Failed to capture Bybit snapshot for user %s: %s",
                user.id,
                exc,
            )
            return {
                "connected": True,
                "testnet": user.bybit_testnet,
                "error": str(exc),
            }

    @classmethod
    async def get_latest_snapshot(
        cls,
        db: AsyncSession,
        user_id,
    ) -> PortfolioSnapshot | None:
        result = await db.execute(
            select(PortfolioSnapshot)
            .where(PortfolioSnapshot.user_id == user_id)
            .order_by(PortfolioSnapshot.captured_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @classmethod
    async def capture_snapshot(
        cls,
        db: AsyncSession,
        user: User,
        *,
        force: bool = False,
        stale_after_minutes: int = 15,
    ) -> PortfolioSnapshot | None:
        latest = await cls.get_latest_snapshot(db, user.id)
        now = utc_now()

        if not force and latest and latest.captured_at:
            latest_captured = latest.captured_at
            if latest_captured.tzinfo is None:
                latest_captured = latest_captured.replace(tzinfo=now.tzinfo)
            if latest_captured >= now - timedelta(minutes=stale_after_minutes):
                return latest

        if not user.wallet_address and not user.bybit_configured:
            return latest

        hl_snapshot, bybit_snapshot = await asyncio.gather(
            cls._fetch_hyperliquid_snapshot(user),
            cls._fetch_bybit_snapshot(user),
        )

        breakdown: dict[str, Any] = {}
        totals = {
            "account_equity": 0.0,
            "available_balance": 0.0,
            "unrealized_pnl": 0.0,
            "total_balance": 0.0,
            "open_positions": 0,
        }

        for exchange_name, snapshot in (
            ("hyperliquid", hl_snapshot),
            ("bybit", bybit_snapshot),
        ):
            if not snapshot:
                continue
            breakdown[exchange_name] = snapshot
            if snapshot.get("error"):
                continue

            totals["account_equity"] += float(snapshot.get("account_equity", 0))
            totals["available_balance"] += float(snapshot.get("available_balance", 0))
            totals["unrealized_pnl"] += float(snapshot.get("unrealized_pnl", 0))
            totals["total_balance"] += float(snapshot.get("total_balance", 0))
            totals["open_positions"] += int(snapshot.get("open_positions", 0))

        if not breakdown:
            return latest

        snapshot = PortfolioSnapshot(
            user_id=user.id,
            account_equity=round(totals["account_equity"], 8),
            available_balance=round(totals["available_balance"], 8),
            unrealized_pnl=round(totals["unrealized_pnl"], 8),
            total_balance=round(totals["total_balance"], 8),
            open_positions=totals["open_positions"],
            exchange_breakdown=breakdown,
            captured_at=now,
        )
        db.add(snapshot)
        await db.flush()
        return snapshot

    @classmethod
    async def capture_all_snapshots(cls, db: AsyncSession) -> int:
        result = await db.execute(
            select(User).where(
                User.is_active == True,  # noqa: E712
                or_(
                    User.wallet_address != None,  # noqa: E711
                    User.bybit_api_key_encrypted != None,  # noqa: E711
                ),
            )
        )
        users = result.scalars().all()
        captured = 0

        for user in users:
            snapshot = await cls.capture_snapshot(
                db,
                user,
                force=True,
                stale_after_minutes=0,
            )
            if snapshot is not None:
                captured += 1

        return captured
