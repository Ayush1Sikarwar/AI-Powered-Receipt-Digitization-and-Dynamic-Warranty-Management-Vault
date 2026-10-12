"""claim_files_and_job_scheduling

Revision ID: 092fe14af9eb
Revises: c7d4e9f01a5b
Create Date: 2026-08-31
"""

import sqlalchemy as sa

from alembic import op

revision = '092fe14af9eb'
down_revision = 'c7d4e9f01a5b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Evidence files attached to claims (SRS FR-16/FR-19 supporting evidence).
    op.create_table(
        "claim_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", sa.String(length=36), nullable=False, unique=True),
        sa.Column("claim_id", sa.Integer(), sa.ForeignKey("claims.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("file_size >= 0", name="ck_claim_files_file_size"),
    )
    op.create_index("ix_claim_files_claim", "claim_files", ["claim_id", "created_at"])

    # Deferred retention/cleanup jobs (SRS NFR-02 purge scheduling).
    op.add_column(
        "background_jobs",
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_background_jobs_scheduled", "background_jobs", ["scheduled_at"])


def downgrade() -> None:
    op.drop_index("ix_background_jobs_scheduled", table_name="background_jobs")
    op.drop_column("background_jobs", "scheduled_at")
    op.drop_index("ix_claim_files_claim", table_name="claim_files")
    op.drop_table("claim_files")
