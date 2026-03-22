"""Fix bybit_signals UUID column types to CHAR(36)

The bybit_signals table was created with sa.Uuid() but the GUID()
TypeDecorator uses CHAR(36). This mismatch causes insert failures
on PostgreSQL. This migration converts the columns.

Revision ID: c1d2e3f4g5h6
Revises: b1c2d3e4f5g6
Create Date: 2026-03-22

"""

from alembic import op
import sqlalchemy as sa

revision = "c1d2e3f4g5h6"
down_revision = "b1c2d3e4f5g6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the FK constraint first
    op.drop_constraint(
        "fk_executions_bybit_signal_id", "executions", type_="foreignkey"
    )

    # Convert bybit_signals.id from uuid to CHAR(36)
    op.alter_column(
        "bybit_signals",
        "id",
        existing_type=sa.Uuid(),
        type_=sa.CHAR(36),
        existing_nullable=False,
        postgresql_using="id::text",
    )

    # Convert executions.bybit_signal_id from uuid to CHAR(36)
    op.alter_column(
        "executions",
        "bybit_signal_id",
        existing_type=sa.Uuid(),
        type_=sa.CHAR(36),
        existing_nullable=True,
        postgresql_using="bybit_signal_id::text",
    )

    # Recreate the FK constraint
    op.create_foreign_key(
        "fk_executions_bybit_signal_id",
        "executions",
        "bybit_signals",
        ["bybit_signal_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_executions_bybit_signal_id", "executions", type_="foreignkey"
    )
    op.alter_column(
        "executions",
        "bybit_signal_id",
        existing_type=sa.CHAR(36),
        type_=sa.Uuid(),
        existing_nullable=True,
        postgresql_using="bybit_signal_id::uuid",
    )
    op.alter_column(
        "bybit_signals",
        "id",
        existing_type=sa.CHAR(36),
        type_=sa.Uuid(),
        existing_nullable=False,
        postgresql_using="id::uuid",
    )
    op.create_foreign_key(
        "fk_executions_bybit_signal_id",
        "executions",
        "bybit_signals",
        ["bybit_signal_id"],
        ["id"],
        ondelete="CASCADE",
    )
