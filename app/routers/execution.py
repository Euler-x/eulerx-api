import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.base import get_db
from app.middleware.permissions import RequireVerified, UserPermissions
from app.models.enums import Exchange, ExecutionStatus, SignalDirection
from app.models.execution import Execution
from app.models.schemas.common import PaginatedResponse
from app.models.schemas.execution import (
    CloseExecutionResponse,
    ExecutionResponse,
    ExecutionVerifyResponse,
)
from app.services.binance import BinanceService
from app.services.bybit import BybitService
from app.services.execution_sync import ExecutionSyncService
from app.services.hyperliquid import HyperliquidService
from app.services.notifications import NotificationService
from app.services.verification import VerificationService
from app.utils.security import decrypt_private_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/executions", tags=["Executions"])


@router.get("", response_model=PaginatedResponse)
async def list_executions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: ExecutionStatus | None = None,
    exchange: str | None = Query(default=None),
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(Execution)
        .where(Execution.user_id == perms.id)
        .order_by(Execution.created_at.desc())
    )
    count_query = select(func.count(Execution.id)).where(Execution.user_id == perms.id)

    if status:
        query = query.where(Execution.status == status)
        count_query = count_query.where(Execution.status == status)
    else:
        query = query.where(Execution.status != ExecutionStatus.FAILED)
        count_query = count_query.where(Execution.status != ExecutionStatus.FAILED)

    if exchange:
        query = query.where(Execution.exchange == exchange)
        count_query = count_query.where(Execution.exchange == exchange)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    executions = result.scalars().all()

    return PaginatedResponse(
        items=[ExecutionResponse.model_validate(e) for e in executions],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


@router.get("/{execution_id}", response_model=ExecutionResponse)
async def get_execution(
    execution_id: uuid.UUID,
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Execution).where(
            Execution.id == execution_id,
            Execution.user_id == perms.id,
        )
    )
    execution = result.scalar_one_or_none()
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    return ExecutionResponse.model_validate(execution)


@router.get("/{execution_id}/verify", response_model=ExecutionVerifyResponse)
async def verify_execution(
    execution_id: uuid.UUID,
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Execution).where(
            Execution.id == execution_id,
            Execution.user_id == perms.id,
        )
    )
    execution = result.scalar_one_or_none()
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")

    if not execution.tx_hash:
        return ExecutionVerifyResponse(
            execution_id=execution.id,
            tx_hash=None,
            verified=False,
            verification_link=None,
        )

    verification = await VerificationService.verify_on_chain(execution.tx_hash)

    return ExecutionVerifyResponse(
        execution_id=execution.id,
        tx_hash=execution.tx_hash,
        verified=verification.get("verified", False),
        verification_link=verification.get("verification_link"),
    )


@router.post("/{execution_id}/close", response_model=CloseExecutionResponse)
async def close_execution(
    execution_id: uuid.UUID,
    perms: UserPermissions = RequireVerified,
    db: AsyncSession = Depends(get_db),
):
    """Manually close an open position.

    Handles two scenarios:
    1. Position is still open on the exchange → places a market close order then syncs.
    2. Position was already closed on the exchange but EulerX still shows it as open
       (e.g. user closed it on the exchange UI) → reconciles exit data and marks closed.

    Uses ExecutionSyncService for consistent close data recording including
    exit_price, PnL, closed_at, close_reason, and close_source.
    """
    result = await db.execute(
        select(Execution)
        .options(
            selectinload(Execution.signal),
            selectinload(Execution.bybit_signal),
            selectinload(Execution.binance_signal),
            selectinload(Execution.user),
            selectinload(Execution.strategy),
        )
        .where(
            Execution.id == execution_id,
            Execution.user_id == perms.id,
        )
    )
    execution = result.scalar_one_or_none()
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")

    if execution.status != ExecutionStatus.FILLED:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot close execution with status '{execution.status.value}'. Only FILLED positions can be closed.",
        )

    user = execution.user
    signal = execution.signal or execution.bybit_signal or execution.binance_signal
    if not signal:
        raise HTTPException(
            status_code=400, detail="Execution has no associated signal."
        )

    symbol: str = signal.symbol
    quantity: float = float(execution.quantity)
    is_buy: bool = execution.direction == SignalDirection.BUY
    exchange_name: Exchange = getattr(execution, "exchange", Exchange.HYPERLIQUID)
    already_closed_on_exchange = False
    close_data: dict | None = None

    # ── HyperLiquid ──────────────────────────────────────────────────────────
    if exchange_name == Exchange.HYPERLIQUID:
        if not user.wallet_address or not user.encrypted_private_key:
            raise HTTPException(
                status_code=400, detail="No HyperLiquid wallet connected."
            )

        try:
            private_key = decrypt_private_key(user.encrypted_private_key)
        except Exception:
            raise HTTPException(status_code=500, detail="Failed to decrypt wallet key.")

        hl = HyperliquidService()
        positions = await hl.get_user_positions(user.wallet_address)

        if positions.get(symbol) is not None:
            # Position is still open — close it on exchange
            close_result = await hl.close_position(
                wallet_private_key=private_key,
                symbol=symbol,
                size=quantity,
                is_buy=is_buy,
                account_address=user.wallet_address,
            )
            if not close_result.get("success"):
                raise HTTPException(
                    status_code=502,
                    detail=f"Exchange rejected close order: {close_result.get('error', 'unknown error')}",
                )
            logger.info(
                "Manual close placed on HyperLiquid for execution %s", execution_id
            )
        else:
            already_closed_on_exchange = True
            logger.info(
                "Execution %s already closed on HyperLiquid — syncing record",
                execution_id,
            )

        # Wait for fill settlement — polls for real close data
        close_data = await ExecutionSyncService.wait_for_hyperliquid_close(
            hl,
            execution,
            user.wallet_address,
            attempts=8,
            delay_seconds=1.5,
        )

        # Fallback: if wait times out, try one more extended fills lookup
        if close_data is None:
            extended_fills = await hl.get_user_fills(user.wallet_address, limit=2000)
            close_data = ExecutionSyncService.find_hyperliquid_close_fill(
                execution, extended_fills
            )

        # Last resort: use current mid price
        if close_data is None:
            logger.warning(
                "No fill data found for manual close of execution %s — "
                "using current mid price",
                execution_id,
            )
            try:
                all_mids = await hl.get_all_mids()
                mid_price = float(all_mids.get(symbol, 0))
            except Exception:
                mid_price = 0.0

            if mid_price > 0:
                entry_price_f = float(execution.entry_price)
                pnl = (
                    (mid_price - entry_price_f) * quantity
                    if is_buy
                    else (entry_price_f - mid_price) * quantity
                )
                close_data = {
                    "exit_price": mid_price,
                    "pnl": pnl,
                    "close_hash": None,
                    "closed_at": None,
                }
            else:
                close_data = {
                    "exit_price": float(execution.entry_price),
                    "pnl": 0.0,
                    "close_hash": None,
                    "closed_at": None,
                }

    # ── Bybit ────────────────────────────────────────────────────────────────
    elif exchange_name == Exchange.BINANCE:
        if not user.binance_configured:
            raise HTTPException(status_code=400, detail="No Binance account connected.")

        try:
            api_key = decrypt_private_key(user.binance_api_key_encrypted)
            api_secret = decrypt_private_key(user.binance_api_secret_encrypted)
        except Exception:
            raise HTTPException(
                status_code=500, detail="Failed to decrypt Binance keys."
            )

        binance = BinanceService(testnet=getattr(user, "binance_testnet", False))
        hedge_mode = await binance.get_position_mode(api_key, api_secret)
        position_side = ("LONG" if is_buy else "SHORT") if hedge_mode else "BOTH"
        positions = await binance.get_user_positions(api_key, api_secret)

        if positions.get(symbol) is not None:
            close_result = await binance.close_position(
                api_key=api_key,
                api_secret=api_secret,
                symbol=symbol,
                size=quantity,
                is_buy=is_buy,
                position_side=position_side,
            )
            if not close_result.get("success"):
                raise HTTPException(
                    status_code=502,
                    detail=f"Exchange rejected close order: {close_result.get('error', 'unknown error')}",
                )
            logger.info("Manual close placed on Binance for execution %s", execution_id)
        else:
            already_closed_on_exchange = True
            logger.info(
                "Execution %s already closed on Binance â€” syncing record",
                execution_id,
            )

        close_data = await ExecutionSyncService.wait_for_binance_close(
            binance,
            execution,
            api_key,
            api_secret,
            position_side=position_side,
            attempts=8,
            delay_seconds=1.5,
        )

        if close_data is None:
            try:
                opened_at_ms = int(
                    ExecutionSyncService.get_execution_opened_at(execution).timestamp()
                    * 1000
                )
                close_data = await binance.get_close_trade_summary(
                    api_key,
                    api_secret,
                    symbol,
                    start_time_ms=opened_at_ms,
                    expected_close_side="SELL" if is_buy else "BUY",
                    expected_position_side=position_side,
                    entry_order_id=execution.exchange_order_id,
                    expected_quantity=quantity,
                    limit=100,
                )
            except Exception as e:
                logger.warning(
                    "Failed to fetch Binance close trades for execution %s: %s",
                    execution_id,
                    e,
                )

        if close_data is None:
            try:
                opened_at_ms = int(
                    ExecutionSyncService.get_execution_opened_at(execution).timestamp()
                    * 1000
                )
                pnl_data = await binance.get_closed_pnl(
                    api_key, api_secret, symbol, start_time_ms=opened_at_ms, limit=20
                )
                if pnl_data:
                    close_data = pnl_data
            except Exception as e:
                logger.warning(
                    "Failed to fetch Binance closed PnL for execution %s: %s",
                    execution_id,
                    e,
                )

        if close_data is None:
            try:
                ticker = await binance.get_ticker(symbol)
                bid = float(ticker.get("bidPrice") or 0) if ticker else 0.0
                ask = float(ticker.get("askPrice") or 0) if ticker else 0.0
                mid = (
                    (bid + ask) / 2
                    if bid > 0 and ask > 0
                    else float(ticker.get("lastPrice") or 0)
                    if ticker
                    else 0.0
                )
            except Exception:
                mid = 0.0

            if mid > 0:
                entry_price_f = float(execution.entry_price)
                pnl = (
                    (mid - entry_price_f) * quantity
                    if is_buy
                    else (entry_price_f - mid) * quantity
                )
                close_data = {
                    "exit_price": mid,
                    "pnl": pnl,
                    "close_hash": None,
                    "closed_at": None,
                }
            else:
                close_data = {
                    "exit_price": float(execution.entry_price),
                    "pnl": 0.0,
                    "close_hash": None,
                    "closed_at": None,
                }

    elif exchange_name == Exchange.BYBIT:
        if not user.bybit_configured:
            raise HTTPException(status_code=400, detail="No Bybit account connected.")

        try:
            api_key = decrypt_private_key(user.bybit_api_key_encrypted)
            api_secret = decrypt_private_key(user.bybit_api_secret_encrypted)
        except Exception:
            raise HTTPException(status_code=500, detail="Failed to decrypt Bybit keys.")

        bybit = BybitService(testnet=getattr(user, "bybit_testnet", False))
        positions = await bybit.get_user_positions(api_key, api_secret)

        if positions.get(symbol) is not None:
            close_result = await bybit.close_position(
                api_key=api_key,
                api_secret=api_secret,
                symbol=symbol,
                size=quantity,
                is_buy=is_buy,
            )
            if not close_result.get("success"):
                raise HTTPException(
                    status_code=502,
                    detail=f"Exchange rejected close order: {close_result.get('error', 'unknown error')}",
                )
            logger.info("Manual close placed on Bybit for execution %s", execution_id)
        else:
            already_closed_on_exchange = True
            logger.info(
                "Execution %s already closed on Bybit — syncing record", execution_id
            )

        # Wait for fill settlement
        close_data = await ExecutionSyncService.wait_for_bybit_close(
            bybit,
            execution,
            api_key,
            api_secret,
            attempts=8,
            delay_seconds=1.5,
        )

        # Fallback: direct closed PnL lookup
        if close_data is None:
            try:
                opened_at_ms = int(
                    ExecutionSyncService.get_execution_opened_at(execution).timestamp()
                    * 1000
                )
                pnl_data = await bybit.get_closed_pnl(
                    api_key, api_secret, symbol, start_time_ms=opened_at_ms, limit=20
                )
                if pnl_data:
                    close_data = pnl_data
            except Exception as e:
                logger.warning(
                    "Failed to fetch Bybit closed PnL for execution %s: %s",
                    execution_id,
                    e,
                )

        if close_data is None:
            try:
                ticker = await bybit.get_ticker(symbol)
                bid = float(ticker.get("bid1Price") or 0) if ticker else 0.0
                ask = float(ticker.get("ask1Price") or 0) if ticker else 0.0
                mid = (
                    (bid + ask) / 2
                    if bid > 0 and ask > 0
                    else float(ticker.get("lastPrice") or 0)
                    if ticker
                    else 0.0
                )
            except Exception:
                mid = 0.0

            if mid > 0:
                entry_price_f = float(execution.entry_price)
                pnl = (
                    (mid - entry_price_f) * quantity
                    if is_buy
                    else (entry_price_f - mid) * quantity
                )
                close_data = {
                    "exit_price": mid,
                    "pnl": pnl,
                    "close_hash": None,
                    "closed_at": None,
                }

        # Last resort
        if close_data is None:
            close_data = {
                "exit_price": float(execution.entry_price),
                "pnl": 0.0,
                "close_hash": None,
                "closed_at": None,
            }

    # ── Persist via centralized sync service ─────────────────────────────────
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported exchange for manual close: {exchange_name.value}",
        )

    exit_price = close_data.get("exit_price", float(execution.entry_price))
    closed_pnl = close_data.get("pnl", close_data.get("closed_pnl", 0.0))
    close_hash = close_data.get("close_hash")
    closed_at = close_data.get("closed_at")

    ExecutionSyncService.apply_close_details(
        execution,
        signal,
        exit_price=exit_price,
        closed_pnl=closed_pnl,
        close_hash=close_hash,
        closed_at=closed_at,
        close_source="manual",
        manual_close=True,
    )

    await db.flush()

    message = (
        f"Position synced — was already closed on {exchange_name.value} at ${exit_price:.4f}."
        if already_closed_on_exchange
        else f"Position closed on {exchange_name.value} at ${exit_price:.4f}."
    )

    # Send notification (non-blocking — failure must not affect the response)
    try:
        notification_service = NotificationService()
        pnl_str = f"{float(execution.pnl):+.4f}"
        strategy_name = execution.strategy.name if execution.strategy else "Unknown"
        direction_str = "buy" if is_buy else "sell"
        exchange_str = exchange_name.value
        await notification_service.send_take_profit_hit(
            user=user,
            symbol=symbol,
            direction=direction_str,
            entry_price=f"{float(execution.entry_price):.4f}",
            exit_price=f"{exit_price:.4f}",
            pnl=pnl_str,
            strategy_name=f"{strategy_name} (Manual Close)",
            exchange=exchange_str,
        )
    except Exception as e:
        logger.warning(
            "Failed to send close notification for execution %s: %s", execution_id, e
        )

    return CloseExecutionResponse(
        execution=ExecutionResponse.model_validate(execution),
        message=message,
        already_closed_on_exchange=already_closed_on_exchange,
    )
