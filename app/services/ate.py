import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models.enums import ExecutionStatus, OrderType, SignalDirection, SignalStatus
from app.models.execution import Execution
from app.models.signal import Signal
from app.models.strategy import Strategy
from app.models.user import User
from app.services.hyperliquid import HyperliquidService
from app.services.notifications import NotificationService
from app.services.verification import VerificationService
from app.utils.helpers import utc_now
from app.utils.rate_limiter import InMemoryRateLimiter
from app.utils.security import decrypt_private_key

logger = logging.getLogger(__name__)
settings = get_settings()

ate_rate_limiter = InMemoryRateLimiter(
    max_requests=settings.ate_max_executions_per_hour,
    window_seconds=3600,
)


class ATEService:
    def __init__(self):
        self.hyperliquid = HyperliquidService()

    def evaluate_signal(
        self,
        signal: Signal,
        strategy: Strategy,
        open_position_count: int,
    ) -> tuple[bool, str]:
        if signal.direction == SignalDirection.HOLD:
            return False, "Signal is HOLD"

        if signal.confidence < settings.ate_confidence_threshold:
            return (
                False,
                f"Confidence {signal.confidence} below threshold {settings.ate_confidence_threshold}",
            )

        if signal.status == SignalStatus.EXPIRED:
            return False, "Signal has expired"

        if signal.expires_at and signal.expires_at < utc_now():
            return False, "Signal has expired"

        if open_position_count >= strategy.max_positions:
            return False, f"Max positions ({strategy.max_positions}) reached"

        return True, "Signal approved for execution"

    def calculate_position_size(
        self,
        strategy: Strategy,
        entry_price: float,
        available_balance: float | None = None,
        sz_decimals: int = 4,
    ) -> float:
        """Calculate position size based on wallet balance percentage.

        Uses strategy.allocation_pct (1-100%) of the user's available wallet
        balance to determine effective capital for position sizing.
        """
        if available_balance is None or available_balance <= 0:
            return 0.0

        # allocation_pct is 1-100, representing percentage of wallet balance
        effective_capital = available_balance * (strategy.allocation_pct / 100)
        leverage = min(strategy.leverage_limit, settings.ate_default_leverage)

        logger.info(
            "Position sizing: allocation_pct=%.1f%%, wallet_balance=$%.2f, "
            "effective=$%.2f",
            strategy.allocation_pct,
            available_balance,
            effective_capital,
        )

        risk_multiplier = {
            "low": 0.02,
            "medium": 0.05,
            "high": 0.10,
        }.get(strategy.risk_profile.value, 0.02)

        position_value = effective_capital * risk_multiplier * leverage
        if entry_price <= 0:
            return 0.0

        quantity = position_value / entry_price

        # Round to asset's szDecimals precision
        quantity = round(quantity, sz_decimals)

        # HyperLiquid minimum notional is ~$10
        notional = quantity * entry_price
        if notional < 10.0:
            quantity = round(10.5 / entry_price, sz_decimals)
            logger.info(
                "Notional $%.2f below $10 minimum, adjusted qty to %s",
                notional,
                quantity,
            )

        return quantity

    async def _get_wallet_balance(self, user: User) -> float | None:
        """Fetch the user's available perps margin from HyperLiquid.

        Returns available balance or None if the wallet has no perps margin.
        Also checks spot balances and logs a helpful message if funds are
        only in spot.
        """
        if not user.wallet_address:
            return None

        try:
            state = await self.hyperliquid.get_user_state(user.wallet_address)
            margin_summary = state.get("marginSummary", {})
            account_value = float(margin_summary.get("accountValue", 0))
            total_margin_used = float(margin_summary.get("totalMarginUsed", 0))
            available = account_value - total_margin_used

            if available <= 0:
                # Check if user has spot funds but no perps margin
                spot_balances = await self.hyperliquid.get_spot_balances(
                    user.wallet_address
                )
                spot_total = sum(b.get("total", 0) for b in spot_balances)
                if spot_total > 0:
                    logger.warning(
                        "User %s has $%.2f in spot but $0 in perps margin. "
                        "Funds need to be transferred to perps to trade.",
                        user.id,
                        spot_total,
                    )
                return None

            logger.info(
                "User %s wallet balance: account=$%.2f, margin_used=$%.2f, "
                "available=$%.2f",
                user.id,
                account_value,
                total_margin_used,
                available,
            )
            return available

        except Exception as e:
            logger.error("Failed to fetch wallet balance for user %s: %s", user.id, e)
            return None

    async def _get_sz_decimals(self, symbol: str) -> int:
        """Get the size decimals for a symbol from HyperLiquid meta."""
        try:
            meta = await self.hyperliquid.get_meta()
            for asset in meta.get("universe", []):
                if asset.get("name") == symbol:
                    return asset.get("szDecimals", 4)
        except Exception as e:
            logger.warning("Failed to fetch szDecimals for %s: %s", symbol, e)
        return 4  # safe default

    def _create_failed_execution(
        self,
        signal: Signal,
        user: User,
        strategy: Strategy,
        error_message: str,
        entry_price: float | None = None,
    ) -> Execution:
        """Create a FAILED execution record with an error message."""
        return Execution(
            signal_id=signal.id,
            user_id=user.id,
            strategy_id=strategy.id,
            wallet_address_hash=user.wallet_address_hash or "",
            order_type=OrderType.MARKET,
            direction=signal.direction,
            entry_price=entry_price or float(signal.entry_price),
            quantity=0,
            leverage=min(strategy.leverage_limit, settings.ate_default_leverage),
            status=ExecutionStatus.FAILED,
            error_message=error_message[:500],
        )

    async def execute_signal(
        self,
        db: AsyncSession,
        signal: Signal,
        strategy: Strategy,
        user: User,
    ) -> Execution | None:
        rate_key = f"ate:{user.id}"
        if not ate_rate_limiter.check(rate_key):
            logger.warning("ATE rate limit reached for user %s", user.id)
            return None

        # ── Per-(signal, strategy) idempotency ───────────────────────
        # Signals are shared across strategies. Prevent the same strategy
        # from executing the same signal twice.
        existing = await db.execute(
            select(func.count(Execution.id)).where(
                Execution.signal_id == signal.id,
                Execution.strategy_id == strategy.id,
            )
        )
        if (existing.scalar() or 0) > 0:
            logger.info(
                "Signal %s already executed by strategy %s, skipping",
                signal.id,
                strategy.id,
            )
            return None

        # ── Pre-execution wallet checks ──────────────────────────────
        if not user.wallet_address:
            logger.warning("User %s has no wallet connected, skipping", user.id)
            execution = self._create_failed_execution(
                signal, user, strategy, "No wallet connected"
            )
            db.add(execution)
            await db.flush()
            return execution

        if not user.encrypted_private_key:
            logger.warning("User %s has no private key stored, skipping", user.id)
            execution = self._create_failed_execution(
                signal, user, strategy, "No trading key configured"
            )
            db.add(execution)
            await db.flush()
            return execution

        # Check wallet has perps margin
        available_balance = await self._get_wallet_balance(user)
        if available_balance is None or available_balance < 1.0:
            balance_str = f"${available_balance:.2f}" if available_balance else "$0.00"
            logger.warning(
                "User %s has insufficient perps margin ($%.2f), skipping execution",
                user.id,
                available_balance or 0,
            )
            execution = self._create_failed_execution(
                signal,
                user,
                strategy,
                f"Insufficient perps margin ({balance_str}). Transfer funds to perps to trade.",
            )
            db.add(execution)
            await db.flush()
            return execution

        # Count open positions for this strategy
        result = await db.execute(
            select(func.count(Execution.id)).where(
                Execution.user_id == user.id,
                Execution.strategy_id == strategy.id,
                Execution.status.in_(
                    [
                        ExecutionStatus.PENDING,
                        ExecutionStatus.FILLED,
                    ]
                ),
            )
        )
        open_count = result.scalar() or 0

        can_execute, reason = self.evaluate_signal(signal, strategy, open_count)
        if not can_execute:
            logger.info("Signal %s rejected: %s", signal.id, reason)
            execution = self._create_failed_execution(signal, user, strategy, reason)
            db.add(execution)
            await db.flush()
            return execution

        # ── Position sizing with real balance + szDecimals ───────────
        entry_price = float(signal.entry_price)
        sz_decimals = await self._get_sz_decimals(signal.symbol)
        quantity = self.calculate_position_size(
            strategy, entry_price, available_balance, sz_decimals
        )
        if quantity <= 0:
            logger.warning("Calculated position size is 0 for signal %s", signal.id)
            execution = self._create_failed_execution(
                signal,
                user,
                strategy,
                "Position size too small for this asset price",
                entry_price,
            )
            db.add(execution)
            await db.flush()
            return execution

        # Final notional check
        notional = quantity * entry_price
        if notional > available_balance:
            logger.warning(
                "Notional $%.2f exceeds available balance $%.2f for signal %s",
                notional,
                available_balance,
                signal.id,
            )
            execution = self._create_failed_execution(
                signal,
                user,
                strategy,
                f"Order size (${notional:.2f}) exceeds available balance (${available_balance:.2f})",
                entry_price,
            )
            db.add(execution)
            await db.flush()
            return execution

        is_buy = signal.direction == SignalDirection.BUY

        # Create execution record (idempotency: unique signal_id + strategy_id)
        execution = Execution(
            signal_id=signal.id,
            user_id=user.id,
            strategy_id=strategy.id,
            wallet_address_hash=user.wallet_address_hash,
            order_type=OrderType.MARKET,
            direction=signal.direction,
            entry_price=entry_price,
            quantity=quantity,
            leverage=min(strategy.leverage_limit, settings.ate_default_leverage),
            status=ExecutionStatus.PENDING,
        )
        db.add(execution)

        # ── Decrypt private key ──────────────────────────────────────
        try:
            private_key = decrypt_private_key(user.encrypted_private_key)
        except ValueError:
            logger.error("Failed to decrypt private key for user %s", user.id)
            execution.status = ExecutionStatus.FAILED
            execution.error_message = "Failed to decrypt trading key"
            await db.flush()
            return execution

        # For connected wallets (agent key), pass account_address so the
        # agent signs on behalf of the user's main Hyperliquid wallet.
        # For generated wallets, wallet_address IS the trading wallet.
        from app.models.enums import WalletType

        account_address = (
            user.wallet_address if user.wallet_type == WalletType.CONNECTED else None
        )

        logger.info(
            "Placing order: %s %s qty=%s @ $%.2f (notional=$%.2f) "
            "for user %s strategy %s [wallet_type=%s, account=%s]",
            "BUY" if is_buy else "SELL",
            signal.symbol,
            quantity,
            entry_price,
            notional,
            user.id,
            strategy.id,
            user.wallet_type.value if user.wallet_type else "none",
            account_address or "direct",
        )

        order_result = await self.hyperliquid.place_order(
            wallet_private_key=private_key,
            symbol=signal.symbol,
            is_buy=is_buy,
            size=quantity,
            account_address=account_address,
            order_type="market",
        )

        if order_result.get("success"):
            execution.status = ExecutionStatus.FILLED
            execution.tx_hash = str(order_result.get("tx_hash", ""))
            execution.executed_at = utc_now()
            logger.info(
                "Order FILLED for signal %s strategy %s: tx_hash=%s",
                signal.id,
                strategy.id,
                execution.tx_hash,
            )
        else:
            execution.status = ExecutionStatus.FAILED
            error_msg = order_result.get("error", "unknown")
            execution.error_message = str(error_msg)[:500]
            logger.error(
                "Order FAILED for signal %s (user %s, strategy %s, symbol %s): %s",
                signal.id,
                user.id,
                strategy.id,
                signal.symbol,
                error_msg,
            )

        await db.flush()

        # Record rate limit
        ate_rate_limiter.record(rate_key)

        # Log transaction
        if execution.status == ExecutionStatus.FILLED:
            await VerificationService.log_execution_transaction(
                db=db,
                execution=execution,
                amount=quantity * entry_price,
                asset=signal.symbol,
            )

            # Send trade executed notification
            try:
                notification_service = NotificationService()
                await notification_service.send_trade_executed(
                    user=user,
                    symbol=signal.symbol,
                    direction=signal.direction.value,
                    quantity=str(quantity),
                    entry_price=str(entry_price),
                    strategy_name=strategy.name,
                )
            except Exception as e:
                logger.error("Failed to send trade executed email: %s", e)

        return execution

    async def check_drawdown(
        self,
        db: AsyncSession,
        strategy: Strategy,
        user: User,
    ) -> bool:
        """Check if strategy has exceeded its max drawdown limit.
        Returns True if drawdown limit is hit and strategy should be paused.
        """
        result = await db.execute(
            select(func.sum(Execution.pnl)).where(
                Execution.strategy_id == strategy.id,
                Execution.user_id == user.id,
                Execution.status == ExecutionStatus.FILLED,
                Execution.created_at >= utc_now() - timedelta(days=1),
            )
        )
        daily_pnl = result.scalar() or 0.0

        # Use wallet balance to calculate drawdown percentage
        available_balance = await self._get_wallet_balance(user)
        if available_balance is None or available_balance <= 0:
            return False

        effective_capital = available_balance * (strategy.allocation_pct / 100)
        if effective_capital <= 0:
            return False

        drawdown_percent = abs(min(0, float(daily_pnl))) / effective_capital * 100

        # Use daily_loss_cap if set, otherwise fall back to max_drawdown_percent
        effective_cap = strategy.daily_loss_cap_percent or strategy.max_drawdown_percent

        if drawdown_percent >= effective_cap:
            strategy.is_active = False
            strategy.paused_reason = (
                f"Daily loss cap hit: {drawdown_percent:.1f}% >= {effective_cap}%"
            )
            strategy.paused_at = utc_now()
            logger.warning(
                f"Strategy {strategy.id} auto-paused: drawdown {drawdown_percent:.1f}% "
                f">= limit {effective_cap}%"
            )

            # Send strategy paused notification
            try:
                notification_service = NotificationService()
                await notification_service.send_strategy_paused(
                    user=user,
                    strategy_name=strategy.name,
                    reason=strategy.paused_reason,
                )
            except Exception as e:
                logger.error("Failed to send strategy paused email: %s", e)

            return True

        return False

    async def monitor_positions(self, db: AsyncSession) -> list[dict]:
        """Monitor open positions for TP/SL hits.

        Queries all FILLED executions without an exit_price, compares
        current market prices against the signal's take_profit/stop_loss
        levels, and closes positions that have been triggered.

        Returns a list of result dicts for each closed position.
        """
        # Load all open executions with their signals, strategies, and users
        result = await db.execute(
            select(Execution)
            .options(
                selectinload(Execution.signal),
                selectinload(Execution.strategy),
                selectinload(Execution.user),
            )
            .where(
                Execution.status == ExecutionStatus.FILLED,
                Execution.exit_price == None,  # noqa: E711
            )
        )
        open_executions = result.scalars().all()

        if not open_executions:
            return []

        # Batch-fetch current prices
        try:
            all_mids = await self.hyperliquid.get_all_mids()
        except Exception as e:
            logger.error("Failed to fetch market prices for TP/SL monitoring: %s", e)
            return []

        closed = []
        notification_service = NotificationService()

        for execution in open_executions:
            signal = execution.signal
            user = execution.user
            strategy = execution.strategy

            if not signal or not user:
                continue

            symbol = signal.symbol
            mid_str = all_mids.get(symbol)
            if not mid_str:
                continue

            current_price = float(mid_str)
            tp_price = float(signal.take_profit) if signal.take_profit else None
            sl_price = float(signal.stop_loss) if signal.stop_loss else None
            entry_price = float(execution.entry_price)
            is_buy = execution.direction == SignalDirection.BUY

            # Determine if TP or SL has been hit
            triggered = None
            if tp_price:
                if (is_buy and current_price >= tp_price) or (
                    not is_buy and current_price <= tp_price
                ):
                    triggered = "take_profit"
            if sl_price and triggered is None:
                if (is_buy and current_price <= sl_price) or (
                    not is_buy and current_price >= sl_price
                ):
                    triggered = "stop_loss"

            if triggered is None:
                continue

            # Close position
            private_key = None
            if user.encrypted_private_key:
                try:
                    private_key = decrypt_private_key(user.encrypted_private_key)
                except ValueError:
                    logger.error(
                        "Failed to decrypt key for user %s during TP/SL close", user.id
                    )
                    continue

            if not private_key:
                logger.warning(
                    "No private key for user %s, cannot close position %s",
                    user.id,
                    execution.id,
                )
                continue

            # Determine account_address for agent wallet mode
            from app.models.enums import WalletType

            account_address = (
                user.wallet_address
                if user.wallet_type == WalletType.CONNECTED
                else None
            )

            close_result = await self.hyperliquid.close_position(
                wallet_private_key=private_key,
                symbol=symbol,
                size=float(execution.quantity),
                is_buy=is_buy,
                account_address=account_address,
            )

            if close_result.get("success"):
                execution.exit_price = current_price
                quantity = float(execution.quantity)
                if is_buy:
                    execution.pnl = (current_price - entry_price) * quantity
                else:
                    execution.pnl = (entry_price - current_price) * quantity
                execution.status = ExecutionStatus.CLOSED

                pnl_str = f"{float(execution.pnl):+.4f}"
                strategy_name = strategy.name if strategy else "Unknown"

                # Send notification
                try:
                    if triggered == "take_profit":
                        await notification_service.send_take_profit_hit(
                            user=user,
                            symbol=symbol,
                            direction=execution.direction.value,
                            entry_price=str(entry_price),
                            exit_price=str(current_price),
                            pnl=pnl_str,
                            strategy_name=strategy_name,
                        )
                    else:
                        await notification_service.send_stop_loss_hit(
                            user=user,
                            symbol=symbol,
                            direction=execution.direction.value,
                            entry_price=str(entry_price),
                            exit_price=str(current_price),
                            pnl=pnl_str,
                            strategy_name=strategy_name,
                        )
                except Exception as e:
                    logger.error("Failed to send %s notification: %s", triggered, e)

                closed.append(
                    {
                        "execution_id": str(execution.id),
                        "symbol": symbol,
                        "triggered": triggered,
                        "entry_price": entry_price,
                        "exit_price": current_price,
                        "pnl": float(execution.pnl),
                    }
                )

                logger.info(
                    "%s hit for %s: entry=%.4f exit=%.4f pnl=%s",
                    triggered.upper().replace("_", " "),
                    symbol,
                    entry_price,
                    current_price,
                    pnl_str,
                )
            else:
                close_error = close_result.get("error", "unknown")
                execution.error_message = (
                    f"Failed to close on {triggered}: {close_error}"[:500]
                )
                logger.error(
                    "Failed to close position %s for %s: %s",
                    execution.id,
                    triggered,
                    close_error,
                )

        if closed:
            await db.flush()

        return closed
