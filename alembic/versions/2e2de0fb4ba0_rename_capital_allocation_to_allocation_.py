"""rename capital_allocation to allocation_pct

Revision ID: 2e2de0fb4ba0
Revises: f1a2b3c4d5e6
Create Date: 2026-03-09 14:10:42.810128

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2e2de0fb4ba0"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add unique constraint for signal-strategy idempotency
    op.create_unique_constraint(
        "uq_execution_signal_strategy", "executions", ["signal_id", "strategy_id"]
    )

    # Rename capital_allocation → allocation_pct and convert dollar values to percentages
    op.alter_column(
        "strategies",
        "capital_allocation",
        new_column_name="allocation_pct",
        type_=sa.Float(),
        existing_type=sa.Numeric(precision=18, scale=8),
        existing_nullable=False,
    )

    # Set default 100% for any existing rows, then clamp to valid range
    op.execute(
        "UPDATE strategies SET allocation_pct = 100.0 WHERE allocation_pct > 100.0 OR allocation_pct < 1.0"
    )


def downgrade() -> None:
    op.alter_column(
        "strategies",
        "allocation_pct",
        new_column_name="capital_allocation",
        type_=sa.Numeric(precision=18, scale=8),
        existing_type=sa.Float(),
        existing_nullable=False,
    )
    op.drop_constraint("uq_execution_signal_strategy", "executions", type_="unique")
