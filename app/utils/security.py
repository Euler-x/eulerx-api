import hashlib
import time
from datetime import datetime, timezone

import jwt
from cryptography.fernet import Fernet, InvalidToken
from eth_account.messages import encode_defunct
from web3 import Web3

from app.config import get_settings

settings = get_settings()


# ─── Wallet Address Hashing ─────────────────────────────


def hash_wallet_address(address: str) -> str:
    return hashlib.sha256(address.lower().encode()).hexdigest()


# ─── Private Key Encryption ─────────────────────────────


def encrypt_private_key(private_key: str) -> str:
    if not settings.wallet_encryption_key:
        raise ValueError("WALLET_ENCRYPTION_KEY not configured")
    f = Fernet(settings.wallet_encryption_key.encode())
    return f.encrypt(private_key.encode()).decode()


def decrypt_private_key(encrypted_key: str) -> str:
    if not settings.wallet_encryption_key:
        raise ValueError("WALLET_ENCRYPTION_KEY not configured")
    f = Fernet(settings.wallet_encryption_key.encode())
    try:
        return f.decrypt(encrypted_key.encode()).decode()
    except InvalidToken as e:
        raise ValueError("Failed to decrypt private key") from e


# ─── Telegram Token Encryption (separate key) ────────────


def _get_telegram_fernet_key() -> str:
    """Return the Fernet key for Telegram tokens.

    Uses TELEGRAM_ENCRYPTION_KEY if set, otherwise falls back to WALLET_ENCRYPTION_KEY.
    """
    key = settings.telegram_encryption_key or settings.wallet_encryption_key
    if not key:
        raise ValueError("No encryption key configured for Telegram tokens")
    return key


def encrypt_telegram_token(token: str) -> str:
    """Encrypt a Telegram bot token using a dedicated Fernet key."""
    f = Fernet(_get_telegram_fernet_key().encode())
    return f.encrypt(token.encode()).decode()


def decrypt_telegram_token(encrypted_token: str) -> str:
    """Decrypt a Telegram bot token."""
    f = Fernet(_get_telegram_fernet_key().encode())
    try:
        return f.decrypt(encrypted_token.encode()).decode()
    except InvalidToken as e:
        raise ValueError("Failed to decrypt Telegram bot token") from e


# ─── JWT Tokens ──────────────────────────────────────────


def create_access_token(user_id: str, is_admin: bool = False) -> str:
    now = time.time()
    expire = now + (settings.jwt_access_token_expire_minutes * 60)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": expire,
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: str) -> str:
    now = time.time()
    expire = now + (settings.jwt_refresh_token_expire_days * 86400)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": expire,
        "type": "refresh",
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def verify_token(token: str, expected_type: str = "access") -> dict:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError as e:
        raise ValueError(f"Invalid token: {e}") from e

    if payload.get("type") != expected_type:
        raise ValueError(f"Expected {expected_type} token, got {payload.get('type')}")

    return payload


# ─── Wallet Signature Verification ──────────────────────


def verify_wallet_signature(
    wallet_address: str, message: str, signature: str
) -> bool:
    try:
        w3 = Web3()
        msg = encode_defunct(text=message)
        recovered_address = w3.eth.account.recover_message(msg, signature=signature)
        return recovered_address.lower() == wallet_address.lower()
    except Exception:
        return False


def generate_sign_message(wallet_address: str) -> str:
    timestamp = int(datetime.now(timezone.utc).timestamp())
    return f"Sign this message to authenticate with EulerX Network.\nWallet: {wallet_address}\nTimestamp: {timestamp}"
