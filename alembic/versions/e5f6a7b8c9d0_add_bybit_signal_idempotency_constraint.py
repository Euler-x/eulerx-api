"""Add unique constraint for Bybit signal idempotency

The existing uq_execution_signal_strategy constraint on (signal_id, strategy_id)
does not protect Bybit signals because signal_id is always NULL for them —
PostgreSQL treats each NULL as distinct. This adds a parallel constraint on
(bybit_signal_id, strategy_id) to prevent duplicate Bybit executions.

Revision ID: e5f6a7b8c9d0
Revises: d2e3f4a5b6c7
Create Date: 2026-04-12

"""

from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_execution_bybit_signal_strategy",
        "executions",
        ["bybit_signal_id", "strategy_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_execution_bybit_signal_strategy",
        "executions",
        type_="unique",
    )
