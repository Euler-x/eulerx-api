"""add signal timestamp defaults

Revision ID: f3a4b5c6d7e8
Revises: c4d5e6f7g8h9
Create Date: 2026-05-29 14:35:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, None] = "c4d5e6f7g8h9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "bybit_signals",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
        server_default=sa.text("now()"),
    )
    op.alter_column(
        "binance_signals",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
        server_default=sa.text("now()"),
    )


def downgrade() -> None:
    op.alter_column(
        "binance_signals",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
        server_default=None,
    )
    op.alter_column(
        "bybit_signals",
        "updated_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
        server_default=None,
    )
