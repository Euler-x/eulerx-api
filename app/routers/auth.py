import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.middleware.auth import get_current_user
from app.models.ambassador import Ambassador
from app.models.enums import WalletType
from app.models.schemas.auth import (
    AuthResponse,
    EmailSubmitRequest,
    EmailVerificationResponse,
    EmailVerifyRequest,
    RefreshTokenRequest,
    SignMessageResponse,
    UserResponse,
    WalletConnectRequest,
    WalletGenerateRequest,
    WalletGenerateResponse,
)
from app.models.user import User
from app.services.notifications import NotificationService
from app.services.wallet import WalletService
from app.utils.helpers import generate_referral_code, utc_now
from app.utils.security import (
    create_access_token,
    create_refresh_token,
    generate_sign_message,
    verify_token,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/connect", response_model=AuthResponse)
async def connect_wallet(
    request: WalletConnectRequest,
    db: AsyncSession = Depends(get_db),
):
    if not WalletService.verify_signature(
        request.wallet_address, request.message, request.signature
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid wallet signature",
        )

    address_hash = WalletService.hash_address(request.wallet_address)

    result = await db.execute(
        select(User).where(User.wallet_address_hash == address_hash)
    )
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            wallet_address_hash=address_hash,
            wallet_type=WalletType.CONNECTED,
        )
        db.add(user)
        await db.flush()

    access_token = create_access_token(str(user.id), user.is_admin)
    refresh_token = create_refresh_token(str(user.id))

    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserResponse.model_validate(user),
    )


@router.post("/generate", response_model=WalletGenerateResponse)
async def generate_wallet(
    request: WalletGenerateRequest = WalletGenerateRequest(),
    db: AsyncSession = Depends(get_db),
):
    wallet_data = WalletService.generate_wallet()

    user = User(
        wallet_address_hash=wallet_data["address_hash"],
        wallet_type=WalletType.GENERATED,
        encrypted_private_key=wallet_data["encrypted_private_key"],
    )
    db.add(user)
    await db.flush()

    # Handle referral
    if request.referral_code:
        ref_result = await db.execute(
            select(Ambassador).where(
                Ambassador.referral_code == request.referral_code
            )
        )
        referrer = ref_result.scalar_one_or_none()
        if referrer:
            ambassador = Ambassador(
                user_id=user.id,
                referral_code=generate_referral_code(),
                referred_by=referrer.id,
            )
            db.add(ambassador)
            referrer.total_referrals += 1
            referrer.team_size += 1

            # Notify ambassador of new referral
            referrer_user_result = await db.execute(
                select(User).where(User.id == referrer.user_id)
            )
            referrer_user = referrer_user_result.scalar_one_or_none()
            if referrer_user:
                notification_service = NotificationService()
                await notification_service.send_referral_signup(
                    ambassador_user=referrer_user,
                    referred_wallet_hash=wallet_data["address_hash"],
                )

    access_token = create_access_token(str(user.id), user.is_admin)
    refresh_token = create_refresh_token(str(user.id))

    return WalletGenerateResponse(
        wallet_address=wallet_data["address"],
        private_key=wallet_data["private_key"],
        auth=AuthResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            user=UserResponse.model_validate(user),
        ),
    )


@router.post("/refresh", response_model=AuthResponse)
async def refresh_token(
    request: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        payload = verify_token(request.refresh_token, expected_type="refresh")
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        ) from e

    user_id = payload.get("sub")
    result = await db.execute(
        select(User).where(User.id == uuid.UUID(user_id))
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    access_token = create_access_token(str(user.id), user.is_admin)
    new_refresh_token = create_refresh_token(str(user.id))

    return AuthResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        user=UserResponse.model_validate(user),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return UserResponse.model_validate(current_user)


@router.get("/sign-message", response_model=SignMessageResponse)
async def get_sign_message(wallet_address: str):
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", wallet_address):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid wallet address format",
        )
    message = generate_sign_message(wallet_address)
    return SignMessageResponse(message=message, wallet_address=wallet_address)


# ── Email Verification ───────────────────────────────────────────


@router.post("/email/submit", response_model=EmailVerificationResponse)
async def submit_email(
    request: EmailSubmitRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Submit an email address for verification. Sends a 6-digit code."""
    # Check if email is already taken by another user
    existing = await db.execute(
        select(User).where(
            User.email == request.email,
            User.id != current_user.id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This email is already associated with another account.",
        )

    current_user.email = request.email
    current_user.email_verified = False

    notification_service = NotificationService()
    await notification_service.send_verification_email(db, current_user)

    return EmailVerificationResponse(
        message="Verification code sent to your email.",
        email_verified=False,
    )


@router.post("/email/verify", response_model=EmailVerificationResponse)
async def verify_email(
    request: EmailVerifyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Verify email with 6-digit code."""
    if current_user.email_verified:
        return EmailVerificationResponse(
            message="Email already verified.",
            email_verified=True,
        )

    if not current_user.email_verification_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No verification code found. Please submit your email first.",
        )

    if (
        current_user.email_verification_expires_at
        and current_user.email_verification_expires_at < utc_now()
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification code has expired. Please request a new one.",
        )

    if current_user.email_verification_code != request.code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification code.",
        )

    current_user.email_verified = True
    current_user.email_verification_code = None
    current_user.email_verification_expires_at = None
    await db.flush()

    # Send welcome email
    notification_service = NotificationService()
    await notification_service.send_welcome_email(current_user)

    return EmailVerificationResponse(
        message="Email verified successfully!",
        email_verified=True,
    )
