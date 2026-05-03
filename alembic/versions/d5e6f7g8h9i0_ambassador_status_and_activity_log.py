"""Add ambassador status control and activity log

Revision ID: d5e6f7g8h9i0
Revises: b9c8d7e6f5a4
Create Date: 2026-05-03

Changes:
- Add ambassador_status_enum (active, suspended, under_review)
- Add ambassadors.status column (default: active)
- Add ambassadors.admin_notes column (Text, nullable)
- Add activity_event_type_enum (17 event types)
- Create ambassador_activity_logs table (append-only audit log)
"""

import sqlalchemy as sa
from alembic import op

revision = "d5e6f7g8h9i0"
down_revision = "b9c8d7e6f5a4"
branch_labels = None
depends_on = None

ACTIVITY_EVENT_TYPES = [
    "registered",
    "rank_changed",
    "commission_calculated",
    "commission_paid",
    "bonus_awarded",
    "bonus_paid",
    "payout_created",
    "payout_status_changed",
    "payout_cancelled",
    "referral_joined",
    "travel_awarded",
    "travel_status_changed",
    "training_completed",
    "training_removed",
    "status_changed",
    "admin_note_added",
    "pool_calculated",
]

AMBASSADOR_STATUSES = ["active", "suspended", "under_review"]


def upgrade() -> None:
    bind = op.get_bind()

    # ── 1. ambassador_status_enum + ambassadors.status column ─────────────────
    if bind.dialect.name == "postgresql":
        op.execute(
            f"CREATE TYPE ambassador_status_enum AS ENUM ({', '.join(repr(v) for v in AMBASSADOR_STATUSES)})"
        )
        op.add_column(
            "ambassadors",
            sa.Column(
                "status",
                sa.Enum(
                    *AMBASSADOR_STATUSES,
                    name="ambassador_status_enum",
                    create_type=False,
                ),
                nullable=False,
                server_default="active",
            ),
        )
    else:
        # SQLite: use plain string column
        op.add_column(
            "ambassadors",
            sa.Column(
                "status",
                sa.String(20),
                nullable=False,
                server_default="active",
            ),
        )

    # ── 2. ambassadors.admin_notes column ─────────────────────────────────────
    op.add_column(
        "ambassadors",
        sa.Column("admin_notes", sa.Text, nullable=True),
    )

    # ── 3. activity_event_type_enum + ambassador_activity_logs table ──────────
    if bind.dialect.name == "postgresql":
        op.execute(
            f"CREATE TYPE activity_event_type_enum AS ENUM ({', '.join(repr(v) for v in ACTIVITY_EVENT_TYPES)})"
        )
        event_type_col = sa.Column(
            "event_type",
            sa.Enum(
                *ACTIVITY_EVENT_TYPES,
                name="activity_event_type_enum",
                create_type=False,
            ),
            nullable=False,
        )
    else:
        event_type_col = sa.Column(
            "event_type",
            sa.String(40),
            nullable=False,
        )

    op.create_table(
        "ambassador_activity_logs",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column(
            "ambassador_id",
            sa.CHAR(36),
            sa.ForeignKey("ambassadors.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        event_type_col,
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("related_id", sa.String(36), nullable=True),
        sa.Column("actor", sa.String(50), nullable=False, server_default="system"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            index=True,
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_table("ambassador_activity_logs")

    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS activity_event_type_enum")

    op.drop_column("ambassadors", "admin_notes")
    op.drop_column("ambassadors", "status")

    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS ambassador_status_enum")
