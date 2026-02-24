import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field

from app.models.enums import WalletType


class WalletConnectRequest(BaseModel):
    """Connect a Hyperliquid wallet using an Agent/API wallet key."""
    wallet_address: str = Field(
        ..., min_length=42, max_length=42, pattern=r"^0x[0-9a-fA-F]{40}$",
        description="Your Hyperliquid wallet address (main account)",
    )
    agent_private_key: str = Field(
        ..., min_length=1,
        description="Agent/API wallet private key (created at app.hyperliquid.xyz/API)",
    )


class WalletGenerateRequest(BaseModel):
    referral_code: Optional[str] = None


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    referral_code: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: uuid.UUID
    wallet_address_hash: Optional[str] = None
    wallet_type: Optional[WalletType] = None
    is_admin: bool
    is_active: bool = True
    email: Optional[str] = None
    email_verified: bool = False
    has_wallet: bool = False
    telegram_configured: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse


class WalletGenerateResponse(BaseModel):
    wallet_address: str
    private_key: str = Field(
        ..., description="Shown once. User must save this securely."
    )
    auth: AuthResponse



class EmailSubmitRequest(BaseModel):
    email: EmailStr


class EmailVerifyRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=6)


class EmailVerificationResponse(BaseModel):
    message: str
    email_verified: bool
