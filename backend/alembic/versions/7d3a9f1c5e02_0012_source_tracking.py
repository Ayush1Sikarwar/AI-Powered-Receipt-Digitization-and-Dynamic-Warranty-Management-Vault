"""0012 warranty source tracking (TSD §44 Data Source Management).

Adds the operational tracking fields required for every source:
  - owner             who owns/maintains the source
  - retrieval_method  how it is retrieved (MANUAL_CURATED / WEB_FETCH / ...)
  - last_success_at   last successful retrieval
  - last_failure_at   last failed retrieval attempt

(retrieved_at already existed as "last retrieval"; document_hash is the
content hash; source_version is the version; retrieval_status is the
status.)

Revision ID: 7d3a9f1c5e02
Revises: 4b8e2c6d9a17
Create Date: 2026-09-04
"""

import sqlalchemy as sa

from alembic import op

revision = "7d3a9f1c5e02"
down_revision = "4b8e2c6d9a17"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("warranty_sources", sa.Column("owner", sa.String(200), nullable=True))
    op.add_column("warranty_sources", sa.Column("retrieval_method", sa.String(40), nullable=True))
    op.add_column("warranty_sources", sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("warranty_sources", sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("warranty_sources", "last_failure_at")
    op.drop_column("warranty_sources", "last_success_at")
    op.drop_column("warranty_sources", "retrieval_method")
    op.drop_column("warranty_sources", "owner")
