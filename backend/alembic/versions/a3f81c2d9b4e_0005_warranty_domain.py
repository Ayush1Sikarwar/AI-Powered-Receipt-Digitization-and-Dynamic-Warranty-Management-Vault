"""0005_warranty_domain

Revision ID: a3f81c2d9b4e
Revises: f2ddeffd956f
Create Date: 2026-08-31 07:05:00.000000

Warranty domain (TSD §13-§15): warranty_sources, warranty_policies,
warranty_coverages, warranty_mappings, warranty_timelines.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PGEnum

from alembic import op

# NOTE: `verification_status` already exists (migration 0003) and
# `warranty_duration_unit` is created above in this same migration, so
# both must be referenced with create_type=False to avoid re-creating
# the PG enum type. (Plain sa.Enum drops create_type=False when adapted
# to the native PG ENUM.)
revision = 'a3f81c2d9b4e'
down_revision = 'f2ddeffd956f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('warranty_sources',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('manufacturer_id', sa.Integer(), nullable=True),
    sa.Column('source_type', sa.Enum('OFFICIAL_API', 'OFFICIAL_WEB', 'OFFICIAL_DOCUMENT', 'APPROVED_PARTNER', 'CURATED_DATASET', 'OTHER', name='warranty_source_type'), nullable=False),
    sa.Column('source_url', sa.String(length=2048), nullable=True),
    sa.Column('document_hash', sa.String(length=64), nullable=True),
    sa.Column('source_title', sa.String(length=500), nullable=False),
    sa.Column('source_version', sa.String(length=32), nullable=True),
    sa.Column('retrieved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('effective_date', sa.Date(), nullable=True),
    sa.Column('region', sa.String(length=8), nullable=True),
    sa.Column('trust_level', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('trust_level >= 1 AND trust_level <= 5', name='ck_warranty_sources_trust_level'),
    sa.ForeignKeyConstraint(['manufacturer_id'], ['manufacturers.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_warranty_sources_manufacturer_id'), 'warranty_sources', ['manufacturer_id'], unique=False)
    op.create_table('warranty_policies',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('manufacturer_id', sa.Integer(), nullable=True),
    sa.Column('product_category', sa.String(length=120), nullable=True),
    sa.Column('product_model_pattern', sa.String(length=200), nullable=True),
    sa.Column('region', sa.String(length=8), nullable=True),
    sa.Column('policy_name', sa.String(length=300), nullable=False),
    sa.Column('policy_version', sa.String(length=32), nullable=False),
    sa.Column('effective_from', sa.Date(), nullable=False),
    sa.Column('effective_to', sa.Date(), nullable=True),
    sa.Column('source_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.Enum('DRAFT', 'ACTIVE', 'ARCHIVED', name='warranty_policy_status'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['manufacturer_id'], ['manufacturers.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_id'], ['warranty_sources.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_warranty_policies_manufacturer_id'), 'warranty_policies', ['manufacturer_id'], unique=False)
    op.create_index(op.f('ix_warranty_policies_region'), 'warranty_policies', ['region'], unique=False)
    op.create_index(op.f('ix_warranty_policies_source_id'), 'warranty_policies', ['source_id'], unique=False)
    op.create_index(op.f('ix_warranty_policies_status'), 'warranty_policies', ['status'], unique=False)
    op.create_table('warranty_coverages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('policy_id', sa.Integer(), nullable=False),
    sa.Column('component', sa.String(length=120), nullable=False),
    sa.Column('duration_value', sa.Integer(), nullable=True),
    sa.Column('duration_unit', sa.Enum('DAY', 'WEEK', 'MONTH', 'YEAR', 'UNLIMITED', name='warranty_duration_unit'), nullable=False),
    sa.Column('start_condition', sa.Enum('PURCHASE_DATE', 'REGISTRATION_DATE', 'MANUFACTURE_DATE', 'INSTALLATION_DATE', 'ACTIVATION_DATE', name='warranty_start_condition'), nullable=False),
    sa.Column('registration_required', sa.Boolean(), nullable=False),
    sa.Column('coverage_description', sa.Text(), nullable=True),
    sa.Column('exclusions', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(duration_unit = 'UNLIMITED') OR (duration_value > 0)", name='ck_warranty_coverages_duration'),
    sa.ForeignKeyConstraint(['policy_id'], ['warranty_policies.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('policy_id', 'component', name='uq_coverage_policy_component')
    )
    op.create_index(op.f('ix_warranty_coverages_policy_id'), 'warranty_coverages', ['policy_id'], unique=False)
    op.create_table('warranty_mappings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('purchase_item_id', sa.Integer(), nullable=False),
    sa.Column('warranty_policy_id', sa.Integer(), nullable=False),
    sa.Column('match_method', sa.Enum('EXACT_MODEL', 'EXACT_SKU', 'CATALOG_MATCH', 'FUZZY_MATCH', 'SEMANTIC_MATCH', 'MANUAL', name='warranty_match_method'), nullable=False),
    sa.Column('match_confidence', sa.Float(), nullable=True),
    sa.Column('verification_status', PGEnum('AI_EXTRACTED', 'USER_CONFIRMED', 'USER_CORRECTED', 'REJECTED', name='verification_status', create_type=False), nullable=False),
    sa.Column('mapped_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('uuid', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['purchase_item_id'], ['purchase_items.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['warranty_policy_id'], ['warranty_policies.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('purchase_item_id', 'warranty_policy_id', name='uq_warranty_mapping')
    )
    op.create_index(op.f('ix_warranty_mappings_purchase_item_id'), 'warranty_mappings', ['purchase_item_id'], unique=False)
    op.create_index(op.f('ix_warranty_mappings_uuid'), 'warranty_mappings', ['uuid'], unique=True)
    op.create_index(op.f('ix_warranty_mappings_warranty_policy_id'), 'warranty_mappings', ['warranty_policy_id'], unique=False)
    op.create_table('warranty_timelines',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('purchase_item_id', sa.Integer(), nullable=False),
    sa.Column('coverage_id', sa.Integer(), nullable=False),
    sa.Column('component', sa.String(length=120), nullable=False),
    sa.Column('purchase_date', sa.Date(), nullable=True),
    sa.Column('start_date', sa.Date(), nullable=True),
    sa.Column('expiry_date', sa.Date(), nullable=True),
    sa.Column('duration_value', sa.Integer(), nullable=True),
    sa.Column('duration_unit', PGEnum('DAY', 'WEEK', 'MONTH', 'YEAR', 'UNLIMITED', name='warranty_duration_unit', create_type=False), nullable=True),
    sa.Column('status', sa.Enum('NOT_STARTED', 'ACTIVE', 'EXPIRING', 'EXPIRED', 'UNKNOWN', name='warranty_timeline_status'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['coverage_id'], ['warranty_coverages.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['purchase_item_id'], ['purchase_items.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('purchase_item_id', 'coverage_id', name='uq_timeline_item_coverage')
    )
    op.create_index('ix_warranty_timelines_expiry_status', 'warranty_timelines', ['expiry_date', 'status'], unique=False)
    op.create_index(op.f('ix_warranty_timelines_coverage_id'), 'warranty_timelines', ['coverage_id'], unique=False)
    op.create_index(op.f('ix_warranty_timelines_purchase_item_id'), 'warranty_timelines', ['purchase_item_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_warranty_timelines_purchase_item_id'), table_name='warranty_timelines')
    op.drop_index(op.f('ix_warranty_timelines_coverage_id'), table_name='warranty_timelines')
    op.drop_index('ix_warranty_timelines_expiry_status', table_name='warranty_timelines')
    op.drop_table('warranty_timelines')
    op.drop_index(op.f('ix_warranty_mappings_warranty_policy_id'), table_name='warranty_mappings')
    op.drop_index(op.f('ix_warranty_mappings_uuid'), table_name='warranty_mappings')
    op.drop_index(op.f('ix_warranty_mappings_purchase_item_id'), table_name='warranty_mappings')
    op.drop_table('warranty_mappings')
    op.drop_index(op.f('ix_warranty_coverages_policy_id'), table_name='warranty_coverages')
    op.drop_table('warranty_coverages')
    op.drop_index(op.f('ix_warranty_policies_status'), table_name='warranty_policies')
    op.drop_index(op.f('ix_warranty_policies_source_id'), table_name='warranty_policies')
    op.drop_index(op.f('ix_warranty_policies_region'), table_name='warranty_policies')
    op.drop_index(op.f('ix_warranty_policies_manufacturer_id'), table_name='warranty_policies')
    op.drop_table('warranty_policies')
    op.drop_index(op.f('ix_warranty_sources_manufacturer_id'), table_name='warranty_sources')
    op.drop_table('warranty_sources')
    # warranty_duration_unit is shared with warranty_coverages (already
    # dropped above); verification_status is shared with extracted_fields
    # (migration 0003) and must be kept.
    sa.Enum(name='warranty_duration_unit').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='warranty_timeline_status').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='warranty_match_method').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='warranty_start_condition').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='warranty_policy_status').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='warranty_source_type').drop(op.get_bind(), checkfirst=True)
    op.execute("DROP TYPE IF EXISTS warranty_duration_unit")
    op.execute("DROP TYPE IF EXISTS warranty_match_method")
    op.execute("DROP TYPE IF EXISTS warranty_policy_status")
    op.execute("DROP TYPE IF EXISTS warranty_source_type")
    op.execute("DROP TYPE IF EXISTS warranty_start_condition")
    op.execute("DROP TYPE IF EXISTS warranty_timeline_status")
