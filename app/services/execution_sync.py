import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.models.binance_signal import BinanceSignal
from app.models.bybit_signal import BybitSignal
from app.models.enums import ExecutionStatus, SignalDirection
from app.models.execution import Execution
from app.models.signal import Signal
from app.services.binance import BinanceService
from app.services.bybit import BybitService
from app.services.hyperliquid import HyperliquidService
from app.utils.helpers import utc_now

logger = logging.getLogger(__name__)


class ExecutionSyncService:
    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _normalize_datetime(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @classmethod
    def get_signal(
        cls, execution: Execution
    ) -> Signal | BybitSignal | BinanceSignal | None:
        return execution.signal or execution.bybit_signal or execution.binance_signal

    @classmethod
    def get_execution_opened_at(cls, execution: Execution) -> datetime:
        opened_at = (
            cls._normalize_datetime(execution.executed_at)
            or cls._normalize_datetime(execution.created_at)
            or utc_now()
        )
        return opened_at

    @classmethod
    def derive_close_reason(
        cls,
        execution: Execution,
        signal: Signal | BybitSignal | BinanceSignal | None,
        exit_price: float,
        manual_close: bool = False,
    ) -> str:
        if manual_close:
            return "manual"

        if signal is None:
            return "closed"

        is_buy = execution.direction == SignalDirection.BUY
        tp_price = cls._safe_float(getattr(signal, "take_profit", None), 0.0)
        sl_price = cls._safe_float(getattr(signal, "stop_loss", None), 0.0)

        if tp_price > 0 and (
            (is_buy and exit_price >= tp_price * 0.99)
            or (not is_buy and exit_price <= tp_price * 1.01)
        ):
            return "take_profit"

        if sl_price > 0 and (
            (is_buy and exit_price <= sl_price * 1.01)
            or (not is_buy and exit_price >= sl_price * 0.99)
        ):
            return "stop_loss"

        return "closed"

    @classmethod
    def apply_close_details(
        cls,
        execution: Execution,
        signal: Signal | BybitSignal | BinanceSignal | None,
        *,
        exit_price: float,
        closed_pnl: float = 0.0,
        close_hash: str | None = None,
        closed_at: datetime | None = None,
        close_source: str = "exchange_sync",
        close_reason: str | None = None,
        manual_close: bool = False,
    ) -> dict[str, Any]:
        entry_price = float(execution.entry_price)
        quantity = float(execution.quantity)
        is_buy = execution.direction == SignalDirection.BUY
        normalized_closed_at = cls._normalize_datetime(closed_at) or utc_now()

        if closed_pnl:
            pnl_value = closed_pnl
        elif is_buy:
            pnl_value = (exit_price - entry_price) * quantity
        else:
            pnl_value = (entry_price - exit_price) * quantity

        execution.pnl = pnl_value
        execution.exit_price = exit_price
        execution.status = ExecutionStatus.CLOSED
        execution.closed_at = normalized_closed_at
        execution.close_source = close_source
        execution.close_reason = close_reason or cls.derive_close_reason(
            execution,
            signal,
            exit_price,
            manual_close=manual_close,
        )
        if close_hash:
            execution.tx_hash = close_hash

        return {
            "execution_id": str(execution.id),
            "symbol": getattr(signal, "symbol", "unknown"),
            "triggered": execution.close_reason or "closed",
            "entry_price": entry_price,
            "exit_price": exit_price,
            "pnl": round(float(pnl_value), 8),
            "tx_hash": close_hash,
            "direction": execution.direction.value,
            "closed_at": normalized_closed_at.isoformat(),
            "exchange": getattr(execution.exchange, "value", str(execution.exchange)),
        }

    @classmethod
    def find_hyperliquid_close_fill(
        cls,
        execution: Execution,
        fills: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Find the fill that closed this execution.

        Uses a two-pass strategy:
          Pass 1 — Standard matching: prefer fills on the close side with
                   closedPnl set, within 30 minutes of execution open.
          Pass 2 — Relaxed matching: any fill for the symbol after execution
                   time that has a non-zero closedPnl (catches TP/SL fills
                   that may not match the expected side exactly).
        """
        signal = cls.get_signal(execution)
        if signal is None:
            return None

        symbol = signal.symbol
        close_side = "A" if execution.direction == SignalDirection.BUY else "B"
        opened_at = cls.get_execution_opened_at(execution)
        # Use a 30-minute buffer to handle delayed settlement
        min_time_ms = int((opened_at - timedelta(minutes=30)).timestamp() * 1000)

        # ── Pass 1: Standard scored matching ──────────────────────────
        candidates: list[tuple[int, dict[str, Any]]] = []
        for fill in fills:
            if fill.get("coin") != symbol:
                continue

            fill_time_ms = int(fill.get("time") or 0)
            if fill_time_ms and fill_time_ms < min_time_ms:
                continue

            score = fill_time_ms
            if fill.get("side") == close_side:
                score += 10_000_000_000_000
            closed_pnl_val = fill.get("closedPnl")
            if closed_pnl_val is not None and cls._safe_float(closed_pnl_val) != 0.0:
                score += 5_000_000_000_000
            elif closed_pnl_val is not None:
                score += 2_000_000_000_000  # closedPnl present but zero
            if fill.get("hash"):
                score += 1_000_000_000_000

            candidates.append((score, fill))

        # ── Pass 2: Relaxed matching (closedPnl-based) ────────────────
        # If no candidates from pass 1, accept any fill for the symbol
        # that has a non-zero closedPnl (TP/SL closes often have this).
        if not candidates:
            for fill in fills:
                if fill.get("coin") != symbol:
                    continue
                fill_time_ms = int(fill.get("time") or 0)
                if fill_time_ms and fill_time_ms < min_time_ms:
                    continue
                closed_pnl_val = fill.get("closedPnl")
                if (
                    closed_pnl_val is not None
                    and cls._safe_float(closed_pnl_val) != 0.0
                ):
                    candidates.append((fill_time_ms, fill))

        if not candidates:
            return None

        _, best_fill = max(candidates, key=lambda item: item[0])
        exit_price = cls._safe_float(best_fill.get("px"), 0.0)
        if exit_price <= 0:
            return None

        fill_time_ms = int(best_fill.get("time") or 0)
        closed_at = (
            datetime.fromtimestamp(fill_time_ms / 1000, tz=timezone.utc)
            if fill_time_ms
            else utc_now()
        )
        return {
            "exit_price": exit_price,
            "closed_pnl": cls._safe_float(best_fill.get("closedPnl"), 0.0),
            "close_hash": best_fill.get("hash"),
            "closed_at": closed_at,
            "raw": best_fill,
        }

    @classmethod
    async def wait_for_hyperliquid_close(
        cls,
        hl: HyperliquidService,
        execution: Execution,
        wallet_address: str,
        *,
        attempts: int = 6,
        delay_seconds: float = 2.0,
    ) -> dict[str, Any] | None:
        signal = cls.get_signal(execution)
        if signal is None:
            return None

        symbol = signal.symbol
        for attempt in range(attempts):
            positions, fills = await asyncio.gather(
                hl.get_user_positions(wallet_address),
                hl.get_user_fills(wallet_address, limit=500),
            )
            close_data = cls.find_hyperliquid_close_fill(execution, fills)
            if close_data and positions.get(symbol) is None:
                return close_data

            if attempt < attempts - 1:
                await asyncio.sleep(delay_seconds)

        return None

    @classmethod
    async def wait_for_bybit_close(
        cls,
        bybit: BybitService,
        execution: Execution,
        api_key: str,
        api_secret: str,
        *,
        attempts: int = 6,
        delay_seconds: float = 2.0,
    ) -> dict[str, Any] | None:
        signal = cls.get_signal(execution)
        if signal is None:
            return None

        symbol = signal.symbol
        opened_at_ms = int(cls.get_execution_opened_at(execution).timestamp() * 1000)

        for attempt in range(attempts):
            positions, closed_pnl = await asyncio.gather(
                bybit.get_user_positions(api_key, api_secret),
                bybit.get_closed_pnl(
                    api_key,
                    api_secret,
                    symbol,
                    start_time_ms=opened_at_ms,
                    limit=20,
                ),
            )
            if closed_pnl and positions.get(symbol) is None:
                return closed_pnl

            if attempt < attempts - 1:
                await asyncio.sleep(delay_seconds)

        return None

    @classmethod
    async def wait_for_binance_close(
        cls,
        binance: BinanceService,
        execution: Execution,
        api_key: str,
        api_secret: str,
        *,
        position_side: str = "BOTH",
        attempts: int = 6,
        delay_seconds: float = 2.0,
    ) -> dict[str, Any] | None:
        signal = cls.get_signal(execution)
        if signal is None:
            return None

        symbol = signal.symbol
        opened_at_ms = int(cls.get_execution_opened_at(execution).timestamp() * 1000)
        expected_close_side = (
            "SELL" if execution.direction == SignalDirection.BUY else "BUY"
        )

        for attempt in range(attempts):
            positions, close_trade = await asyncio.gather(
                binance.get_user_positions(api_key, api_secret),
                binance.get_close_trade_summary(
                    api_key,
                    api_secret,
                    symbol,
                    start_time_ms=opened_at_ms,
                    expected_close_side=expected_close_side,
                    expected_position_side=position_side,
                    entry_order_id=execution.exchange_order_id,
                    expected_quantity=cls._safe_float(execution.quantity),
                    limit=100,
                ),
            )
            if close_trade and positions.get(symbol) is None:
                return close_trade

            if attempt < attempts - 1:
                await asyncio.sleep(delay_seconds)

        return None
