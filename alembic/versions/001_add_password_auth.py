"""Add password_hash column and make wallet fields nullable.

Revision ID: 001_add_password_auth
Revises:
Create Date: 2026-02-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "001_add_password_auth"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(255), nullable=True))
    op.alter_column("users", "wallet_address_hash", existing_type=sa.String(64), nullable=True)
    op.alter_column("users", "wallet_type", existing_type=sa.Enum("connected", "generated", name="wallet_type_enum"), nullable=True)


def downgrade() -> None:
    op.alter_column("users", "wallet_type", existing_type=sa.Enum("connected", "generated", name="wallet_type_enum"), nullable=False)
    op.alter_column("users", "wallet_address_hash", existing_type=sa.String(64), nullable=False)
    op.drop_column("users", "password_hash")
