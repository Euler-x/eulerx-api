import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, Numeric, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import GUID

if TYPE_CHECKING:
    from app.models.user import User


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    account_equity: Mapped[float] = mapped_column(
        Numeric(precision=18, scale=8),
        nullable=False,
        default=0,
        server_default="0",
    )
    available_balance: Mapped[float] = mapped_column(
        Numeric(precision=18, scale=8),
        nullable=False,
        default=0,
        server_default="0",
    )
    unrealized_pnl: Mapped[float] = mapped_column(
        Numeric(precision=18, scale=8),
        nullable=False,
        default=0,
        server_default="0",
    )
    total_balance: Mapped[float] = mapped_column(
        Numeric(precision=18, scale=8),
        nullable=False,
        default=0,
        server_default="0",
    )
    open_positions: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    exchange_breakdown: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON,
        nullable=True,
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="portfolio_snapshots")

    __table_args__ = (
        Index(
            "ix_portfolio_snapshots_user_captured_at",
            "user_id",
            "captured_at",
        ),
    )
