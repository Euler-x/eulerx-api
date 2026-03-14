"""Ambassador program models: territories, commissions, bonuses, payouts, training."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.types import GUID
from app.db.base import Base, TimestampMixin
from app.models.enums import (
    BonusType,
    CommissionStatus,
    PayoutStatus,
    TerritoryType,
)
from app.utils.helpers import utc_now

if TYPE_CHECKING:
    from app.models.ambassador import Ambassador


class AmbassadorTerritory(Base, TimestampMixin):
    __tablename__ = "ambassador_territories"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    territory_type: Mapped[TerritoryType] = mapped_column(
        SAEnum(
            TerritoryType,
            name="territory_type_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    master_ambassador_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(),
        ForeignKey(
            "ambassadors.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_territory_master_ambassador",
        ),
        nullable=True,
        unique=True,
    )
    revenue_share_pct: Mapped[float] = mapped_column(Numeric(5, 2), default=5.0)

    master_ambassador: Mapped[Optional["Ambassador"]] = relationship(
        "Ambassador",
        foreign_keys=[master_ambassador_id],
        primaryjoin="AmbassadorTerritory.master_ambassador_id == Ambassador.id",
    )
    ambassadors: Mapped[list["Ambassador"]] = relationship(
        "Ambassador",
        back_populates="territory",
        foreign_keys="Ambassador.territory_id",
    )
    territory_commissions: Mapped[list["TerritoryCommission"]] = relationship(
        back_populates="territory", cascade="all, delete-orphan"
    )


class AmbassadorCommission(Base, TimestampMixin):
    __tablename__ = "ambassador_commissions"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    ambassador_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("ambassadors.id", ondelete="CASCADE"),
        nullable=False,
    )
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    active_referral_count: Mapped[int] = mapped_column(Integer, default=0)
    commission_rate: Mapped[Optional[float]] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    commission_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    status: Mapped[CommissionStatus] = mapped_column(
        SAEnum(
            CommissionStatus,
            name="commission_status_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        default=CommissionStatus.PENDING,
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    ambassador: Mapped["Ambassador"] = relationship(back_populates="commissions")

    __table_args__ = (
        UniqueConstraint(
            "ambassador_id", "month", "year", name="uq_commission_ambassador_month_year"
        ),
    )


class AmbassadorBonus(Base, TimestampMixin):
    __tablename__ = "ambassador_bonuses"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    ambassador_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("ambassadors.id", ondelete="CASCADE"),
        nullable=False,
    )
    bonus_type: Mapped[BonusType] = mapped_column(
        SAEnum(
            BonusType,
            name="bonus_type_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    period: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    status: Mapped[CommissionStatus] = mapped_column(
        SAEnum(
            CommissionStatus,
            name="commission_status_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        default=CommissionStatus.PENDING,
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    ambassador: Mapped["Ambassador"] = relationship(back_populates="bonuses")


class AmbassadorPayout(Base, TimestampMixin):
    __tablename__ = "ambassador_payouts"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    ambassador_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("ambassadors.id", ondelete="CASCADE"),
        nullable=False,
    )
    total_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    commission_ids: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    bonus_ids: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    status: Mapped[PayoutStatus] = mapped_column(
        SAEnum(
            PayoutStatus,
            name="payout_status_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        default=PayoutStatus.PENDING,
    )
    payout_address: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    admin_notes: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    processed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    ambassador: Mapped["Ambassador"] = relationship(back_populates="payouts")


class AmbassadorTrainingCompletion(Base, TimestampMixin):
    __tablename__ = "ambassador_training_completions"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    ambassador_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("ambassadors.id", ondelete="CASCADE"),
        nullable=False,
    )
    module_name: Mapped[str] = mapped_column(String(100), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    completed_by_admin: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    ambassador: Mapped["Ambassador"] = relationship(
        back_populates="training_completions"
    )

    __table_args__ = (
        UniqueConstraint(
            "ambassador_id", "module_name", name="uq_training_ambassador_module"
        ),
    )


class TerritoryCommission(Base, TimestampMixin):
    __tablename__ = "territory_commissions"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    territory_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("ambassador_territories.id", ondelete="CASCADE"),
        nullable=False,
    )
    master_ambassador_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("ambassadors.id", ondelete="CASCADE"),
        nullable=False,
    )
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    territory_volume_usd: Mapped[Optional[float]] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    ambassador_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    revenue_share_pct: Mapped[Optional[float]] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    commission_amount: Mapped[Optional[float]] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    status: Mapped[CommissionStatus] = mapped_column(
        SAEnum(
            CommissionStatus,
            name="commission_status_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        default=CommissionStatus.PENDING,
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    territory: Mapped["AmbassadorTerritory"] = relationship(
        back_populates="territory_commissions"
    )

    __table_args__ = (
        UniqueConstraint(
            "territory_id", "month", "year", name="uq_territory_commission_month_year"
        ),
    )
