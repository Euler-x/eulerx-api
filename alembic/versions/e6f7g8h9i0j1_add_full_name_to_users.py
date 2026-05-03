"""Add full_name column to users

Revision ID: e6f7g8h9i0j1
Revises: d5e6f7g8h9i0
Create Date: 2026-05-03
"""

import sqlalchemy as sa
from alembic import op

revision = "e6f7g8h9i0j1"
down_revision = "d5e6f7g8h9i0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS full_name VARCHAR(200);
        """)
    else:
        with op.batch_alter_table("users") as batch_op:
            try:
                batch_op.add_column(
                    sa.Column("full_name", sa.String(200), nullable=True)
                )
            except Exception:
                pass


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE users DROP COLUMN IF EXISTS full_name")
    else:
        with op.batch_alter_table("users") as batch_op:
            batch_op.drop_column("full_name")
