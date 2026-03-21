"""Add Bybit integration columns

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

# Exchange enum values
exchange_enum = sa.Enum("hyperliquid", "bybit", name="exchange_enum")


def upgrade() -> None:
    # Create the exchange enum type
    exchange_enum.create(op.get_bind(), checkfirst=True)

    # User: Bybit API credentials
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

    # Signal: exchange source
    op.add_column(
        "signals",
        sa.Column(
            "exchange",
            exchange_enum,
            nullable=False,
            server_default="hyperliquid",
        ),
    )

    # Execution: exchange and order ID
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


def downgrade() -> None:
    op.drop_column("executions", "exchange_order_id")
    op.drop_column("executions", "exchange")
    op.drop_column("signals", "exchange")
    op.drop_column("users", "bybit_testnet")
    op.drop_column("users", "bybit_api_secret_encrypted")
    op.drop_column("users", "bybit_api_key_encrypted")
    exchange_enum.drop(op.get_bind(), checkfirst=True)
