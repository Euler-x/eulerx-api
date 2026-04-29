"""Add execution close tracking fields and portfolio_snapshots table

Revision ID: a9b8c7d6e5f4
Revises: e5f6a7b8c9d0
Create Date: 2026-04-29

"""

import sqlalchemy as sa
from alembic import op

from app.db.types import GUID

revision = "a9b8c7d6e5f4"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Execution close-tracking columns ────────────────────────────
    op.add_column(
        "executions",
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "executions",
        sa.Column("close_reason", sa.String(50), nullable=True),
    )
    op.add_column(
        "executions",
        sa.Column("close_source", sa.String(50), nullable=True),
    )

    # ── portfolio_snapshots table ────────────────────────────────────
    op.create_table(
        "portfolio_snapshots",
        sa.Column("id", GUID(length=36), nullable=False),
        sa.Column("user_id", GUID(length=36), nullable=False),
        sa.Column(
            "account_equity",
            sa.Numeric(precision=18, scale=8),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "available_balance",
            sa.Numeric(precision=18, scale=8),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "unrealized_pnl",
            sa.Numeric(precision=18, scale=8),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_balance",
            sa.Numeric(precision=18, scale=8),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "open_positions",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("exchange_breakdown", sa.JSON(), nullable=True),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_portfolio_snapshots_user_captured_at",
        "portfolio_snapshots",
        ["user_id", "captured_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_portfolio_snapshots_user_captured_at",
        table_name="portfolio_snapshots",
    )
    op.drop_table("portfolio_snapshots")
    op.drop_column("executions", "close_source")
    op.drop_column("executions", "close_reason")
    op.drop_column("executions", "closed_at")
