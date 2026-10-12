"""0006_notifications_and_referential_integrity

Revision ID: c7d4e9f01a5b
Revises: a3f81c2d9b4e
Create Date: 2026-08-31 07:05:00.000001

Adds the notifications table (TSD §22/§32, SRS FR-15) and completes the
referential-integrity foundation required by TSD §34:

- user-ownership FKs: documents.user_id, purchases.user_id,
  claims.user_id, notifications.user_id (ON DELETE CASCADE, so account
  deletion per SRS FR-20 can cascade to owned data);
- cascade/SET NULL rules on the document/purchase/product chains so a
  user deletion cannot be blocked by child rows (extraction jobs,
  pages, derivatives, purchase items, aliases).
"""

import sqlalchemy as sa

from alembic import op

revision = 'c7d4e9f01a5b'
down_revision = 'a3f81c2d9b4e'
branch_labels = None
depends_on = None


def _recreate_fk(constraint: str, table: str, column: str, target: tuple[str, str], ondelete: str) -> None:
    op.drop_constraint(constraint, table, type_='foreignkey')
    op.create_foreign_key(
        constraint, table, target[0], [column], [target[1]], ondelete=ondelete
    )


def upgrade() -> None:
    # --- notifications (TSD §22/§32) ---------------------------------------
    op.create_table('notifications',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('category', sa.Enum('WARRANTY_EXPIRING', 'PROCESSING_COMPLETE', 'REVIEW_REQUIRED', 'CLAIM_READY', 'SYSTEM', name='notification_category'), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('body', sa.Text(), nullable=True),
    sa.Column('purchase_id', sa.Integer(), nullable=True),
    sa.Column('warranty_mapping_id', sa.Integer(), nullable=True),
    sa.Column('expiry_date_snapshot', sa.Date(), nullable=True),
    sa.Column('reminder_type', sa.String(length=16), nullable=True),
    sa.Column('notification_key', sa.String(length=255), nullable=False),
    sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.Enum('SCHEDULED', 'SENT', 'READ', 'CANCELLED', 'FAILED', name='notification_status'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['purchase_id'], ['purchases.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['warranty_mapping_id'], ['warranty_mappings.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('notification_key', name='uq_notifications_notification_key')
    )
    op.create_index(op.f('ix_notifications_category'), 'notifications', ['category'], unique=False)
    op.create_index('ix_notifications_user_sched', 'notifications', ['user_id', 'scheduled_at'], unique=False)
    op.create_index(op.f('ix_notifications_status'), 'notifications', ['status'], unique=False)
    op.create_index(op.f('ix_notifications_purchase_id'), 'notifications', ['purchase_id'], unique=False)
    op.create_index(op.f('ix_notifications_scheduled_at'), 'notifications', ['scheduled_at'], unique=False)
    op.create_index(op.f('ix_notifications_user_id'), 'notifications', ['user_id'], unique=False)

    # --- user-ownership FKs (TSD §34; SRS FR-20 cascade) --------------------
    op.create_foreign_key('fk_documents_user_id', 'documents', 'users', ['user_id'], ['id'], ondelete='CASCADE')
    op.create_foreign_key('fk_purchases_user_id', 'purchases', 'users', ['user_id'], ['id'], ondelete='CASCADE')
    op.create_foreign_key('fk_claims_user_id', 'claims', 'users', ['user_id'], ['id'], ondelete='CASCADE')

    # --- normalize existing FKs so user deletion cascades cleanly ----------
    _recreate_fk('extraction_jobs_document_id_fkey', 'extraction_jobs', 'document_id', ('documents', 'id'), 'CASCADE')
    _recreate_fk('extracted_fields_document_id_fkey', 'extracted_fields', 'document_id', ('documents', 'id'), 'CASCADE')
    _recreate_fk('extracted_fields_extraction_job_id_fkey', 'extracted_fields', 'extraction_job_id', ('extraction_jobs', 'id'), 'SET NULL')
    _recreate_fk('document_pages_document_id_fkey', 'document_pages', 'document_id', ('documents', 'id'), 'CASCADE')
    _recreate_fk('document_derivatives_document_id_fkey', 'document_derivatives', 'document_id', ('documents', 'id'), 'CASCADE')
    _recreate_fk('purchase_items_purchase_id_fkey', 'purchase_items', 'purchase_id', ('purchases', 'id'), 'CASCADE')
    _recreate_fk('purchase_items_product_id_fkey', 'purchase_items', 'product_id', ('products', 'id'), 'SET NULL')
    _recreate_fk('product_aliases_product_id_fkey', 'product_aliases', 'product_id', ('products', 'id'), 'CASCADE')
    _recreate_fk('product_aliases_retailer_id_fkey', 'product_aliases', 'retailer_id', ('retailers', 'id'), 'SET NULL')
    _recreate_fk('products_manufacturer_id_fkey', 'products', 'manufacturer_id', ('manufacturers', 'id'), 'SET NULL')
    _recreate_fk('purchases_document_id_fkey', 'purchases', 'document_id', ('documents', 'id'), 'SET NULL')
    _recreate_fk('purchases_retailer_id_fkey', 'purchases', 'retailer_id', ('retailers', 'id'), 'SET NULL')
    _recreate_fk('claims_purchase_id_fkey', 'claims', 'purchase_id', ('purchases', 'id'), 'SET NULL')


def downgrade() -> None:
    _recreate_fk('claims_purchase_id_fkey', 'claims', 'purchase_id', ('purchases', 'id'), 'NO ACTION')
    _recreate_fk('purchases_retailer_id_fkey', 'purchases', 'retailer_id', ('retailers', 'id'), 'NO ACTION')
    _recreate_fk('purchases_document_id_fkey', 'purchases', 'document_id', ('documents', 'id'), 'NO ACTION')
    _recreate_fk('products_manufacturer_id_fkey', 'products', 'manufacturer_id', ('manufacturers', 'id'), 'NO ACTION')
    _recreate_fk('product_aliases_retailer_id_fkey', 'product_aliases', 'retailer_id', ('retailers', 'id'), 'NO ACTION')
    _recreate_fk('product_aliases_product_id_fkey', 'product_aliases', 'product_id', ('products', 'id'), 'NO ACTION')
    _recreate_fk('purchase_items_product_id_fkey', 'purchase_items', 'product_id', ('products', 'id'), 'NO ACTION')
    _recreate_fk('purchase_items_purchase_id_fkey', 'purchase_items', 'purchase_id', ('purchases', 'id'), 'NO ACTION')
    _recreate_fk('document_derivatives_document_id_fkey', 'document_derivatives', 'document_id', ('documents', 'id'), 'NO ACTION')
    _recreate_fk('document_pages_document_id_fkey', 'document_pages', 'document_id', ('documents', 'id'), 'NO ACTION')
    _recreate_fk('extracted_fields_extraction_job_id_fkey', 'extracted_fields', 'extraction_job_id', ('extraction_jobs', 'id'), 'NO ACTION')
    _recreate_fk('extracted_fields_document_id_fkey', 'extracted_fields', 'document_id', ('documents', 'id'), 'NO ACTION')
    _recreate_fk('extraction_jobs_document_id_fkey', 'extraction_jobs', 'document_id', ('documents', 'id'), 'NO ACTION')
    op.drop_constraint('fk_claims_user_id', 'claims', type_='foreignkey')
    op.drop_constraint('fk_purchases_user_id', 'purchases', type_='foreignkey')
    op.drop_constraint('fk_documents_user_id', 'documents', type_='foreignkey')
    op.drop_index(op.f('ix_notifications_user_id'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_scheduled_at'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_purchase_id'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_status'), table_name='notifications')
    op.drop_index('ix_notifications_user_sched', table_name='notifications')
    op.drop_index(op.f('ix_notifications_category'), table_name='notifications')
    op.drop_table('notifications')
    sa.Enum(name='notification_status').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='notification_category').drop(op.get_bind(), checkfirst=True)
    op.execute("DROP TYPE IF EXISTS notification_category")
    op.execute("DROP TYPE IF EXISTS notification_status")
