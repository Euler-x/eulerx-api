import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.types import GUID

from app.db.base import Base, TimestampMixin
from app.models.enums import RiskProfile, StrategyTimeframe, StrategyType

if TYPE_CHECKING:
    from app.models.execution import Execution
    from app.models.signal import Signal
    from app.models.user import User


class Strategy(Base, TimestampMixin):
    __tablename__ = "strategies"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    strategy_type: Mapped[StrategyType] = mapped_column(
        SAEnum(
            StrategyType,
            name="strategy_type_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    risk_profile: Mapped[RiskProfile] = mapped_column(
        SAEnum(
            RiskProfile,
            name="risk_profile_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    leverage_limit: Mapped[float] = mapped_column(Float, default=1.0)
    max_positions: Mapped[int] = mapped_column(Integer, default=5)
    allocation_pct: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    max_drawdown_percent: Mapped[float] = mapped_column(Float, default=10.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)

    # Risk management
    daily_loss_cap_percent: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, default=None
    )
    target_volatility: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, default=None
    )

    # Strategy detail
    expected_volatility: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, default=None
    )
    timeframe: Mapped[Optional[StrategyTimeframe]] = mapped_column(
        SAEnum(
            StrategyTimeframe,
            name="strategy_timeframe_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=True,
        default=None,
    )
    target_return_min: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, default=None
    )
    target_return_max: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, default=None
    )

    # Exchange targeting: "hyperliquid", "bybit", or "both" (default)
    target_exchange: Mapped[str] = mapped_column(
        String(20), nullable=False, default="both", server_default="both"
    )

    # Auto-pause tracking
    paused_reason: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, default=None
    )
    paused_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    user: Mapped["User"] = relationship(back_populates="strategies")
    signals: Mapped[list["Signal"]] = relationship(
        back_populates="strategy", cascade="all, delete-orphan"
    )
    executions: Mapped[list["Execution"]] = relationship(
        back_populates="strategy", cascade="all, delete-orphan"
    )
