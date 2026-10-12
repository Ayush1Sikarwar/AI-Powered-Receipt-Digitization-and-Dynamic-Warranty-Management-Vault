"""0007_notification_claim_ref

Adds notifications.claim_id so CLAIM_READY notifications can reference the
claim they were created for (SRS FR-16). SET NULL: a deleted claim keeps
the (user-owned) notification row.
"""

import sqlalchemy as sa

from alembic import op

revision = "9b41c7d20a35"
down_revision = "372a08d2314f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("claim_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_notifications_claim_id",
        "notifications",
        "claims",
        ["claim_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_notifications_claim_id"), "notifications", ["claim_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_notifications_claim_id"), table_name="notifications")
    op.drop_constraint("fk_notifications_claim_id", "notifications", type_="foreignkey")
    op.drop_column("notifications", "claim_id")
