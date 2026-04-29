import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models.enums import (
    Exchange,
    ExecutionStatus,
    OrderType,
    SignalDirection,
    SignalStatus,
)
from app.models.bybit_signal import BybitSignal
from app.models.execution import Execution
from app.models.signal import Signal
from app.models.strategy import Strategy
from app.models.user import User
from app.services.bybit import BybitService
from app.services.execution_sync import ExecutionSyncService
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
        # Use strategy's leverage — fall back to system default only if unset
        leverage = strategy.leverage_limit or settings.ate_default_leverage

        # Risk multiplier from strategy's risk_profile (user-configured)
        risk_multiplier = {
            "low": 0.02,
            "medium": 0.05,
            "high": 0.10,
        }.get(strategy.risk_profile.value, 0.02)

        logger.info(
            "Position sizing: strategy=%s, risk_profile=%s (multiplier=%.2f), "
            "allocation=%.1f%%, leverage=%.1fx, wallet=$%.2f, effective=$%.2f",
            strategy.name,
            strategy.risk_profile.value,
            risk_multiplier,
            strategy.allocation_pct,
            leverage,
            available_balance,
            effective_capital,
        )

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

    def _get_bybit_keys(self, user: User) -> tuple[str, str] | None:
        """Decrypt and return (api_key, api_secret) for a Bybit-configured user."""
        if not user.bybit_configured:
            return None
        try:
            api_key = decrypt_private_key(user.bybit_api_key_encrypted)
            api_secret = decrypt_private_key(user.bybit_api_secret_encrypted)
            return api_key, api_secret
        except ValueError:
            logger.error("Failed to decrypt Bybit keys for user %s", user.id)
            return None

    def _get_user_bybit_service(self, user: User) -> BybitService:
        """Get a BybitService configured for this user's testnet/mainnet setting."""
        return BybitService(testnet=getattr(user, "bybit_testnet", False))

    async def _get_bybit_balance(self, user: User) -> float | None:
        """Fetch available balance from Bybit."""
        keys = self._get_bybit_keys(user)
        if not keys:
            return None
        api_key, api_secret = keys
        try:
            bybit = self._get_user_bybit_service(user)
            balance = await bybit.get_available_balance(api_key, api_secret)
            if balance <= 0:
                return None
            logger.info("User %s Bybit balance: $%.2f", user.id, balance)
            return balance
        except Exception as e:
            logger.error("Failed to fetch Bybit balance for user %s: %s", user.id, e)
            return None

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
        signal: Signal | BybitSignal,
        user: User,
        strategy: Strategy,
        error_message: str,
        entry_price: float | None = None,
    ) -> Execution:
        """Create a FAILED execution record with an error message."""
        is_bb = isinstance(signal, BybitSignal)
        return Execution(
            signal_id=None if is_bb else signal.id,
            bybit_signal_id=signal.id if is_bb else None,
            user_id=user.id,
            strategy_id=strategy.id,
            wallet_address_hash=user.wallet_address_hash
            or f"bybit:{str(user.id)[:16]}",
            order_type=OrderType.MARKET,
            direction=signal.direction,
            entry_price=entry_price or float(signal.entry_price),
            quantity=0,
            leverage=strategy.leverage_limit or settings.ate_default_leverage,
            status=ExecutionStatus.FAILED,
            exchange=Exchange.BYBIT if is_bb else Exchange.HYPERLIQUID,
            error_message=error_message[:500],
        )

    async def execute_signal(
        self,
        db: AsyncSession,
        signal: Signal | BybitSignal,
        strategy: Strategy,
        user: User,
    ) -> Execution | None:
        from app.services.dynamic_config import get_config

        # Read dynamic config (DB overrides → env defaults)
        default_leverage = await get_config(
            "ate_default_leverage", settings.ate_default_leverage, db
        )

        rate_key = f"ate:{user.id}"
        if not ate_rate_limiter.check(rate_key):
            logger.warning("ATE rate limit reached for user %s", user.id)
            return None

        # ── Per-(signal, strategy) idempotency ───────────────────────
        # Signals are shared across strategies. Prevent the same strategy
        # from executing the same signal twice.
        # For Bybit signals, signal_id is NULL — check bybit_signal_id instead.
        if isinstance(signal, BybitSignal):
            existing = await db.execute(
                select(func.count(Execution.id)).where(
                    Execution.bybit_signal_id == signal.id,
                    Execution.strategy_id == strategy.id,
                )
            )
        else:
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

        # ── Determine exchange for this signal ─────────────────────────
        is_bybit = isinstance(signal, BybitSignal)
        signal_exchange = Exchange.BYBIT if is_bybit else Exchange.HYPERLIQUID

        # ── Pre-execution wallet/key checks ───────────────────────────
        if is_bybit:
            if not user.bybit_configured:
                logger.warning("User %s has no Bybit keys, skipping", user.id)
                execution = self._create_failed_execution(
                    signal, user, strategy, "No Bybit API keys configured"
                )
                db.add(execution)
                await db.flush()
                return execution
            available_balance = await self._get_bybit_balance(user)
        else:
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
        if is_bybit:
            user_bybit = self._get_user_bybit_service(user)
            qty_step = await user_bybit.get_qty_step(signal.symbol)
            # Derive decimals from qty_step (e.g. 0.001 → 3)
            import math as _math

            sz_decimals = (
                max(0, -int(_math.floor(_math.log10(qty_step)))) if qty_step > 0 else 3
            )
        else:
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
        is_bb_signal = isinstance(signal, BybitSignal)
        execution = Execution(
            signal_id=None if is_bb_signal else signal.id,
            bybit_signal_id=signal.id if is_bb_signal else None,
            user_id=user.id,
            strategy_id=strategy.id,
            wallet_address_hash=user.wallet_address_hash
            or f"bybit:{str(user.id)[:16]}",
            order_type=OrderType.MARKET,
            direction=signal.direction,
            entry_price=entry_price,
            quantity=quantity,
            leverage=strategy.leverage_limit or default_leverage,
            status=ExecutionStatus.PENDING,
            exchange=signal_exchange,
        )
        db.add(execution)

        target_leverage = int(strategy.leverage_limit or default_leverage)

        if is_bybit:
            # ── Bybit execution path ──────────────────────────────────
            bybit_keys = self._get_bybit_keys(user)
            if not bybit_keys:
                execution.status = ExecutionStatus.FAILED
                execution.error_message = "Failed to decrypt Bybit keys"
                await db.flush()
                return execution
            api_key, api_secret = bybit_keys

            # Set leverage — retry with lower values if symbol has a cap
            actual_leverage = target_leverage
            if target_leverage >= 1:
                for lev in [target_leverage, 10, 5, 3, 1]:
                    lev_result = await user_bybit.update_leverage(
                        api_key, api_secret, signal.symbol, lev
                    )
                    if lev_result.get("success"):
                        actual_leverage = lev
                        break
                    err = str(lev_result.get("error", ""))
                    if "not modified" in err:
                        actual_leverage = lev
                        break
                    if "maxLeverage" in err or "110043" in err:
                        continue  # try next lower leverage
                    logger.warning(
                        "Failed to set Bybit leverage to %dx for %s: %s",
                        lev,
                        signal.symbol,
                        err,
                    )
                    break

            # Recalculate position size if leverage was reduced
            if actual_leverage != target_leverage and actual_leverage > 0:
                ratio = actual_leverage / target_leverage
                quantity = round(quantity * ratio, sz_decimals)
                notional = quantity * entry_price

            logger.info(
                "Placing Bybit order: %s %s qty=%s @ $%.2f (notional=$%.2f) "
                "leverage=%dx for user %s strategy %s",
                "BUY" if is_buy else "SELL",
                signal.symbol,
                quantity,
                entry_price,
                notional,
                actual_leverage,
                user.id,
                strategy.id,
            )

            # Place market order WITHOUT inline TP/SL.
            # TP/SL will be set separately after fill via set_trading_stop,
            # which validates against the actual fill price and avoids
            # rejections when the market has moved past the signal's targets.
            order_result = await user_bybit.place_order(
                api_key=api_key,
                api_secret=api_secret,
                symbol=signal.symbol,
                is_buy=is_buy,
                size=quantity,
                order_type="market",
            )

            if order_result.get("success"):
                execution.status = ExecutionStatus.FILLED
                execution.exchange_order_id = str(order_result.get("oid", ""))
                execution.tx_hash = str(order_result.get("tx_hash", ""))
                execution.executed_at = utc_now()
                logger.info(
                    "Bybit order FILLED for signal %s: order_id=%s",
                    signal.id,
                    execution.exchange_order_id,
                )
            else:
                execution.status = ExecutionStatus.FAILED
                error_msg = order_result.get("error", "unknown")
                execution.error_message = str(error_msg)[:500]
                logger.error(
                    "Bybit order FAILED for signal %s: %s", signal.id, error_msg
                )

        else:
            # ── Hyperliquid execution path (existing) ─────────────────
            try:
                private_key = decrypt_private_key(user.encrypted_private_key)
            except ValueError:
                logger.error("Failed to decrypt private key for user %s", user.id)
                execution.status = ExecutionStatus.FAILED
                execution.error_message = "Failed to decrypt trading key"
                await db.flush()
                return execution

            from app.models.enums import WalletType

            account_address = (
                user.wallet_address
                if user.wallet_type == WalletType.CONNECTED
                else None
            )

            if target_leverage >= 1:
                lev_result = await self.hyperliquid.update_leverage(
                    wallet_private_key=private_key,
                    symbol=signal.symbol,
                    leverage=target_leverage,
                    account_address=account_address,
                )
                if not lev_result.get("success"):
                    logger.warning(
                        "Failed to set leverage to %dx for %s: %s",
                        target_leverage,
                        signal.symbol,
                        lev_result.get("error"),
                    )

            logger.info(
                "Placing HL order: %s %s qty=%s @ $%.2f (notional=$%.2f) "
                "leverage=%dx for user %s strategy %s [risk=%s, allocation=%.0f%%]",
                "BUY" if is_buy else "SELL",
                signal.symbol,
                quantity,
                entry_price,
                notional,
                target_leverage,
                user.id,
                strategy.id,
                strategy.risk_profile.value,
                strategy.allocation_pct,
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
            else:
                execution.status = ExecutionStatus.FAILED
                error_msg = order_result.get("error", "unknown")
                execution.error_message = str(error_msg)[:500]
                logger.error("HL order FAILED for signal %s: %s", signal.id, error_msg)

        await db.flush()
        ate_rate_limiter.record(rate_key)

        # ── Post-fill: log transaction, place TP/SL, notify ───────────
        if execution.status == ExecutionStatus.FILLED:
            await VerificationService.log_execution_transaction(
                db=db,
                execution=execution,
                amount=quantity * entry_price,
                asset=signal.symbol,
            )

            # Place TP/SL after fill
            tp_price = float(signal.take_profit) if signal.take_profit else None
            sl_price = float(signal.stop_loss) if signal.stop_loss else None

            if is_bybit:
                # Bybit: use set_trading_stop (tick-size rounding handled
                # inside place_tp_sl_orders)
                if tp_price is not None or sl_price is not None:
                    try:
                        bybit_keys = self._get_bybit_keys(user)
                        if bybit_keys:
                            api_key, api_secret = bybit_keys
                            user_bybit = self._get_user_bybit_service(user)
                            tpsl_result = await user_bybit.place_tp_sl_orders(
                                api_key=api_key,
                                api_secret=api_secret,
                                symbol=signal.symbol,
                                is_buy=is_buy,
                                take_profit_price=tp_price,
                                stop_loss_price=sl_price,
                            )
                            if tpsl_result.get("success"):
                                logger.info(
                                    "Bybit TP/SL set for %s: tp=$%s sl=$%s",
                                    signal.symbol,
                                    tp_price,
                                    sl_price,
                                )
                            else:
                                logger.warning(
                                    "Bybit set_trading_stop failed for %s: %s "
                                    "— position is open without TP/SL, "
                                    "monitor will handle exits",
                                    signal.symbol,
                                    tpsl_result.get("error"),
                                )
                    except Exception as e:
                        logger.error(
                            "Exception setting Bybit TP/SL for %s: %s",
                            signal.symbol,
                            e,
                        )
            else:
                # Hyperliquid: separate TP/SL order calls
                if tp_price is not None or sl_price is not None:
                    try:
                        tpsl_result = await self.hyperliquid.place_tp_sl_orders(
                            wallet_private_key=private_key,
                            symbol=signal.symbol,
                            size=quantity,
                            is_buy=is_buy,
                            take_profit_price=tp_price,
                            stop_loss_price=sl_price,
                            account_address=account_address,
                        )
                        tp_ok = tpsl_result.get("tp_placed", False)
                        sl_ok = tpsl_result.get("sl_placed", False)
                        if tp_ok and sl_ok:
                            logger.info(
                                "TP/SL placed for %s: tp=$%s sl=$%s",
                                signal.symbol,
                                tp_price,
                                sl_price,
                            )
                        else:
                            if not tp_ok and tp_price:
                                logger.error(
                                    "Failed TP for %s: %s",
                                    signal.symbol,
                                    tpsl_result.get("results", {}).get("tp"),
                                )
                            if not sl_ok and sl_price:
                                logger.error(
                                    "Failed SL for %s: %s",
                                    signal.symbol,
                                    tpsl_result.get("results", {}).get("sl"),
                                )
                    except Exception as e:
                        logger.error(
                            "Exception placing TP/SL for %s: %s", signal.symbol, e
                        )

            try:
                notification_service = NotificationService()
                await notification_service.send_trade_executed(
                    user=user,
                    symbol=signal.symbol,
                    direction=signal.direction.value,
                    quantity=str(quantity),
                    entry_price=str(entry_price),
                    strategy_name=strategy.name,
                    exchange=signal_exchange.value,
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
        # Try Bybit first if configured, then Hyperliquid
        available_balance = None
        if user.bybit_configured:
            available_balance = await self._get_bybit_balance(user)
        if available_balance is None:
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
        """Reconcile open executions with actual exchange position state.

        Handles both Hyperliquid and Bybit executions. For each FILLED execution,
        checks whether the position still exists on the exchange. If gone (closed
        by native TP/SL or manually), looks up exit data and updates the DB.
        """
        result = await db.execute(
            select(Execution)
            .options(
                selectinload(Execution.signal),
                selectinload(Execution.bybit_signal),
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

        # Split executions by exchange
        hl_executions: list[Execution] = []
        bybit_executions: list[Execution] = []
        for ex in open_executions:
            if not ex.user:
                continue
            # Must have either HL signal or Bybit signal
            has_signal = ex.signal is not None or ex.bybit_signal is not None
            if not has_signal:
                continue
            ex_exchange = getattr(ex, "exchange", Exchange.HYPERLIQUID)
            if ex_exchange == Exchange.BYBIT:
                bybit_executions.append(ex)
            else:
                hl_executions.append(ex)

        closed: list[dict] = []
        notification_service = NotificationService()

        # ── Monitor Hyperliquid positions ──────────────────────────────
        by_wallet: dict[str, list[Execution]] = {}
        for execution in hl_executions:
            user = execution.user
            if not user or not user.wallet_address:
                continue
            by_wallet.setdefault(user.wallet_address, []).append(execution)

        for wallet_address, executions in by_wallet.items():
            hl_positions = await self.hyperliquid.get_user_positions(wallet_address)
            fills = await self.hyperliquid.get_user_fills(wallet_address, limit=500)

            for execution in executions:
                signal = execution.signal or execution.bybit_signal
                user = execution.user
                strategy = execution.strategy
                if not signal or not user:
                    continue

                symbol = signal.symbol

                hl_pos = hl_positions.get(symbol)
                if hl_pos is not None:
                    continue

                # Position is GONE from Hyperliquid — find the close fill
                close_data = ExecutionSyncService.find_hyperliquid_close_fill(
                    execution,
                    fills,
                )

                # Fallback: if standard fill matching fails, re-fetch fills
                # with a larger window and try again
                if close_data is None:
                    logger.warning(
                        "Position gone on HL for execution %s (%s) but no "
                        "close fill in initial batch — fetching extended fills",
                        execution.id,
                        symbol,
                    )
                    extended_fills = await self.hyperliquid.get_user_fills(
                        wallet_address, limit=2000
                    )
                    close_data = ExecutionSyncService.find_hyperliquid_close_fill(
                        execution,
                        extended_fills,
                    )

                # Last resort: position confirmed gone, fetch current mid
                # price to calculate best-available close data.  The monitor
                # runs every minute — this means the price is at most ~1 min
                # stale and guarantees the execution won't stay FILLED.
                if close_data is None:
                    logger.warning(
                        "No fill data found for execution %s (%s) after "
                        "extended search — using current mid price as exit",
                        execution.id,
                        symbol,
                    )
                    try:
                        all_mids = await self.hyperliquid.get_all_mids()
                        mid_price = float(all_mids.get(symbol, 0))
                    except Exception as e:
                        logger.error(
                            "Failed to fetch mid prices for fallback close: %s", e
                        )
                        mid_price = 0.0

                    if mid_price > 0:
                        entry_price = float(execution.entry_price)
                        quantity = float(execution.quantity)
                        is_buy = execution.direction == SignalDirection.BUY
                        pnl = (
                            (mid_price - entry_price) * quantity
                            if is_buy
                            else (entry_price - mid_price) * quantity
                        )
                        close_data = {
                            "exit_price": mid_price,
                            "closed_pnl": pnl,
                            "close_hash": None,
                            "closed_at": utc_now(),
                        }
                    else:
                        # Cannot determine exit price — skip this cycle,
                        # will retry on next monitor run
                        continue

                result_entry = self._reconcile_closed_position(
                    execution,
                    signal,
                    strategy,
                    user,
                    close_data["exit_price"],
                    close_data.get("close_hash"),
                    close_data.get("closed_pnl", 0.0),
                    close_data.get("closed_at"),
                    notification_service,
                    close_source="monitor",
                )
                if result_entry:
                    closed.append(result_entry)

        # ── Monitor Bybit positions ────────────────────────────────────
        by_user_id: dict[str, list[Execution]] = {}
        for execution in bybit_executions:
            user = execution.user
            if not user or not user.bybit_configured:
                continue
            by_user_id.setdefault(str(user.id), []).append(execution)

        for _uid, executions in by_user_id.items():
            user = executions[0].user
            bybit_keys = self._get_bybit_keys(user)
            if not bybit_keys:
                continue
            api_key, api_secret = bybit_keys
            user_bybit = self._get_user_bybit_service(user)

            bybit_positions = await user_bybit.get_user_positions(api_key, api_secret)

            for execution in executions:
                signal = execution.bybit_signal or execution.signal
                strategy = execution.strategy
                if not signal or not strategy:
                    continue

                symbol = signal.symbol
                entry_price = float(execution.entry_price)
                is_buy = execution.direction == SignalDirection.BUY

                bb_pos = bybit_positions.get(symbol)
                if bb_pos is not None:
                    continue

                # Position gone on Bybit — get actual PnL from closed PnL API
                exit_price = float(execution.entry_price)
                closed_pnl = 0.0
                close_hash = None
                closed_at = None
                try:
                    closed_pnl_data = await user_bybit.get_closed_pnl(
                        api_key,
                        api_secret,
                        symbol,
                        start_time_ms=int(
                            ExecutionSyncService.get_execution_opened_at(
                                execution
                            ).timestamp()
                            * 1000
                        ),
                        limit=20,
                    )
                    if closed_pnl_data:
                        exit_price = closed_pnl_data.get("exit_price", entry_price)
                        closed_pnl = closed_pnl_data.get("pnl", 0.0)
                        close_hash = closed_pnl_data.get("close_hash")
                        closed_at = closed_pnl_data.get("closed_at")
                        logger.info(
                            "Bybit closed PnL for %s: exit=%.8f pnl=%.4f",
                            symbol,
                            exit_price,
                            closed_pnl,
                        )
                except Exception as e:
                    logger.warning(
                        "Failed to get Bybit closed PnL for %s: %s", symbol, e
                    )

                result_entry = self._reconcile_closed_position(
                    execution,
                    signal,
                    strategy,
                    user,
                    exit_price,
                    close_hash,
                    closed_pnl,
                    closed_at,
                    notification_service,
                    close_source="monitor",
                )
                if result_entry:
                    closed.append(result_entry)

        if closed:
            await db.flush()

        # Send notifications after flush
        for entry in closed:
            await self._send_close_notification(entry, notification_service)

        # Strip ORM objects before returning — Celery must JSON-serialize this
        for entry in closed:
            entry.pop("_user", None)

        return closed

    def _reconcile_closed_position(
        self,
        execution: Execution,
        signal: Signal | BybitSignal,
        strategy: Strategy | None,
        user: User,
        exit_price: float,
        close_hash: str | None,
        closed_pnl: float,
        closed_at,
        notification_service: NotificationService,
        close_source: str = "exchange_sync",
    ) -> dict | None:
        """Update execution with exit data and return result dict."""
        result_entry = ExecutionSyncService.apply_close_details(
            execution,
            signal,
            exit_price=exit_price,
            closed_pnl=closed_pnl,
            close_hash=close_hash,
            closed_at=closed_at,
            close_source=close_source,
        )

        pnl_str = f"{float(execution.pnl):+.4f}"
        strategy_name = strategy.name if strategy else "Unknown"

        logger.info(
            "%s for %s: entry=%.4f exit=%.4f pnl=%s (user %s, %s)",
            (execution.close_reason or "closed").upper().replace("_", " "),
            signal.symbol,
            float(execution.entry_price),
            exit_price,
            pnl_str,
            user.id,
            getattr(execution, "exchange", "hyperliquid"),
        )

        result_entry.update(
            {
                "user_id": str(user.id),
                "_user": user,
                "strategy_name": strategy_name,
                "triggered": execution.close_reason or "closed",
                "exchange": getattr(
                    execution,
                    "exchange",
                    Exchange.HYPERLIQUID,
                ).value,
            }
        )
        return result_entry

    async def _send_close_notification(
        self, entry: dict, notification_service: NotificationService
    ) -> None:
        """Send TP/SL notification for a closed position."""
        user = entry.get("_user")
        if not user:
            return
        try:
            ex = entry.get("exchange", "hyperliquid")
            if entry["triggered"] == "take_profit":
                await notification_service.send_take_profit_hit(
                    user=user,
                    symbol=entry["symbol"],
                    direction=entry["direction"],
                    entry_price=str(entry["entry_price"]),
                    exit_price=str(entry["exit_price"]),
                    pnl=f"{entry['pnl']:+.4f}",
                    strategy_name=entry["strategy_name"],
                    exchange=ex,
                )
            elif entry["triggered"] == "stop_loss":
                await notification_service.send_stop_loss_hit(
                    user=user,
                    symbol=entry["symbol"],
                    direction=entry["direction"],
                    entry_price=str(entry["entry_price"]),
                    exit_price=str(entry["exit_price"]),
                    pnl=f"{entry['pnl']:+.4f}",
                    strategy_name=entry["strategy_name"],
                    exchange=ex,
                )
        except Exception as e:
            logger.error("Failed to send %s notification: %s", entry["triggered"], e)
