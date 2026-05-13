import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.middleware.auth import get_current_user, get_optional_user_strict
from app.models.ambassador import Ambassador
from app.models.enums import WalletType
from app.models.schemas.auth import (
    AuthResponse,
    BinanceConnectRequest,
    BinanceConnectResponse,
    BybitConnectRequest,
    BybitConnectResponse,
    EmailSubmitRequest,
    EmailVerificationResponse,
    EmailVerifyRequest,
    ForgotPasswordRequest,
    LoginRequest,
    PasswordResetResponse,
    RefreshTokenRequest,
    RegisterRequest,
    ResetPasswordRequest,
    UserResponse,
    WalletConnectRequest,
    WalletGenerateRequest,
    WalletGenerateResponse,
)
from app.models.user import User
from app.services.notifications import NotificationService
from app.services.turnstile import verify_turnstile
from app.services.wallet import WalletService
from app.utils.helpers import generate_referral_code, utc_now
from app.services import telegram_templates
from app.utils.security import (
    create_access_token,
    create_refresh_token,
    encrypt_private_key,
    hash_password,
    hash_wallet_address,
    verify_password,
    verify_token,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


async def _generate_unique_ambassador_code(db: AsyncSession) -> str:
    """Generate a referral code that is not already assigned."""
    for _ in range(10):
        code = generate_referral_code()
        existing = await db.execute(
            select(Ambassador.id).where(Ambassador.referral_code == code)
        )
        if existing.scalar_one_or_none() is None:
            return code
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Could not generate a unique referral code. Please try again.",
    )


async def _find_referrer_by_code(
    db: AsyncSession, referral_code: str | None
) -> Ambassador | None:
    code = (referral_code or "").strip().upper()
    if not code:
        return None
    result = await db.execute(
        select(Ambassador).where(Ambassador.referral_code == code)
    )
    return result.scalar_one_or_none()


async def _attach_referral_ambassador(
    db: AsyncSession,
    user: User,
    referrer: Ambassador | None,
) -> Ambassador | None:
    if referrer is None:
        return None
    ambassador = Ambassador(
        user_id=user.id,
        referral_code=await _generate_unique_ambassador_code(db),
        referred_by=referrer.id,
    )
    db.add(ambassador)
    referrer.total_referrals = (referrer.total_referrals or 0) + 1
    referrer.team_size = (referrer.team_size or 0) + 1
    await db.flush()
    return ambassador


# ── Email/Password Authentication ────────────────────────────────


@router.post("/register", response_model=AuthResponse)
async def register(
    request: RegisterRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Register a new user with email and password."""
    await verify_turnstile(
        request.cf_turnstile_token,
        http_request.client.host if http_request.client else None,
    )

    existing = await db.execute(select(User).where(User.email == request.email))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email already exists.",
        )

    user = User(
        email=request.email,
        password_hash=hash_password(request.password),
        email_verified=False,
    )
    db.add(user)
    await db.flush()

    # Handle referral. Codes are normalized so /ref/IPKW96GV and a manually
    # typed "ipkw96gv" resolve identically.
    referrer_user: User | None = None
    referrer = await _find_referrer_by_code(db, request.referral_code)
    if referrer:
        await _attach_referral_ambassador(db, user, referrer)
        ref_user_result = await db.execute(select(User).where(User.id == referrer.user_id))
        referrer_user = ref_user_result.scalar_one_or_none()

    notification_service = NotificationService()

    # Notify referrer of new signup
    if referrer_user:
        try:
            await notification_service.send_referral_signup_email(
                ambassador_user=referrer_user,
                referred_email=request.email,
            )
        except Exception:
            # Notifications must never roll back a successful signup.
            pass

    # Admin alert for new signup
    try:
        await notification_service.send_admin_alert(
            telegram_templates.admin_new_signup(
                identifier=request.email,
                method="Email/Password",
                referral=referrer_user is not None,
            )
        )
    except Exception:
        pass

    # Send verification email
    await notification_service.send_verification_email(db, user)

    access_token = create_access_token(str(user.id), user.is_admin)
    refresh_token = create_refresh_token(str(user.id))

    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserResponse.model_validate(user),
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    request: LoginRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Login with email and password."""
    await verify_turnstile(
        request.cf_turnstile_token,
        http_request.client.host if http_request.client else None,
    )

    result = await db.execute(select(User).where(User.email == request.email))
    user = result.scalar_one_or_none()

    if user is None or not user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    if not verify_password(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated. Please contact support.",
        )

    access_token = create_access_token(str(user.id), user.is_admin)
    refresh_token = create_refresh_token(str(user.id))

    # Login alert via Telegram
    notification_service = NotificationService()
    login_time = utc_now().strftime("%Y-%m-%d %H:%M UTC")
    ip = http_request.client.host if http_request.client else None
    await notification_service.send_login_alert(user, login_time, ip)

    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserResponse.model_validate(user),
    )


# ── Hyperliquid Wallet Connection ────────────────────────────────


@router.post("/connect", response_model=AuthResponse)
async def connect_wallet(
    request: WalletConnectRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user_strict),
):
    """Connect a Hyperliquid wallet using an Agent/API wallet private key.

    The agent key is created by the user at https://app.hyperliquid.xyz/API.
    Agent wallets can only execute trades — they cannot withdraw funds.
    """
    # Validate that the agent key is a valid Ethereum private key
    agent_address = WalletService.validate_private_key(request.agent_private_key)
    if not agent_address:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid agent private key. Please check the key and try again.",
        )

    address_hash = hash_wallet_address(request.wallet_address)
    encrypted_agent_key = encrypt_private_key(request.agent_private_key)

    # Check if wallet is already linked to another user
    result = await db.execute(
        select(User).where(User.wallet_address_hash == address_hash)
    )
    existing_wallet_user = result.scalar_one_or_none()

    if current_user is not None:
        # Authenticated user linking or updating a wallet from dashboard
        if existing_wallet_user and existing_wallet_user.id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This wallet is already linked to another account.",
            )
        current_user.wallet_address = request.wallet_address
        current_user.wallet_address_hash = address_hash
        current_user.wallet_type = WalletType.CONNECTED
        current_user.encrypted_private_key = encrypted_agent_key
        await db.flush()
        user = current_user
    elif existing_wallet_user is not None:
        # Existing wallet user — update agent key
        existing_wallet_user.encrypted_private_key = encrypted_agent_key
        existing_wallet_user.wallet_address = request.wallet_address
        await db.flush()
        user = existing_wallet_user
    else:
        # New wallet-only user
        user = User(
            wallet_address=request.wallet_address,
            wallet_address_hash=address_hash,
            wallet_type=WalletType.CONNECTED,
            encrypted_private_key=encrypted_agent_key,
        )
        db.add(user)
        await db.flush()
        # Notify admin of new wallet signup
        ns = NotificationService()
        await ns.send_admin_alert(
            telegram_templates.admin_new_signup(
                identifier=request.wallet_address[:10] + "...",
                method="Wallet Connect",
            )
        )

    access_token = create_access_token(str(user.id), user.is_admin)
    refresh_token = create_refresh_token(str(user.id))

    # Login alert via Telegram (for returning wallet users)
    notification_service = NotificationService()
    login_time = utc_now().strftime("%Y-%m-%d %H:%M UTC")
    ip = http_request.client.host if http_request.client else None
    await notification_service.send_login_alert(user, login_time, ip)

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
        wallet_address=wallet_data["address"],
        wallet_address_hash=wallet_data["address_hash"],
        wallet_type=WalletType.GENERATED,
        encrypted_private_key=wallet_data["encrypted_private_key"],
    )
    db.add(user)
    await db.flush()

    # Handle referral
    referrer = await _find_referrer_by_code(db, request.referral_code)
    if referrer:
        await _attach_referral_ambassador(db, user, referrer)
        referrer_user_result = await db.execute(
            select(User).where(User.id == referrer.user_id)
        )
        referrer_user = referrer_user_result.scalar_one_or_none()
        if referrer_user:
            try:
                notification_service = NotificationService()
                await notification_service.send_referral_signup(
                    ambassador_user=referrer_user,
                    referred_wallet_hash=wallet_data["address_hash"],
                )
            except Exception:
                pass

    # Admin alert for new wallet signup
    ns = NotificationService()
    try:
        await ns.send_admin_alert(
            telegram_templates.admin_new_signup(
                identifier=wallet_data["address"][:10] + "...",
                method="Generated Wallet",
                referral=referrer is not None,
            )
        )
    except Exception:
        pass

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
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
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


# ── Password Reset ────────────────────────────────────────────────


@router.post("/forgot-password", response_model=PasswordResetResponse)
async def forgot_password(
    request: ForgotPasswordRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Request a password reset email.

    Always returns the same message to prevent email enumeration.
    Only email/password accounts can reset their password.
    """
    await verify_turnstile(
        request.cf_turnstile_token,
        http_request.client.host if http_request.client else None,
    )

    _GENERIC = "If that email is registered, you'll receive a reset link shortly."

    result = await db.execute(select(User).where(User.email == request.email))
    user = result.scalar_one_or_none()

    # Silently skip: unknown email or wallet-only users (no password to reset)
    if user is None or not user.password_hash:
        return PasswordResetResponse(message=_GENERIC)

    notification_service = NotificationService()
    await notification_service.send_password_reset_email(db, user)

    return PasswordResetResponse(message=_GENERIC)


@router.post("/reset-password", response_model=PasswordResetResponse)
async def reset_password(
    request: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Complete a password reset using the token from the reset email."""
    result = await db.execute(
        select(User).where(User.password_reset_token == request.token)
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token.",
        )

    # Normalise timezone (SQLite returns naive datetimes; PostgreSQL returns aware)
    expires_at = user.password_reset_expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        from datetime import timezone as _tz

        expires_at = expires_at.replace(tzinfo=_tz.utc)

    if expires_at is None or expires_at < utc_now():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset link has expired. Please request a new one.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated. Please contact support.",
        )

    user.password_hash = hash_password(request.new_password)
    user.password_reset_token = None
    user.password_reset_expires_at = None
    await db.flush()

    return PasswordResetResponse(
        message="Password reset successfully. You can now log in."
    )


# ── Bybit API Key Connection ──────────────────────────────────


@router.post("/bybit/connect", response_model=BybitConnectResponse)
async def connect_bybit(
    request: BybitConnectRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Connect a Bybit account using API key + secret.

    The API key must have trading permissions. We validate the keys
    by fetching the account balance before saving.
    """
    from app.services.bybit import BybitService

    bybit = BybitService(testnet=request.testnet)
    valid, message = await bybit.validate_api_keys(request.api_key, request.api_secret)
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bybit API key validation failed: {message}",
        )

    # Encrypt and store
    current_user.bybit_api_key_encrypted = encrypt_private_key(request.api_key)
    current_user.bybit_api_secret_encrypted = encrypt_private_key(request.api_secret)
    current_user.bybit_testnet = request.testnet
    await db.flush()

    # Get account equity for confirmation
    equity = await bybit.get_account_value(request.api_key, request.api_secret)

    env_label = "testnet" if request.testnet else "mainnet"
    return BybitConnectResponse(
        message=f"Bybit {env_label} account connected successfully",
        bybit_configured=True,
        testnet=request.testnet,
        account_equity=equity,
    )


@router.post("/bybit/disconnect", response_model=BybitConnectResponse)
async def disconnect_bybit(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Disconnect Bybit account by removing stored API keys."""
    current_user.bybit_api_key_encrypted = None
    current_user.bybit_api_secret_encrypted = None
    current_user.bybit_testnet = False
    await db.flush()

    return BybitConnectResponse(
        message="Bybit account disconnected",
        bybit_configured=False,
        account_equity=0.0,
    )


# ── Binance API Key Connection ─────────────────────────────────────


@router.post("/binance/connect", response_model=BinanceConnectResponse)
async def connect_binance(
    request: BinanceConnectRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Connect a Binance USDⓈ-M Futures account using API key + secret.

    The API key must have Futures trading permissions. Keys are validated
    by fetching the account balance before saving.
    """
    from app.services.binance import BinanceService

    binance = BinanceService(testnet=request.testnet)
    valid, message = await binance.validate_api_keys(
        request.api_key, request.api_secret
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Binance API key validation failed: {message}",
        )

    current_user.binance_api_key_encrypted = encrypt_private_key(request.api_key)
    current_user.binance_api_secret_encrypted = encrypt_private_key(request.api_secret)
    current_user.binance_testnet = request.testnet
    await db.flush()

    equity = await binance.get_account_value(request.api_key, request.api_secret)

    env_label = "testnet" if request.testnet else "mainnet"
    return BinanceConnectResponse(
        message=f"Binance Futures {env_label} account connected successfully",
        binance_configured=True,
        testnet=request.testnet,
        account_equity=equity,
    )


@router.post("/binance/disconnect", response_model=BinanceConnectResponse)
async def disconnect_binance(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Disconnect Binance account by removing stored API keys."""
    current_user.binance_api_key_encrypted = None
    current_user.binance_api_secret_encrypted = None
    current_user.binance_testnet = False
    await db.flush()

    return BinanceConnectResponse(
        message="Binance account disconnected",
        binance_configured=False,
        account_equity=0.0,
    )
