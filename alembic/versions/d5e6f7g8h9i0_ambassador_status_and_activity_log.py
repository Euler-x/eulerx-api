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

All DDL is idempotent: uses IF NOT EXISTS / DO…EXCEPTION blocks so the
migration is safe to run even when types/columns/tables already exist
(e.g. if the app ran create_all on first boot before migrations caught up).
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


def _pg_create_type_if_not_exists(type_name: str, values: list[str]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"""
DO $$ BEGIN
    CREATE TYPE {type_name} AS ENUM ({quoted});
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
"""


def upgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        # ── 1. ambassador_status_enum ────────────────────────────────────────
        op.execute(
            _pg_create_type_if_not_exists("ambassador_status_enum", AMBASSADOR_STATUSES)
        )

        # ── 2. ambassadors.status column ─────────────────────────────────────
        op.execute("""
            ALTER TABLE ambassadors
            ADD COLUMN IF NOT EXISTS status ambassador_status_enum NOT NULL DEFAULT 'active';
        """)

        # ── 3. ambassadors.admin_notes column ────────────────────────────────
        op.execute("""
            ALTER TABLE ambassadors
            ADD COLUMN IF NOT EXISTS admin_notes TEXT;
        """)

        # ── 4. activity_event_type_enum ──────────────────────────────────────
        op.execute(
            _pg_create_type_if_not_exists(
                "activity_event_type_enum", ACTIVITY_EVENT_TYPES
            )
        )

        # ── 5. ambassador_activity_logs table ────────────────────────────────
        op.execute("""
            CREATE TABLE IF NOT EXISTS ambassador_activity_logs (
                id          CHAR(36) PRIMARY KEY,
                ambassador_id CHAR(36) NOT NULL
                    REFERENCES ambassadors(id) ON DELETE CASCADE,
                event_type  activity_event_type_enum NOT NULL,
                description VARCHAR(500) NOT NULL,
                amount      NUMERIC(12, 2),
                related_id  VARCHAR(36),
                actor       VARCHAR(50) NOT NULL DEFAULT 'system',
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        # Indexes (IF NOT EXISTS avoids re-run errors)
        op.execute("""
            CREATE INDEX IF NOT EXISTS ix_ambassador_activity_logs_ambassador_id
            ON ambassador_activity_logs(ambassador_id);
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS ix_ambassador_activity_logs_event_type
            ON ambassador_activity_logs(event_type);
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS ix_ambassador_activity_logs_created_at
            ON ambassador_activity_logs(created_at);
        """)

    else:
        # SQLite path (tests / local dev)
        with op.batch_alter_table("ambassadors") as batch_op:
            try:
                batch_op.add_column(
                    sa.Column(
                        "status", sa.String(20), nullable=False, server_default="active"
                    )
                )
            except Exception:
                pass
            try:
                batch_op.add_column(sa.Column("admin_notes", sa.Text, nullable=True))
            except Exception:
                pass

        op.create_table(
            "ambassador_activity_logs",
            sa.Column("id", sa.CHAR(36), primary_key=True),
            sa.Column("ambassador_id", sa.CHAR(36), nullable=False),
            sa.Column("event_type", sa.String(40), nullable=False),
            sa.Column("description", sa.String(500), nullable=False),
            sa.Column("amount", sa.Numeric(12, 2), nullable=True),
            sa.Column("related_id", sa.String(36), nullable=True),
            sa.Column("actor", sa.String(50), nullable=False, server_default="system"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        op.execute("DROP TABLE IF EXISTS ambassador_activity_logs")
        op.execute("DROP TYPE IF EXISTS activity_event_type_enum")
        op.execute("ALTER TABLE ambassadors DROP COLUMN IF EXISTS admin_notes")
        op.execute("ALTER TABLE ambassadors DROP COLUMN IF EXISTS status")
        op.execute("DROP TYPE IF EXISTS ambassador_status_enum")
    else:
        op.drop_table("ambassador_activity_logs")
        with op.batch_alter_table("ambassadors") as batch_op:
            batch_op.drop_column("admin_notes")
            batch_op.drop_column("status")
