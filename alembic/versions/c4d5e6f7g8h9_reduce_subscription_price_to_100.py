"""Reduce EulerX subscription price to $100

Revision ID: c4d5e6f7g8h9
Revises: 2d303c8cd0b5
Create Date: 2026-05-16
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c4d5e6f7g8h9"
down_revision: Union[str, None] = "2d303c8cd0b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    timestamp = "NOW()" if bind.dialect.name == "postgresql" else "CURRENT_TIMESTAMP"
    op.execute(
        f"""
        UPDATE plans
        SET price_usd = 100.00,
            updated_at = {timestamp}
        WHERE price_usd = 250.00;
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    timestamp = "NOW()" if bind.dialect.name == "postgresql" else "CURRENT_TIMESTAMP"
    op.execute(
        f"""
        UPDATE plans
        SET price_usd = 250.00,
            updated_at = {timestamp}
        WHERE price_usd = 100.00
          AND lower(name) IN ('eulerx ate', 'ate pro');
        """
    )
