import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Float, ForeignKey, Integer, Numeric, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.types import GUID

from app.db.base import Base, TimestampMixin
from app.models.enums import RiskProfile, StrategyType

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
        SAEnum(StrategyType, name="strategy_type_enum"), nullable=False
    )
    risk_profile: Mapped[RiskProfile] = mapped_column(
        SAEnum(RiskProfile, name="risk_profile_enum"), nullable=False
    )
    leverage_limit: Mapped[float] = mapped_column(Float, default=1.0)
    max_positions: Mapped[int] = mapped_column(Integer, default=5)
    capital_allocation: Mapped[float] = mapped_column(
        Numeric(precision=18, scale=8), nullable=False
    )
    max_drawdown_percent: Mapped[float] = mapped_column(Float, default=10.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship(back_populates="strategies")
    signals: Mapped[list["Signal"]] = relationship(
        back_populates="strategy", cascade="all, delete-orphan"
    )
    executions: Mapped[list["Execution"]] = relationship(
        back_populates="strategy", cascade="all, delete-orphan"
    )
