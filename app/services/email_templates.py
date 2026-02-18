"""Styled HTML email templates for EulerX transactional notifications.

Templates are loaded from the `templates/` directory and rendered with
Python's str.format(). Each template function returns (subject, html_body).

All templates share a common base layout:
- Dark background (#0D0D0D)
- Card body (#1A1A1A)
- Neon green accent (#39FF14)
- Responsive, inline-styled for email client compatibility
"""

from pathlib import Path

from app.config import get_settings

settings = get_settings()

_TEMPLATE_DIR = Path(__file__).parent / "templates"

# ── Template Loader ─────────────────────────────────────────────

_cache: dict[str, str] = {}


def _load(name: str) -> str:
    """Load an HTML template file by name (without extension). Cached."""
    if name not in _cache:
        path = _TEMPLATE_DIR / f"{name}.html"
        _cache[name] = path.read_text(encoding="utf-8")
    return _cache[name]


def _render(title: str, body_content: str) -> str:
    """Wrap body content in the base layout."""
    base = _load("base")
    return base.format(title=title, body_content=body_content)


# ── Admin Reply Section (for support ticket) ───────────────────

_REPLY_SECTION = """\
<hr style="border:none;border-top:1px solid #2A2A2A;margin:24px 0;">
<h3 style="margin:0 0 12px;font-size:16px;color:#39FF14;">Admin Reply</h3>
<div style="background-color:#0D0D0D;border-radius:8px;padding:16px 20px;margin:0 0 20px;">
  <p style="margin:0;font-size:14px;color:#CCCCCC;line-height:1.6;white-space:pre-wrap;">{admin_reply}</p>
</div>"""


# ── Templates ───────────────────────────────────────────────────


def email_verification(code: str) -> tuple[str, str]:
    """Email verification with 6-digit code."""
    subject = "Verify your EulerX email"
    body = _load("email_verification").format(
        code=code,
        expiry_minutes=settings.email_verification_expiry_minutes,
    )
    return subject, _render(subject, body)


def welcome() -> tuple[str, str]:
    """Welcome email after successful verification."""
    subject = "Welcome to EulerX Network"
    body = _load("welcome").format(frontend_url=settings.frontend_url)
    return subject, _render(subject, body)


def subscription_activated(
    plan_name: str,
    billing_cycle: str,
    expires_at: str,
) -> tuple[str, str]:
    """Subscription activated after payment confirmation."""
    subject = f"Subscription Activated — {plan_name}"
    body = _load("subscription_activated").format(
        plan_name=plan_name,
        billing_cycle=billing_cycle,
        expires_at=expires_at,
        frontend_url=settings.frontend_url,
    )
    return subject, _render(subject, body)


def subscription_expiring(
    plan_name: str,
    days_remaining: int,
    expires_at: str,
) -> tuple[str, str]:
    """Subscription expiring soon warning."""
    subject = f"Your {plan_name} plan expires in {days_remaining} days"
    body = _load("subscription_expiring").format(
        plan_name=plan_name,
        days_remaining=days_remaining,
        expires_at=expires_at,
        frontend_url=settings.frontend_url,
    )
    return subject, _render(subject, body)


def trade_executed(
    symbol: str,
    direction: str,
    quantity: str,
    entry_price: str,
    strategy_name: str,
) -> tuple[str, str]:
    """Trade executed notification."""
    color = "#39FF14" if direction.upper() == "BUY" else "#FF4444"
    subject = f"Trade Executed — {direction.upper()} {symbol}"
    body = _load("trade_executed").format(
        symbol=symbol,
        direction=direction.upper(),
        direction_color=color,
        quantity=quantity,
        entry_price=entry_price,
        strategy_name=strategy_name,
        frontend_url=settings.frontend_url,
    )
    return subject, _render(subject, body)


def strategy_paused(
    strategy_name: str,
    reason: str,
) -> tuple[str, str]:
    """Strategy auto-paused notification."""
    subject = f"Strategy Paused — {strategy_name}"
    body = _load("strategy_paused").format(
        strategy_name=strategy_name,
        reason=reason,
        frontend_url=settings.frontend_url,
    )
    return subject, _render(subject, body)


def signal_generated(
    strategy_name: str,
    signal_count: int,
) -> tuple[str, str]:
    """Signals generated notification."""
    subject = f"{signal_count} New Signal{'s' if signal_count != 1 else ''} — {strategy_name}"
    body = _load("signal_generated").format(
        strategy_name=strategy_name,
        signal_count=signal_count,
        signal_plural="s" if signal_count != 1 else "",
        frontend_url=settings.frontend_url,
    )
    return subject, _render(subject, body)


def support_ticket_update(
    ticket_subject: str,
    new_status: str,
    admin_reply: str | None = None,
) -> tuple[str, str]:
    """Support ticket status update / admin reply."""
    subject = f"Ticket Update — {ticket_subject}"
    reply_section = ""
    if admin_reply:
        reply_section = _REPLY_SECTION.format(admin_reply=admin_reply)

    body = _load("support_ticket_update").format(
        ticket_subject=ticket_subject,
        new_status=new_status,
        reply_section=reply_section,
        frontend_url=settings.frontend_url,
    )
    return subject, _render(subject, body)


def referral_signup(
    referred_wallet_hash: str,
) -> tuple[str, str]:
    """New referral signup notification for the ambassador."""
    subject = "New Referral Signup"
    masked = f"{referred_wallet_hash[:8]}...{referred_wallet_hash[-4:]}"
    body = _load("referral_signup").format(
        masked_wallet=masked,
        frontend_url=settings.frontend_url,
    )
    return subject, _render(subject, body)


def take_profit_hit(
    symbol: str,
    direction: str,
    entry_price: str,
    exit_price: str,
    pnl: str,
    strategy_name: str,
) -> tuple[str, str]:
    """Take profit hit notification."""
    subject = f"Take Profit Hit — {symbol}"
    body = _load("take_profit_hit").format(
        symbol=symbol,
        direction=direction.upper(),
        entry_price=entry_price,
        exit_price=exit_price,
        pnl=pnl,
        pnl_color="#39FF14",
        strategy_name=strategy_name,
        frontend_url=settings.frontend_url,
    )
    return subject, _render(subject, body)


def stop_loss_hit(
    symbol: str,
    direction: str,
    entry_price: str,
    exit_price: str,
    pnl: str,
    strategy_name: str,
) -> tuple[str, str]:
    """Stop loss hit notification."""
    subject = f"Stop Loss Triggered — {symbol}"
    body = _load("stop_loss_hit").format(
        symbol=symbol,
        direction=direction.upper(),
        entry_price=entry_price,
        exit_price=exit_price,
        pnl=pnl,
        pnl_color="#FF4444",
        strategy_name=strategy_name,
        frontend_url=settings.frontend_url,
    )
    return subject, _render(subject, body)


def password_reset(reset_link: str) -> tuple[str, str]:
    """Password reset email (future use)."""
    subject = "Reset Your Password"
    body = _load("password_reset").format(reset_link=reset_link)
    return subject, _render(subject, body)
