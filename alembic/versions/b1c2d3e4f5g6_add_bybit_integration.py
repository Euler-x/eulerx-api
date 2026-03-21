"""Add Bybit integration — separate bybit_signals table + user keys

Revision ID: b1c2d3e4f5g6
Revises: e9f8a7b6c5d4
Create Date: 2026-03-21

"""

from alembic import op
import sqlalchemy as sa

revision = "b1c2d3e4f5g6"
down_revision = "e9f8a7b6c5d4"
branch_labels = None
depends_on = None

exchange_enum = sa.Enum("hyperliquid", "bybit", name="exchange_enum")
# These enums already exist in the DB — reference without creating
signal_direction_enum = sa.Enum(
    "buy", "sell", "hold", name="signal_direction_enum", create_type=False
)
signal_status_enum = sa.Enum(
    "new",
    "executing",
    "filled",
    "expired",
    "cancelled",
    name="signal_status_enum",
    create_type=False,
)


def upgrade() -> None:
    # Create the exchange enum type
    exchange_enum.create(op.get_bind(), checkfirst=True)

    # ── User: Bybit API credentials ────────────────────────────────
    op.add_column(
        "users", sa.Column("bybit_api_key_encrypted", sa.Text(), nullable=True)
    )
    op.add_column(
        "users", sa.Column("bybit_api_secret_encrypted", sa.Text(), nullable=True)
    )
    op.add_column(
        "users",
        sa.Column(
            "bybit_testnet", sa.Boolean(), nullable=False, server_default="false"
        ),
    )

    # ── Bybit Signals: separate table ──────────────────────────────
    op.create_table(
        "bybit_signals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("symbol", sa.String(30), nullable=False, index=True),
        sa.Column("direction", signal_direction_enum, nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("entry_price", sa.Numeric(18, 8), nullable=False),
        sa.Column("stop_loss", sa.Numeric(18, 8), nullable=True),
        sa.Column("take_profit", sa.Numeric(18, 8), nullable=True),
        sa.Column("risk_reward_ratio", sa.Float(), nullable=True),
        sa.Column("indicators", sa.JSON(), nullable=True),
        sa.Column("status", signal_status_enum, nullable=False, server_default="new"),
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
        "ix_bybit_signals_status_created",
        "bybit_signals",
        ["status", "created_at"],
    )

    # ── Execution: add exchange, order ID, and bybit_signal FK ─────
    op.add_column(
        "executions",
        sa.Column(
            "exchange",
            exchange_enum,
            nullable=False,
            server_default="hyperliquid",
        ),
    )
    op.add_column(
        "executions",
        sa.Column("exchange_order_id", sa.String(100), nullable=True),
    )
    op.add_column(
        "executions",
        sa.Column("bybit_signal_id", sa.Uuid(), nullable=True),
    )
    op.create_index("ix_executions_bybit_signal_id", "executions", ["bybit_signal_id"])
    op.create_foreign_key(
        "fk_executions_bybit_signal_id",
        "executions",
        "bybit_signals",
        ["bybit_signal_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Make signal_id nullable (Bybit executions won't have one)
    op.alter_column("executions", "signal_id", existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    op.alter_column("executions", "signal_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_constraint(
        "fk_executions_bybit_signal_id", "executions", type_="foreignkey"
    )
    op.drop_index("ix_executions_bybit_signal_id", "executions")
    op.drop_column("executions", "bybit_signal_id")
    op.drop_column("executions", "exchange_order_id")
    op.drop_column("executions", "exchange")
    op.drop_index("ix_bybit_signals_status_created", "bybit_signals")
    op.drop_table("bybit_signals")
    op.drop_column("users", "bybit_testnet")
    op.drop_column("users", "bybit_api_secret_encrypted")
    op.drop_column("users", "bybit_api_key_encrypted")
    exchange_enum.drop(op.get_bind(), checkfirst=True)
