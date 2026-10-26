"""product_intelligence

Adds the retailer catalog entries table and the per-document product match
record (SRS ML-04/ML-06/ML-07, TSD §12/§28).

Revision ID: 372a08d2314f
Revises: ae4f04771389
Create Date: 2026-08-31
"""

import sqlalchemy as sa
from sqlalchemy import func

from alembic import op

revision = "372a08d2314f"
down_revision = "ae4f04771389"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "retailer_catalog_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("retailer_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("catalog_sku", sa.String(length=120), nullable=True),
        sa.Column("catalog_model", sa.String(length=120), nullable=True),
        sa.Column("catalog_name", sa.String(length=300), nullable=True),
        sa.Column("source", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=func.now(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["retailer_id"], ["retailers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("retailer_id", "catalog_sku", name="uq_retailer_catalog_sku"),
    )
    op.create_index(op.f("ix_retailer_catalog_entries_product_id"), "retailer_catalog_entries", ["product_id"], unique=False)
    op.create_index(op.f("ix_retailer_catalog_entries_retailer_id"), "retailer_catalog_entries", ["retailer_id"], unique=False)

    op.create_table(
        "product_matches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column(
            "match_method",
            sa.Enum(
                "EXACT_SKU",
                "EXACT_MODEL",
                "MANUFACTURER_MODEL",
                "CATALOG_MATCH",
                "ALIAS_MATCH",
                "FUZZY_MATCH",
                "SEMANTIC_MATCH",
                "MANUAL",
                "NONE",
                name="product_match_method",
            ),
            nullable=False,
        ),
        sa.Column("match_confidence", sa.Float(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "AUTO_MATCHED",
                "REVIEW_REQUIRED",
                "CONFIRMED",
                "REJECTED",
                "NO_MATCH",
                name="product_match_status",
            ),
            nullable=False,
        ),
        sa.Column("product_name_raw", sa.String(length=300), nullable=True),
        sa.Column("sku_raw", sa.String(length=120), nullable=True),
        sa.Column("model_raw", sa.String(length=120), nullable=True),
        sa.Column("manufacturer_raw", sa.String(length=200), nullable=True),
        sa.Column("retailer_raw", sa.String(length=200), nullable=True),
        sa.Column("sku_normalized", sa.String(length=120), nullable=True),
        sa.Column("model_normalized", sa.String(length=120), nullable=True),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("confirmed_by", sa.Integer(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=func.now(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_product_matches_document_id"), "product_matches", ["document_id"], unique=True)
    op.create_index(op.f("ix_product_matches_product_id"), "product_matches", ["product_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_product_matches_product_id"), table_name="product_matches")
    op.drop_index(op.f("ix_product_matches_document_id"), table_name="product_matches")
    op.drop_table("product_matches")
    op.execute("DROP TYPE IF EXISTS product_match_status")
    op.execute("DROP TYPE IF EXISTS product_match_method")
    op.drop_index(op.f("ix_retailer_catalog_entries_retailer_id"), table_name="retailer_catalog_entries")
    op.drop_index(op.f("ix_retailer_catalog_entries_product_id"), table_name="retailer_catalog_entries")
    op.drop_table("retailer_catalog_entries")
