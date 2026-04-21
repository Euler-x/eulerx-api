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
from app.services.bybit import BybitService
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
    """
    result = await db.execute(
        select(Execution)
        .options(
            selectinload(Execution.signal),
            selectinload(Execution.bybit_signal),
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
    signal = execution.signal or execution.bybit_signal
    if not signal:
        raise HTTPException(
            status_code=400, detail="Execution has no associated signal."
        )

    symbol: str = signal.symbol
    quantity: float = float(execution.quantity)
    is_buy: bool = execution.direction == SignalDirection.BUY
    exchange_name: Exchange = getattr(execution, "exchange", Exchange.HYPERLIQUID)
    already_closed_on_exchange = False

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

        # Fetch fills to get exit price and PnL (works for both scenarios)
        fills = await hl.get_user_fills(user.wallet_address)
        close_side = "A" if is_buy else "B"
        entry_time_ms = (
            int(execution.executed_at.timestamp() * 1000)
            if execution.executed_at
            else 0
        )
        exit_price: float | None = None
        close_hash: str | None = None
        closed_pnl: float = 0.0

        for fill in reversed(fills):
            if (
                fill.get("coin") == symbol
                and fill.get("side") == close_side
                and fill.get("time", 0) > entry_time_ms
            ):
                exit_price = float(fill.get("px", 0))
                close_hash = fill.get("hash")
                closed_pnl = float(fill.get("closedPnl", 0))
                break

        if exit_price is None:
            # Fallback: use current mark price from position data if available
            exit_price = float(execution.entry_price)
            logger.warning(
                "No close fill found for execution %s — falling back to entry price",
                execution_id,
            )

        message = (
            f"Position synced — was already closed on HyperLiquid at ${exit_price:.4f}."
            if already_closed_on_exchange
            else f"Position closed on HyperLiquid at ${exit_price:.4f}."
        )

    # ── Bybit ────────────────────────────────────────────────────────────────
    else:
        if not user.bybit_configured:
            raise HTTPException(status_code=400, detail="No Bybit account connected.")

        try:
            api_key = decrypt_private_key(user.bybit_api_key_encrypted)
            api_secret = decrypt_private_key(user.bybit_api_secret_encrypted)
        except Exception:
            raise HTTPException(status_code=500, detail="Failed to decrypt Bybit keys.")

        bybit = BybitService()
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

        # Get actual exit price and PnL from Bybit closed PnL API
        exit_price = float(execution.entry_price)
        closed_pnl = 0.0
        close_hash = None
        try:
            closed_pnl_data = await bybit.get_closed_pnl(api_key, api_secret, symbol)
            if closed_pnl_data:
                exit_price = closed_pnl_data.get("exit_price", exit_price)
                closed_pnl = closed_pnl_data.get("pnl", 0.0)
        except Exception as e:
            logger.warning(
                "Failed to fetch Bybit closed PnL for execution %s: %s", execution_id, e
            )

        message = (
            f"Position synced — was already closed on Bybit at ${exit_price:.4f}."
            if already_closed_on_exchange
            else f"Position closed on Bybit at ${exit_price:.4f}."
        )

    # ── Persist ──────────────────────────────────────────────────────────────
    entry_price_f = float(execution.entry_price)
    if closed_pnl:
        execution.pnl = closed_pnl
    elif is_buy:
        execution.pnl = (exit_price - entry_price_f) * quantity
    else:
        execution.pnl = (entry_price_f - exit_price) * quantity

    execution.exit_price = exit_price
    execution.status = ExecutionStatus.CLOSED
    if close_hash:
        execution.tx_hash = close_hash

    await db.flush()

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
            entry_price=f"{entry_price_f:.4f}",
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
