"""Ambassador program full implementation

Revision ID: e9f8a7b6c5d4
Revises: a3b4c5d6e7f8
Create Date: 2026-03-14 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "e9f8a7b6c5d4"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # ── 1. Rename / update ambassador_rank_enum values ──────────────
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE ambassador_rank_enum RENAME VALUE 'bronze' TO 'scout'")
        op.execute("ALTER TYPE ambassador_rank_enum RENAME VALUE 'silver' TO 'guide'")
        op.execute(
            "ALTER TYPE ambassador_rank_enum RENAME VALUE 'gold' TO 'strategist'"
        )
        op.execute(
            "ALTER TYPE ambassador_rank_enum RENAME VALUE 'platinum' TO 'master'"
        )
        # diamond cannot be renamed to an existing value — drop is unsupported in older PG
        # rows with 'diamond' are migrated to 'scout' via UPDATE after the type changes
        op.execute("UPDATE ambassadors SET rank = 'scout' WHERE rank = 'diamond'")
    else:
        # SQLite stores enums as VARCHAR — update existing rows directly
        op.execute("UPDATE ambassadors SET rank = 'scout' WHERE rank = 'bronze'")
        op.execute("UPDATE ambassadors SET rank = 'guide' WHERE rank = 'silver'")
        op.execute("UPDATE ambassadors SET rank = 'strategist' WHERE rank = 'gold'")
        op.execute("UPDATE ambassadors SET rank = 'master' WHERE rank = 'platinum'")
        op.execute("UPDATE ambassadors SET rank = 'scout' WHERE rank = 'diamond'")

    # ── 2. Add new columns to ambassadors ───────────────────────────
    op.add_column(
        "ambassadors",
        sa.Column("payout_address", sa.String(200), nullable=True),
    )
    op.add_column(
        "ambassadors",
        sa.Column("territory_id", sa.CHAR(36), nullable=True),
    )

    # ── 3. Create ambassador_territories ────────────────────────────
    op.create_table(
        "ambassador_territories",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("territory_type", sa.String(20), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("master_ambassador_id", sa.CHAR(36), nullable=True, unique=True),
        sa.Column(
            "revenue_share_pct", sa.Numeric(5, 2), nullable=False, server_default="5.0"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["master_ambassador_id"],
            ["ambassadors.id"],
            ondelete="SET NULL",
        ),
    )

    # ── 4. Create ambassador_commissions ────────────────────────────
    op.create_table(
        "ambassador_commissions",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("ambassador_id", sa.CHAR(36), nullable=False),
        sa.Column("month", sa.Integer, nullable=False),
        sa.Column("year", sa.Integer, nullable=False),
        sa.Column("active_referral_count", sa.Integer, server_default="0"),
        sa.Column("commission_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("commission_amount", sa.Numeric(10, 2), server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["ambassador_id"], ["ambassadors.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "ambassador_id", "month", "year", name="uq_commission_ambassador_month_year"
        ),
    )

    # ── 5. Create ambassador_bonuses ─────────────────────────────────
    op.create_table(
        "ambassador_bonuses",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("ambassador_id", sa.CHAR(36), nullable=False),
        sa.Column("bonus_type", sa.String(30), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("period", sa.String(7), nullable=True),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["ambassador_id"], ["ambassadors.id"], ondelete="CASCADE"
        ),
    )

    # ── 6. Create ambassador_payouts ─────────────────────────────────
    op.create_table(
        "ambassador_payouts",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("ambassador_id", sa.CHAR(36), nullable=False),
        sa.Column("total_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("commission_ids", sa.JSON, nullable=True),
        sa.Column("bonus_ids", sa.JSON, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("payout_address", sa.String(200), nullable=True),
        sa.Column("admin_notes", sa.String(1000), nullable=True),
        sa.Column("processed_by", sa.CHAR(36), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["ambassador_id"], ["ambassadors.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["processed_by"], ["users.id"], ondelete="SET NULL"),
    )

    # ── 7. Create ambassador_training_completions ────────────────────
    op.create_table(
        "ambassador_training_completions",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("ambassador_id", sa.CHAR(36), nullable=False),
        sa.Column("module_name", sa.String(100), nullable=False),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("completed_by_admin", sa.CHAR(36), nullable=True),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["ambassador_id"], ["ambassadors.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["completed_by_admin"], ["users.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "ambassador_id", "module_name", name="uq_training_ambassador_module"
        ),
    )

    # ── 8. Create territory_commissions ──────────────────────────────
    op.create_table(
        "territory_commissions",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("territory_id", sa.CHAR(36), nullable=False),
        sa.Column("master_ambassador_id", sa.CHAR(36), nullable=False),
        sa.Column("month", sa.Integer, nullable=False),
        sa.Column("year", sa.Integer, nullable=False),
        sa.Column("territory_volume_usd", sa.Numeric(10, 2), nullable=True),
        sa.Column("ambassador_count", sa.Integer, nullable=True),
        sa.Column("revenue_share_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("commission_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["territory_id"], ["ambassador_territories.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["master_ambassador_id"], ["ambassadors.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "territory_id", "month", "year", name="uq_territory_commission_month_year"
        ),
    )

    # ── 9. Add FK from ambassadors.territory_id → ambassador_territories.id ──
    if bind.dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_ambassadors_territory_id",
            "ambassadors",
            "ambassador_territories",
            ["territory_id"],
            ["id"],
            ondelete="SET NULL",
        )
    # SQLite does not support ADD CONSTRAINT after table creation; FK is
    # enforced at application level via SQLAlchemy for SQLite.


def downgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        op.drop_constraint(
            "fk_ambassadors_territory_id", "ambassadors", type_="foreignkey"
        )

    op.drop_table("territory_commissions")
    op.drop_table("ambassador_training_completions")
    op.drop_table("ambassador_payouts")
    op.drop_table("ambassador_bonuses")
    op.drop_table("ambassador_commissions")
    op.drop_table("ambassador_territories")

    op.drop_column("ambassadors", "territory_id")
    op.drop_column("ambassadors", "payout_address")

    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE ambassador_rank_enum RENAME VALUE 'scout' TO 'bronze'")
        op.execute("ALTER TYPE ambassador_rank_enum RENAME VALUE 'guide' TO 'silver'")
        op.execute(
            "ALTER TYPE ambassador_rank_enum RENAME VALUE 'strategist' TO 'gold'"
        )
        op.execute(
            "ALTER TYPE ambassador_rank_enum RENAME VALUE 'master' TO 'platinum'"
        )
    else:
        op.execute("UPDATE ambassadors SET rank = 'bronze' WHERE rank = 'scout'")
        op.execute("UPDATE ambassadors SET rank = 'silver' WHERE rank = 'guide'")
        op.execute("UPDATE ambassadors SET rank = 'gold' WHERE rank = 'strategist'")
        op.execute("UPDATE ambassadors SET rank = 'platinum' WHERE rank = 'master'")
