"""0009_extended_warranties

- purchase_extended_warranties: user/extracted/external extended-warranty
  records attached to a purchase (or one of its items). The rules engine
  computes a timeline per record ONLY when data supports it — a missing
  duration or anchor yields an UNKNOWN timeline, never a guessed date.
  Multiple records per component are allowed (multiple simultaneous
  coverage periods).
- warranty_timelines.coverage_id becomes nullable and gains
  extended_warranty_id: timeline rows may originate from a policy
  coverage OR from an extended warranty record (never both).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# NOTE: plain sa.Enum drops create_type=False when adapted by op.create_table
# (see a3f81c2d9b4e), so existing PG enum types must be referenced via
# postgresql.ENUM (PGEnum) with create_type=False.
PGEnum = postgresql.ENUM

revision = "d5a3c8e21b74"
down_revision = "4c9e2b7a1f60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE extended_warranty_source AS ENUM ('USER_DECLARED', 'EXTRACTED', 'EXTERNAL_OFFER')"
    )
    op.create_table(
        "purchase_extended_warranties",
        sa.Column("uuid", sa.String(length=32), nullable=False),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "purchase_id",
            sa.Integer(),
            sa.ForeignKey("purchases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "purchase_item_id",
            sa.Integer(),
            sa.ForeignKey("purchase_items.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("component", sa.String(length=120), nullable=False),
        sa.Column("duration_value", sa.Integer(), nullable=True),
        # Reuse the existing enum types (created with warranty_coverages).
        sa.Column(
            "duration_unit",
            PGEnum("DAY", "WEEK", "MONTH", "YEAR", "UNLIMITED", name="warranty_duration_unit", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "start_condition",
            PGEnum(
                "PURCHASE_DATE",
                "REGISTRATION_DATE",
                "MANUFACTURE_DATE",
                "INSTALLATION_DATE",
                "ACTIVATION_DATE",
                name="warranty_start_condition",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "source_type",
            PGEnum("USER_DECLARED", "EXTRACTED", "EXTERNAL_OFFER", name="extended_warranty_source", create_type=False),
            nullable=False,
        ),
        sa.Column("source_detail", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("uuid", name="uq_purchase_extended_warranties_uuid"),
        sa.CheckConstraint(
            "(duration_unit = 'UNLIMITED') OR (duration_value > 0)",
            name="ck_extended_warranties_duration",
        ),
    )
    op.create_index(
        "ix_purchase_extended_warranties_purchase",
        "purchase_extended_warranties",
        ["purchase_id"],
    )
    op.create_index(
        "ix_purchase_extended_warranties_item",
        "purchase_extended_warranties",
        ["purchase_item_id"],
    )

    # Timeline rows may originate from an extended warranty instead of a
    # policy coverage.
    op.alter_column("warranty_timelines", "coverage_id", existing_type=sa.Integer(), nullable=True)
    op.add_column(
        "warranty_timelines",
        sa.Column(
            "extended_warranty_id",
            sa.Integer(),
            sa.ForeignKey("purchase_extended_warranties.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_warranty_timelines_extended",
        "warranty_timelines",
        ["extended_warranty_id"],
    )
    op.create_unique_constraint(
        "uq_timeline_item_extended",
        "warranty_timelines",
        ["purchase_item_id", "extended_warranty_id"],
    )
    # A timeline originates from exactly one of a policy coverage or an
    # extended warranty record.
    op.create_check_constraint(
        "ck_timeline_origin",
        "warranty_timelines",
        "(coverage_id IS NOT NULL) <> (extended_warranty_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_timeline_origin", "warranty_timelines", type_="check")
    op.drop_constraint("uq_timeline_item_extended", "warranty_timelines", type_="unique")
    op.drop_index("ix_warranty_timelines_extended", table_name="warranty_timelines")
    op.drop_column("warranty_timelines", "extended_warranty_id")
    op.alter_column(
        "warranty_timelines", "coverage_id", existing_type=sa.Integer(), nullable=False
    )
    op.drop_index("ix_purchase_extended_warranties_item", table_name="purchase_extended_warranties")
    op.drop_index("ix_purchase_extended_warranties_purchase", table_name="purchase_extended_warranties")
    op.drop_table("purchase_extended_warranties")
    op.execute("DROP TYPE extended_warranty_source")
