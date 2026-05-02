"""Ambassador programme V2: 10-rank system, multi-level commissions, pool, travel

Revision ID: b9c8d7e6f5a4
Revises: a9b8c7d6e5f4
Create Date: 2026-05-01

Changes:
- Replace ambassador_rank_enum (5 old values) with 10 V2 rank values
- Add V2 qualification columns to ambassadors: par_count, tav_count, rank_achieved_at, fast_start_claimed
- Add V2 columns to ambassador_commissions: tav_count, level_breakdown, generational_override
- Extend ambassador_bonuses.period from String(7) to String(20)
- Create leadership_revenue_pools table
- Create ambassador_travel_incentives table
"""

import sqlalchemy as sa
from alembic import op

revision = "b9c8d7e6f5a4"
down_revision = "a9b8c7d6e5f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # ── 1. Replace ambassador_rank_enum ──────────────────────────────────────
    # Old values (post-amb_program_full): scout, guide, strategist, master, diamond
    # New values: associate, bronze_leader, silver_leader, gold_leader, platinum_leader,
    #             diamond_leader, elite_diamond, black_diamond, crown_ambassador, grand_crown

    if bind.dialect.name == "postgresql":
        # Create new enum type
        op.execute(
            """
            CREATE TYPE ambassador_rank_enum_v2 AS ENUM (
                'associate', 'bronze_leader', 'silver_leader', 'gold_leader',
                'platinum_leader', 'diamond_leader', 'elite_diamond', 'black_diamond',
                'crown_ambassador', 'grand_crown'
            )
            """
        )
        # Migrate ambassadors.rank to new type
        op.execute(
            """
            ALTER TABLE ambassadors
              ALTER COLUMN rank TYPE ambassador_rank_enum_v2
              USING (
                CASE rank::text
                  WHEN 'guide'      THEN 'bronze_leader'
                  WHEN 'strategist' THEN 'silver_leader'
                  WHEN 'master'     THEN 'gold_leader'
                  ELSE 'associate'
                END
              )::ambassador_rank_enum_v2
            """
        )
        # Drop old type and rename new one
        op.execute("DROP TYPE ambassador_rank_enum")
        op.execute("ALTER TYPE ambassador_rank_enum_v2 RENAME TO ambassador_rank_enum")
    else:
        # SQLite: enum stored as VARCHAR — update rows directly
        op.execute("UPDATE ambassadors SET rank = 'bronze_leader' WHERE rank = 'guide'")
        op.execute(
            "UPDATE ambassadors SET rank = 'silver_leader' WHERE rank = 'strategist'"
        )
        op.execute("UPDATE ambassadors SET rank = 'gold_leader' WHERE rank = 'master'")
        op.execute(
            "UPDATE ambassadors SET rank = 'associate' WHERE rank NOT IN "
            "('bronze_leader','silver_leader','gold_leader','platinum_leader',"
            "'diamond_leader','elite_diamond','black_diamond','crown_ambassador','grand_crown')"
        )

    # ── 2. Add V2 qualification columns to ambassadors ────────────────────────
    op.add_column(
        "ambassadors",
        sa.Column("par_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ambassadors",
        sa.Column("tav_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ambassadors",
        sa.Column("rank_achieved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ambassadors",
        sa.Column(
            "fast_start_claimed", sa.Integer(), nullable=False, server_default="0"
        ),
    )

    # ── 3. Add V2 columns to ambassador_commissions ───────────────────────────
    op.add_column(
        "ambassador_commissions",
        sa.Column("tav_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ambassador_commissions",
        sa.Column("level_breakdown", sa.JSON(), nullable=True),
    )
    op.add_column(
        "ambassador_commissions",
        sa.Column(
            "generational_override",
            sa.Numeric(10, 2),
            nullable=False,
            server_default="0",
        ),
    )

    # ── 4. Extend ambassador_bonuses.period (7→20 chars) ─────────────────────
    # "fast_start_1" = 12 chars, period keys like "2026-05" = 7 chars
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TABLE ambassador_bonuses ALTER COLUMN period TYPE VARCHAR(20)"
        )
    else:
        # SQLite: ALTER COLUMN TYPE not supported — table recreate not needed
        # since SQLite ignores declared column length constraints; existing data is safe
        pass

    # ── 5. Create leadership_revenue_pools ────────────────────────────────────
    op.create_table(
        "leadership_revenue_pools",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column(
            "total_pool_amount",
            sa.Numeric(12, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "eligible_ambassador_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "per_ambassador_amount",
            sa.Numeric(10, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
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
        sa.UniqueConstraint("month", "year", name="uq_leadership_pool_month_year"),
    )

    # ── 6. Create ambassador_travel_incentives ────────────────────────────────
    op.create_table(
        "ambassador_travel_incentives",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("ambassador_id", sa.CHAR(36), nullable=False),
        sa.Column("destination", sa.String(200), nullable=False),
        sa.Column("rank_required", sa.String(30), nullable=False),
        sa.Column("qualification_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("qualification_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="qualifying",
        ),
        sa.Column("awarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("admin_notes", sa.String(500), nullable=True),
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
            "ambassador_id", "rank_required", name="uq_travel_ambassador_rank"
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()

    # Drop new tables
    op.drop_table("ambassador_travel_incentives")
    op.drop_table("leadership_revenue_pools")

    # Remove V2 columns from ambassador_commissions
    op.drop_column("ambassador_commissions", "generational_override")
    op.drop_column("ambassador_commissions", "level_breakdown")
    op.drop_column("ambassador_commissions", "tav_count")

    # Remove V2 columns from ambassadors
    op.drop_column("ambassadors", "fast_start_claimed")
    op.drop_column("ambassadors", "rank_achieved_at")
    op.drop_column("ambassadors", "tav_count")
    op.drop_column("ambassadors", "par_count")

    # Restore ambassador_rank_enum to V1 values (scout, guide, strategist, master, diamond)
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            CREATE TYPE ambassador_rank_enum_v1 AS ENUM (
                'scout', 'guide', 'strategist', 'master', 'diamond'
            )
            """
        )
        op.execute(
            """
            ALTER TABLE ambassadors
              ALTER COLUMN rank TYPE ambassador_rank_enum_v1
              USING (
                CASE rank::text
                  WHEN 'bronze_leader'  THEN 'guide'
                  WHEN 'silver_leader'  THEN 'strategist'
                  WHEN 'gold_leader'    THEN 'master'
                  ELSE 'scout'
                END
              )::ambassador_rank_enum_v1
            """
        )
        op.execute("DROP TYPE ambassador_rank_enum")
        op.execute("ALTER TYPE ambassador_rank_enum_v1 RENAME TO ambassador_rank_enum")
    else:
        op.execute("UPDATE ambassadors SET rank = 'guide' WHERE rank = 'bronze_leader'")
        op.execute(
            "UPDATE ambassadors SET rank = 'strategist' WHERE rank = 'silver_leader'"
        )
        op.execute("UPDATE ambassadors SET rank = 'master' WHERE rank = 'gold_leader'")
        op.execute(
            "UPDATE ambassadors SET rank = 'scout' WHERE rank NOT IN "
            "('scout','guide','strategist','master','diamond')"
        )
