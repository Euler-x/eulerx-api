"""Add wallet_address column for Hyperliquid integration

Revision ID: a1b2c3d4e5f6
Revises: 173bb1b573e6
Create Date: 2026-02-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "173bb1b573e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("wallet_address", sa.String(42), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "wallet_address")
