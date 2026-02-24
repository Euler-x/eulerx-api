"""Add strategy risk management and detail fields

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2026-02-24

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6g7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create the strategy_timeframe_enum type
    strategy_timeframe_enum = sa.Enum(
        "scalping",
        "intraday",
        "swing",
        "position",
        name="strategy_timeframe_enum",
    )
    strategy_timeframe_enum.create(op.get_bind(), checkfirst=True)

    # Risk management columns
    op.add_column(
        "strategies",
        sa.Column("daily_loss_cap_percent", sa.Float(), nullable=True),
    )
    op.add_column(
        "strategies",
        sa.Column("target_volatility", sa.Float(), nullable=True),
    )

    # Strategy detail columns
    op.add_column(
        "strategies",
        sa.Column("expected_volatility", sa.Float(), nullable=True),
    )
    op.add_column(
        "strategies",
        sa.Column(
            "timeframe",
            sa.Enum(
                "scalping",
                "intraday",
                "swing",
                "position",
                name="strategy_timeframe_enum",
                create_type=False,
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "strategies",
        sa.Column("target_return_min", sa.Float(), nullable=True),
    )
    op.add_column(
        "strategies",
        sa.Column("target_return_max", sa.Float(), nullable=True),
    )

    # Auto-pause tracking columns
    op.add_column(
        "strategies",
        sa.Column("paused_reason", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "strategies",
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("strategies", "paused_at")
    op.drop_column("strategies", "paused_reason")
    op.drop_column("strategies", "target_return_max")
    op.drop_column("strategies", "target_return_min")
    op.drop_column("strategies", "timeframe")
    op.drop_column("strategies", "expected_volatility")
    op.drop_column("strategies", "target_volatility")
    op.drop_column("strategies", "daily_loss_cap_percent")

    # Drop the enum type
    sa.Enum(name="strategy_timeframe_enum").drop(op.get_bind(), checkfirst=True)
