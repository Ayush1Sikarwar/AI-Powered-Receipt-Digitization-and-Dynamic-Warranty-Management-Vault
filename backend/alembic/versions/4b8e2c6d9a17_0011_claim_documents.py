"""0011 claim documents: user-selected stored documents attached to a claim.

A claim's evidence is not only newly uploaded files — the user can select
already-stored documents (the receipt, a warranty card, a photo) as claim
evidence with a declared role (RECEIPT / WARRANTY / EVIDENCE). The role is
user-declared provenance: the attachment is USER_CONFIRMED even when the
document's own fields are AI-extracted.

Revision ID: 4b8e2c6d9a17
Revises: c9d1e5f8a2b3
Create Date: 2026-09-04
"""

import sqlalchemy as sa

from alembic import op

revision = "4b8e2c6d9a17"
down_revision = "c9d1e5f8a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "claim_documents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "uuid",
            sa.String(length=32),
            nullable=False,
            unique=True,
            server_default=sa.text("lower(md5(random()::text || clock_timestamp()::text))"),
        ),
        sa.Column("claim_id", sa.Integer(), sa.ForeignKey("claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "role",
            sa.Enum("RECEIPT", "WARRANTY", "EVIDENCE", name="claim_document_role"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("claim_id", "document_id", name="uq_claim_document"),
    )
    op.create_index(op.f("ix_claim_documents_claim_id"), "claim_documents", ["claim_id"], unique=False)
    op.create_index(op.f("ix_claim_documents_document_id"), "claim_documents", ["document_id"], unique=False)
    op.create_index(op.f("ix_claim_documents_user_id"), "claim_documents", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_claim_documents_user_id"), table_name="claim_documents")
    op.drop_index(op.f("ix_claim_documents_document_id"), table_name="claim_documents")
    op.drop_index(op.f("ix_claim_documents_claim_id"), table_name="claim_documents")
    op.drop_table("claim_documents")
    op.execute("DROP TYPE IF EXISTS claim_document_role")
