"""page_ocr_engine_version

Revision ID: ae4f04771389
Revises: 092fe14af9eb
Create Date: 2026-08-31
"""

import sqlalchemy as sa

from alembic import op

revision = 'ae4f04771389'
down_revision = '092fe14af9eb'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_pages",
        sa.Column("ocr_engine_version", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_pages", "ocr_engine_version")
