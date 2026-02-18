"""API endpoints for Telegram notification configuration and preferences."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.middleware.auth import get_current_user
from app.models.schemas.notifications import (
    NotificationPreferencesRequest,
    NotificationPreferencesResponse,
)
from app.models.schemas.telegram import (
    TelegramConfigRequest,
    TelegramConfigResponse,
    TelegramTestResponse,
)
from app.models.user import User
from app.services.notifications import NotificationService
from app.utils.security import decrypt_telegram_token, encrypt_telegram_token

router = APIRouter(prefix="/telegram", tags=["Telegram"])


@router.put(
    "/config",
    response_model=TelegramConfigResponse,
    summary="Save Telegram configuration",
)
async def save_telegram_config(
    request: TelegramConfigRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save or update Telegram bot token and chat ID for receiving notifications.

    Validates the bot token via Telegram getMe API and sends a test message
    to the chat_id before saving. Token is encrypted at rest.
    """
    # Step 1: Validate bot token via getMe
    is_valid, bot_info = await NotificationService.validate_telegram_bot(
        request.bot_token
    )
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid Telegram bot token: {bot_info}",
        )

    # Step 2: Send test message to verify chat_id is reachable
    test_ok = await NotificationService.send_telegram(
        bot_token=request.bot_token,
        chat_id=request.chat_id,
        text=(
            "\u2705 <b>EulerX Telegram Connected</b>\n\n"
            f"Bot <b>@{bot_info}</b> is now linked to your EulerX account.\n"
            "You will receive real-time trade and signal notifications here."
        ),
    )
    if not test_ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bot token is valid, but failed to send a message to the provided chat ID. "
            "Make sure you have started a conversation with your bot.",
        )

    # Step 3: Encrypt token and save
    current_user.telegram_bot_token = encrypt_telegram_token(request.bot_token)
    current_user.telegram_chat_id = request.chat_id
    await db.flush()

    return TelegramConfigResponse(
        message=f"Telegram configuration saved. Bot @{bot_info} connected.",
        telegram_configured=True,
    )


@router.delete(
    "/config",
    response_model=TelegramConfigResponse,
    summary="Remove Telegram configuration",
)
async def remove_telegram_config(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove Telegram notification configuration."""
    current_user.telegram_bot_token = None
    current_user.telegram_chat_id = None
    await db.flush()

    return TelegramConfigResponse(
        message="Telegram configuration removed.",
        telegram_configured=False,
    )


@router.post(
    "/test",
    response_model=TelegramTestResponse,
    summary="Test Telegram notifications",
)
async def test_telegram(
    current_user: User = Depends(get_current_user),
):
    """Send a test message to verify Telegram configuration works."""
    if not current_user.telegram_bot_token or not current_user.telegram_chat_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Telegram not configured. Save your bot token and chat ID first.",
        )

    # Decrypt the stored token
    try:
        token = decrypt_telegram_token(current_user.telegram_bot_token)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to decrypt stored Telegram token. Please reconfigure.",
        )

    success = await NotificationService.send_telegram(
        bot_token=token,
        chat_id=current_user.telegram_chat_id,
        text=(
            "\u2705 <b>EulerX Telegram Test</b>\n\n"
            "Your Telegram notifications are working correctly!\n"
            "You will receive real-time alerts for trades, signals, and more."
        ),
    )

    if success:
        return TelegramTestResponse(
            success=True,
            message="Test message sent successfully! Check your Telegram.",
        )
    return TelegramTestResponse(
        success=False,
        message="Failed to send test message. Please verify your bot token and chat ID.",
    )


# ── Notification Preferences ──────────────────────────────────────


@router.get(
    "/notifications/preferences",
    response_model=NotificationPreferencesResponse,
    summary="Get notification preferences",
)
async def get_notification_preferences(
    current_user: User = Depends(get_current_user),
):
    """Get the user's notification preferences.

    Returns all-enabled defaults if no preferences have been saved.
    """
    prefs = current_user.notification_preferences or {}
    return NotificationPreferencesResponse(
        trades_email=prefs.get("trades_email", True),
        trades_telegram=prefs.get("trades_telegram", True),
        signals_email=prefs.get("signals_email", True),
        signals_telegram=prefs.get("signals_telegram", True),
        billing_email=prefs.get("billing_email", True),
        billing_telegram=prefs.get("billing_telegram", True),
        support_email=prefs.get("support_email", True),
        support_telegram=prefs.get("support_telegram", True),
        referrals_email=prefs.get("referrals_email", True),
        referrals_telegram=prefs.get("referrals_telegram", True),
    )


@router.put(
    "/notifications/preferences",
    response_model=NotificationPreferencesResponse,
    summary="Update notification preferences",
)
async def update_notification_preferences(
    request: NotificationPreferencesRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the user's notification preferences.

    Categories: trades, signals, billing, support, referrals.
    Channels: email, telegram.
    """
    current_user.notification_preferences = request.model_dump()
    await db.flush()

    return NotificationPreferencesResponse(**request.model_dump())
