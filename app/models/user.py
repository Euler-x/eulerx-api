import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, JSON, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.types import GUID

from app.db.base import Base, TimestampMixin
from app.models.enums import WalletType

if TYPE_CHECKING:
    from app.models.ambassador import Ambassador
    from app.models.execution import Execution
    from app.models.strategy import Strategy
    from app.models.billing import Subscription
    from app.models.support import SupportTicket
    from app.models.transaction import Transaction


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    wallet_address_hash: Mapped[Optional[str]] = mapped_column(
        String(64), unique=True, nullable=True, index=True
    )
    wallet_address: Mapped[Optional[str]] = mapped_column(String(42), nullable=True)
    wallet_type: Mapped[Optional[WalletType]] = mapped_column(
        SAEnum(
            WalletType,
            name="wallet_type_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=True,
    )
    encrypted_private_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )

    # Email verification
    email: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, index=True, nullable=True
    )
    email_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    email_verification_code: Mapped[Optional[str]] = mapped_column(
        String(6), nullable=True
    )
    email_verification_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Telegram notifications
    telegram_bot_token: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    telegram_chat_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Notification preferences (JSON: {category}_{channel} -> bool)
    notification_preferences: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True
    )

    @property
    def has_wallet(self) -> bool:
        return self.wallet_address_hash is not None

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    strategies: Mapped[list["Strategy"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    executions: Mapped[list["Execution"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    ambassador: Mapped[Optional["Ambassador"]] = relationship(
        back_populates="user", uselist=False
    )
    support_tickets: Mapped[list["SupportTicket"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
