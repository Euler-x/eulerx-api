"""Schemas for Telegram notification configuration endpoints."""

from pydantic import BaseModel, Field


class TelegramConfigRequest(BaseModel):
    bot_token: str = Field(
        ..., min_length=30, max_length=255, description="Telegram Bot API token"
    )
    chat_id: str = Field(
        ..., min_length=1, max_length=64, description="Telegram chat ID"
    )


class TelegramConfigResponse(BaseModel):
    message: str
    telegram_configured: bool


class TelegramTestResponse(BaseModel):
    success: bool
    message: str
