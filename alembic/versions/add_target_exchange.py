"""Add target_exchange to strategies

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4g5h6
Create Date: 2026-03-27
"""

from alembic import op
import sqlalchemy as sa

revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4g5h6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "strategies",
        sa.Column(
            "target_exchange", sa.String(20), nullable=False, server_default="both"
        ),
    )


def downgrade() -> None:
    op.drop_column("strategies", "target_exchange")
