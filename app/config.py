import os
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.environ.get("ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "EulerX "
    app_version: str = "0.1.0"
    debug: bool = False
    environment: str = Field(
        default="development",
        description="Application environment: development, staging, production",
    )
    api_v1_prefix: str = "/api/v1"

    # Database
    database_url: str = Field(
        ...,
        description="PostgreSQL async connection string (required)",
    )

    # JWT
    jwt_secret_key: str = Field(
        ...,
        description="JWT signing key (required, min 32 chars)",
    )
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    # Wallet Encryption
    wallet_encryption_key: str = Field(
        ...,
        description="Fernet symmetric key for encrypting generated wallet private keys (required)",
    )

    # Telegram Token Encryption (separate from wallet key)
    telegram_encryption_key: str = Field(
        default="",
        description="Fernet key for Telegram bot tokens. Falls back to wallet_encryption_key if empty.",
    )

    # Sentry
    sentry_dsn: str = Field(
        default="",
        description="Sentry DSN for error tracking. Empty = disabled.",
    )

    @model_validator(mode="after")
    def _validate_critical_settings(self):
        if self.environment == "production":
            if len(self.jwt_secret_key) < 32:
                raise ValueError(
                    "JWT_SECRET_KEY must be at least 32 characters in production"
                )
            if not self.wallet_encryption_key:
                raise ValueError("WALLET_ENCRYPTION_KEY is required in production")
        return self

    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_models: str = (
        "anthropic/claude-sonnet-4-20250514,openai/gpt-4o,google/gemini-2.0-flash"
    )

    @property
    def openrouter_model_list(self) -> list[str]:
        return [m.strip() for m in self.openrouter_models.split(",") if m.strip()]

    # Hyperliquid
    hyperliquid_api_url: str = "https://api.hyperliquid.xyz"
    hyperliquid_testnet: bool = True

    # NOWPayments
    nowpayments_api_key: str = ""
    nowpayments_ipn_secret: str = ""
    nowpayments_base_url: str = "https://api.nowpayments.io/v1"

    # CORS
    cors_origins: str = "http://localhost:3002"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # Rate Limiting
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60

    # Admin
    admin_wallet_addresses: str = ""

    @property
    def admin_wallet_list(self) -> list[str]:
        if not self.admin_wallet_addresses:
            return []
        return [a.strip() for a in self.admin_wallet_addresses.split(",") if a.strip()]

    # ATE
    ate_max_executions_per_hour: int = 10
    ate_confidence_threshold: float = 0.75
    ate_default_leverage: float = 1.0
    ate_max_drawdown_percent: float = 10.0

    # Subscription
    subscription_grace_period_days: int = 3

    # Redis / Celery
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis URL for Celery broker and result backend",
    )
    celery_task_always_eager: bool = Field(
        default=False,
        description="Run Celery tasks synchronously in-process (for testing)",
    )
    celery_task_time_limit: int = Field(
        default=600,
        description="Hard time limit per task in seconds",
    )
    celery_task_soft_time_limit: int = Field(
        default=540,
        description="Soft time limit per task in seconds",
    )

    # ZeptoMail / Email
    zeptomail_api_url: str = Field(
        default="https://api.zeptomail.com/v1.1/email",
        description="ZeptoMail transactional email API endpoint",
    )
    zeptomail_token: str = Field(
        default="",
        description="ZeptoMail API token (Zoho-enczapikey)",
    )
    zeptomail_from_email: str = Field(
        default="noreply@eulerx.io",
        description="Sender email address for transactional emails",
    )
    zeptomail_from_name: str = Field(
        default="EulerX ",
        description="Sender display name for transactional emails",
    )
    email_verification_expiry_minutes: int = Field(
        default=30,
        description="How long email verification codes are valid (minutes)",
    )
    frontend_url: str = Field(
        default="https://eulerx.io",
        description="Frontend URL for email links",
    )
    backend_url: str = Field(
        default="https://api.eulerx.io",
        description="Backend base URL used for webhook callbacks (e.g. NOWPayments IPN)",
    )

    # Notification Rate Limiting
    notification_rate_limit_per_user: int = Field(
        default=10,
        description="Max notifications per user per type within the window",
    )
    notification_rate_limit_window_seconds: int = Field(
        default=300,
        description="Rate limit sliding window in seconds (5 minutes)",
    )

    # Analysis Pipeline
    analysis_schedule_hours: int = Field(
        default=2,
        description="How often to run the analysis pipeline (hours)",
    )
    analysis_top_symbols_limit: int = Field(
        default=5,
        description="Number of top symbols to analyze per run",
    )
    analysis_max_retries: int = Field(
        default=3,
        description="Max retries for failed analysis tasks",
    )
    analysis_retry_backoff: int = Field(
        default=60,
        description="Base retry delay in seconds (exponential backoff)",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
