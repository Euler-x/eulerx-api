import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.types import GUID

from app.db.base import Base
from app.models.enums import ExecutionStatus, OrderType, SignalDirection

if TYPE_CHECKING:
    from app.models.signal import Signal
    from app.models.strategy import Strategy
    from app.models.user import User


class Execution(Base):
    __tablename__ = "executions"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    signal_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("signals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("strategies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    wallet_address_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    order_type: Mapped[OrderType] = mapped_column(
        SAEnum(
            OrderType,
            name="order_type_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    direction: Mapped[SignalDirection] = mapped_column(
        SAEnum(
            SignalDirection,
            name="signal_direction_enum",
            create_type=False,
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    entry_price: Mapped[float] = mapped_column(
        Numeric(precision=18, scale=8), nullable=False
    )
    exit_price: Mapped[Optional[float]] = mapped_column(
        Numeric(precision=18, scale=8), nullable=True
    )
    quantity: Mapped[float] = mapped_column(
        Numeric(precision=18, scale=8), nullable=False
    )
    leverage: Mapped[float] = mapped_column(Float, default=1.0)
    pnl: Mapped[Optional[float]] = mapped_column(
        Numeric(precision=18, scale=8), nullable=True
    )
    tx_hash: Mapped[Optional[str]] = mapped_column(String(66), nullable=True)
    status: Mapped[ExecutionStatus] = mapped_column(
        SAEnum(
            ExecutionStatus,
            name="execution_status_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        default=ExecutionStatus.PENDING,
        nullable=False,
    )
    executed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    signal: Mapped["Signal"] = relationship(back_populates="executions")
    user: Mapped["User"] = relationship(back_populates="executions")
    strategy: Mapped["Strategy"] = relationship(back_populates="executions")

    __table_args__ = (
        Index("ix_executions_user_status", "user_id", "status"),
        UniqueConstraint(
            "signal_id",
            "strategy_id",
            name="uq_execution_signal_strategy",
        ),
    )
