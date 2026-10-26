"""0008_source_status_and_item_date_anchors

- warranty_sources.retrieval_status (SUCCESS/FAILED) + last_error: a failed
  source refresh must be visible and must never silently drop the previous
  valid policy (SRS FR-12 / task: source failure safety).
- purchase_items.installation_date / registration_date: user-supplied anchor
  dates so INSTALLATION_DATE-anchored and registration-dependent coverage
  can be computed honestly (UNKNOWN until the user records the date).
"""

import sqlalchemy as sa

from alembic import op

revision = "4c9e2b7a1f60"
down_revision = "9b41c7d20a35"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TYPE retrieval_status AS ENUM ('SUCCESS', 'FAILED')")
    op.add_column(
        "warranty_sources",
        sa.Column(
            "retrieval_status",
            sa.Enum("SUCCESS", "FAILED", name="retrieval_status"),
            server_default="SUCCESS",
            nullable=False,
        ),
    )
    op.add_column("warranty_sources", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column("purchase_items", sa.Column("installation_date", sa.Date(), nullable=True))
    op.add_column("purchase_items", sa.Column("registration_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("purchase_items", "registration_date")
    op.drop_column("purchase_items", "installation_date")
    op.drop_column("warranty_sources", "last_error")
    op.drop_column("warranty_sources", "retrieval_status")
    op.execute("DROP TYPE IF EXISTS retrieval_status")
