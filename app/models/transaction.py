import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, Numeric, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.types import GUID

from app.db.base import Base, TimestampMixin
from app.models.enums import TransactionCategory, TransactionStatus

if TYPE_CHECKING:
    from app.models.user import User


class Transaction(Base, TimestampMixin):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category: Mapped[TransactionCategory] = mapped_column(
        SAEnum(TransactionCategory, name="transaction_category_enum"), nullable=False
    )
    amount: Mapped[float] = mapped_column(
        Numeric(precision=18, scale=8), nullable=False
    )
    asset: Mapped[str] = mapped_column(String(20), nullable=False)
    wallet_address_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[TransactionStatus] = mapped_column(
        SAEnum(TransactionStatus, name="transaction_status_enum"),
        default=TransactionStatus.PENDING,
    )
    verification_link: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tx_hash: Mapped[Optional[str]] = mapped_column(String(66), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="transactions")
