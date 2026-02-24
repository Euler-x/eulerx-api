"""Telegram message templates for EulerX notifications.

Each function returns a formatted string using Telegram HTML parse mode.
"""


def trade_executed(
    symbol: str,
    direction: str,
    quantity: str,
    entry_price: str,
    strategy_name: str,
) -> str:
    arrow = "\U0001f7e2" if direction.upper() == "BUY" else "\U0001f534"
    return (
        f"{arrow} <b>Trade Executed</b>\n\n"
        f"<b>Strategy:</b> {strategy_name}\n"
        f"<b>Symbol:</b> {symbol}\n"
        f"<b>Direction:</b> {direction.upper()}\n"
        f"<b>Quantity:</b> {quantity}\n"
        f"<b>Entry Price:</b> {entry_price}"
    )


def take_profit_hit(
    symbol: str,
    direction: str,
    entry_price: str,
    exit_price: str,
    pnl: str,
    strategy_name: str,
) -> str:
    return (
        f"\U0001f3af <b>Take Profit Hit!</b>\n\n"
        f"<b>Strategy:</b> {strategy_name}\n"
        f"<b>Symbol:</b> {symbol}\n"
        f"<b>Direction:</b> {direction.upper()}\n"
        f"<b>Entry:</b> {entry_price}\n"
        f"<b>Exit:</b> {exit_price}\n"
        f"<b>PnL:</b> {pnl}"
    )


def stop_loss_hit(
    symbol: str,
    direction: str,
    entry_price: str,
    exit_price: str,
    pnl: str,
    strategy_name: str,
) -> str:
    return (
        f"\U0001f6d1 <b>Stop Loss Triggered</b>\n\n"
        f"<b>Strategy:</b> {strategy_name}\n"
        f"<b>Symbol:</b> {symbol}\n"
        f"<b>Direction:</b> {direction.upper()}\n"
        f"<b>Entry:</b> {entry_price}\n"
        f"<b>Exit:</b> {exit_price}\n"
        f"<b>PnL:</b> {pnl}"
    )


def signal_generated(strategy_name: str, signal_count: int) -> str:
    plural = "s" if signal_count != 1 else ""
    return (
        f"\U0001f4e1 <b>{signal_count} New Signal{plural}</b>\n\n"
        f"<b>Strategy:</b> {strategy_name}\n"
        f"{signal_count} new trading signal{plural} ready for execution."
    )


def strategy_paused(strategy_name: str, reason: str) -> str:
    return (
        f"\u23f8 <b>Strategy Paused</b>\n\n"
        f"<b>Strategy:</b> {strategy_name}\n"
        f"<b>Reason:</b> {reason}"
    )


def subscription_activated(plan_name: str, billing_cycle: str, expires_at: str) -> str:
    return (
        f"\u2705 <b>Subscription Activated</b>\n\n"
        f"<b>Plan:</b> {plan_name}\n"
        f"<b>Cycle:</b> {billing_cycle}\n"
        f"<b>Expires:</b> {expires_at}"
    )


def subscription_expiring(plan_name: str, days_remaining: int, expires_at: str) -> str:
    s = "s" if days_remaining != 1 else ""
    return (
        f"\u26a0\ufe0f <b>Subscription Expiring</b>\n\n"
        f"Your <b>{plan_name}</b> plan expires in "
        f"<b>{days_remaining}</b> day{s}.\n"
        f"<b>Expires:</b> {expires_at}"
    )


def welcome() -> str:
    return (
        "\U0001f389 <b>Welcome to EulerX Network!</b>\n\n"
        "Your Telegram notifications are now active.\n"
        "You'll receive real-time updates on trades, signals, and more."
    )


def support_ticket_update(
    ticket_subject: str,
    new_status: str,
    admin_reply: str | None = None,
) -> str:
    text = (
        f"\U0001f3ab <b>Ticket Update</b>\n\n"
        f"<b>Subject:</b> {ticket_subject}\n"
        f"<b>Status:</b> {new_status}"
    )
    if admin_reply:
        text += f"\n\n<b>Admin Reply:</b>\n{admin_reply}"
    return text


def referral_signup(referred_wallet_hash: str) -> str:
    masked = f"{referred_wallet_hash[:8]}...{referred_wallet_hash[-4:]}"
    return (
        f"\U0001f91d <b>New Referral Signup</b>\n\n"
        f"Wallet <code>{masked}</code> signed up using your referral link!"
    )
