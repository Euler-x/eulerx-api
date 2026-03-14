import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, Integer, Numeric, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.types import GUID

from app.db.base import Base, TimestampMixin
from app.models.enums import AmbassadorRank

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.ambassador_programs import (
        AmbassadorTerritory,
        AmbassadorCommission,
        AmbassadorBonus,
        AmbassadorPayout,
        AmbassadorTrainingCompletion,
    )


class Ambassador(Base, TimestampMixin):
    __tablename__ = "ambassadors"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    rank: Mapped[AmbassadorRank] = mapped_column(
        SAEnum(
            AmbassadorRank,
            name="ambassador_rank_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        default=AmbassadorRank.SCOUT,
    )
    referral_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    referred_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(),
        ForeignKey("ambassadors.id", ondelete="SET NULL"),
        nullable=True,
    )
    team_size: Mapped[int] = mapped_column(Integer, default=0)
    total_referrals: Mapped[int] = mapped_column(Integer, default=0)
    rewards_earned: Mapped[float] = mapped_column(
        Numeric(precision=18, scale=8), default=0.0
    )
    payout_address: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    territory_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(),
        ForeignKey("ambassador_territories.id", ondelete="SET NULL"),
        nullable=True,
    )

    user: Mapped["User"] = relationship(back_populates="ambassador")
    referrals: Mapped[list["Ambassador"]] = relationship(
        back_populates="referrer",
        foreign_keys="Ambassador.referred_by",
    )
    referrer: Mapped[Optional["Ambassador"]] = relationship(
        back_populates="referrals",
        remote_side="Ambassador.id",
        foreign_keys="Ambassador.referred_by",
    )
    territory: Mapped[Optional["AmbassadorTerritory"]] = relationship(
        back_populates="ambassadors",
        foreign_keys="Ambassador.territory_id",
    )
    commissions: Mapped[list["AmbassadorCommission"]] = relationship(
        back_populates="ambassador", cascade="all, delete-orphan"
    )
    bonuses: Mapped[list["AmbassadorBonus"]] = relationship(
        back_populates="ambassador", cascade="all, delete-orphan"
    )
    payouts: Mapped[list["AmbassadorPayout"]] = relationship(
        back_populates="ambassador", cascade="all, delete-orphan"
    )
    training_completions: Mapped[list["AmbassadorTrainingCompletion"]] = relationship(
        back_populates="ambassador", cascade="all, delete-orphan"
    )
