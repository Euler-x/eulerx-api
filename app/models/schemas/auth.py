import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field

from app.models.enums import WalletType


class WalletConnectRequest(BaseModel):
    wallet_address: str = Field(..., min_length=42, max_length=42, pattern=r"^0x[0-9a-fA-F]{40}$")
    message: str = Field(..., min_length=1)
    signature: str = Field(..., min_length=1)


class WalletGenerateRequest(BaseModel):
    referral_code: Optional[str] = None


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: uuid.UUID
    wallet_address_hash: str
    wallet_type: WalletType
    is_admin: bool
    is_active: bool = True
    email: Optional[str] = None
    email_verified: bool = False
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


class SignMessageResponse(BaseModel):
    message: str
    wallet_address: str


class EmailSubmitRequest(BaseModel):
    email: EmailStr


class EmailVerifyRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=6)


class EmailVerificationResponse(BaseModel):
    message: str
    email_verified: bool
