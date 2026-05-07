"""Add Binance integration — binance_signals table, user keys, execution FK

Revision ID: f2g3h4i5j6k7
Revises: e6f7g8h9i0j1
Create Date: 2026-05-07

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f2g3h4i5j6k7"
down_revision = "e6f7g8h9i0j1"
branch_labels = None
depends_on = None

# Existing PG enum types — create_type=False so we don't try to re-create them.
direction_col_type = postgresql.ENUM(
    "buy", "sell", "hold", name="signal_direction_enum", create_type=False
)
status_col_type = postgresql.ENUM(
    "new",
    "executing",
    "filled",
    "expired",
    "cancelled",
    name="signal_status_enum",
    create_type=False,
)


def upgrade() -> None:
    # ── exchange_enum: add 'binance' value ────────────────────────────
    # ALTER TYPE … ADD VALUE is transactional in PG 12+. Use IF NOT EXISTS
    # so re-running the migration is idempotent.
    op.execute("ALTER TYPE exchange_enum ADD VALUE IF NOT EXISTS 'binance'")

    # ── users: Binance API credentials ───────────────────────────────
    op.add_column(
        "users", sa.Column("binance_api_key_encrypted", sa.Text(), nullable=True)
    )
    op.add_column(
        "users", sa.Column("binance_api_secret_encrypted", sa.Text(), nullable=True)
    )
    op.add_column(
        "users",
        sa.Column(
            "binance_testnet", sa.Boolean(), nullable=False, server_default="false"
        ),
    )

    # ── binance_signals: separate table (mirrors bybit_signals) ──────
    op.create_table(
        "binance_signals",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("symbol", sa.String(30), nullable=False, index=True),
        sa.Column("direction", direction_col_type, nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("entry_price", sa.Numeric(18, 8), nullable=False),
        sa.Column("stop_loss", sa.Numeric(18, 8), nullable=True),
        sa.Column("take_profit", sa.Numeric(18, 8), nullable=True),
        sa.Column("risk_reward_ratio", sa.Float(), nullable=True),
        sa.Column("indicators", sa.JSON(), nullable=True),
        sa.Column("status", status_col_type, nullable=False, server_default="new"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model_responses", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_binance_signals_status_created",
        "binance_signals",
        ["status", "created_at"],
    )

    # ── executions: add binance_signal_id FK + index + unique constraint
    op.add_column(
        "executions",
        sa.Column("binance_signal_id", sa.CHAR(36), nullable=True),
    )
    op.create_index(
        "ix_executions_binance_signal_id", "executions", ["binance_signal_id"]
    )
    op.create_foreign_key(
        "fk_executions_binance_signal_id",
        "executions",
        "binance_signals",
        ["binance_signal_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_execution_binance_signal_strategy",
        "executions",
        ["binance_signal_id", "strategy_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_execution_binance_signal_strategy", "executions", type_="unique"
    )
    op.drop_constraint(
        "fk_executions_binance_signal_id", "executions", type_="foreignkey"
    )
    op.drop_index("ix_executions_binance_signal_id", "executions")
    op.drop_column("executions", "binance_signal_id")
    op.drop_index("ix_binance_signals_status_created", "binance_signals")
    op.drop_table("binance_signals")
    op.drop_column("users", "binance_testnet")
    op.drop_column("users", "binance_api_secret_encrypted")
    op.drop_column("users", "binance_api_key_encrypted")
    # NOTE: PostgreSQL does not support removing enum values — downgrade cannot
    # remove 'binance' from exchange_enum without a full type rebuild.
