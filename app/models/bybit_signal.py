import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Float, Index, JSON, Numeric, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.types import GUID

from app.db.base import Base, TimestampMixin
from app.models.enums import SignalDirection, SignalStatus

if TYPE_CHECKING:
    from app.models.execution import Execution


class BybitSignal(Base, TimestampMixin):
    __tablename__ = "bybit_signals"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    direction: Mapped[SignalDirection] = mapped_column(
        SAEnum(
            SignalDirection,
            name="signal_direction_enum",
            create_type=False,
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(
        Numeric(precision=18, scale=8), nullable=False
    )
    stop_loss: Mapped[Optional[float]] = mapped_column(
        Numeric(precision=18, scale=8), nullable=True
    )
    take_profit: Mapped[Optional[float]] = mapped_column(
        Numeric(precision=18, scale=8), nullable=True
    )
    risk_reward_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    indicators: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    status: Mapped[SignalStatus] = mapped_column(
        SAEnum(
            SignalStatus,
            name="signal_status_enum",
            create_type=False,
            values_callable=lambda obj: [e.value for e in obj],
        ),
        default=SignalStatus.NEW,
        nullable=False,
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    model_responses: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    executions: Mapped[list["Execution"]] = relationship(
        back_populates="bybit_signal",
        foreign_keys="[Execution.bybit_signal_id]",
        cascade="all, delete-orphan",
    )

    __table_args__ = (Index("ix_bybit_signals_status_created", "status", "created_at"),)
