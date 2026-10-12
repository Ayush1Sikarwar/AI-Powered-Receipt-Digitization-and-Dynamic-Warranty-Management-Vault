"""Warranty domain models (TSD §13–§15, SDD §5).

WarrantySource   — provenance of a warranty policy (URL, document hash,
                   trust level). The UI must surface this (SRS FR-12).
WarrantyPolicy   — manufacturer warranty terms for a product category /
                   model pattern in a region, with source provenance.
WarrantyCoverage — one coverage component of a policy (e.g. COMPRESSOR,
                   5 YEARS from PURCHASE_DATE; TSD §13.2).
WarrantyMapping  — links a PurchaseItem to a WarrantyPolicy with match
                   method + confidence (TSD §14).
WarrantyTimeline — computed start/expiry per purchase item + coverage
                   (TSD §15). The notification scheduler scans this table
                   by expiry_date for 60/30/7-day reminders (SRS FR-15).
"""

import enum
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UuidMixin
from app.models.extraction import VerificationStatus


class SourceType(enum.StrEnum):
    OFFICIAL_API = "OFFICIAL_API"
    OFFICIAL_WEB = "OFFICIAL_WEB"
    OFFICIAL_DOCUMENT = "OFFICIAL_DOCUMENT"
    APPROVED_PARTNER = "APPROVED_PARTNER"
    CURATED_DATASET = "CURATED_DATASET"
    OTHER = "OTHER"


class PolicyStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class RetrievalStatus(enum.StrEnum):
    """Result of the last source retrieval attempt (SRS FR-12 provenance).

    A FAILED refresh records the error and the failure time but never
    removes or deactivates previously valid policies — they remain
    retrievable and mapped; only a successful refresh with structured
    terms may retire a version.
    """

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class DurationUnit(enum.StrEnum):
    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"
    YEAR = "YEAR"
    UNLIMITED = "UNLIMITED"


class StartCondition(enum.StrEnum):
    PURCHASE_DATE = "PURCHASE_DATE"
    REGISTRATION_DATE = "REGISTRATION_DATE"
    MANUFACTURE_DATE = "MANUFACTURE_DATE"
    INSTALLATION_DATE = "INSTALLATION_DATE"
    ACTIVATION_DATE = "ACTIVATION_DATE"


class MatchMethod(enum.StrEnum):
    EXACT_MODEL = "EXACT_MODEL"
    EXACT_SKU = "EXACT_SKU"
    CATALOG_MATCH = "CATALOG_MATCH"
    FUZZY_MATCH = "FUZZY_MATCH"
    SEMANTIC_MATCH = "SEMANTIC_MATCH"
    MANUAL = "MANUAL"


class ExtendedWarrantySource(enum.StrEnum):
    """Where the extended-warranty data came from (provenance).

    The rules engine only extends a coverage window when a record exists —
    "where the data supports it". Each source type maps to a provenance
    label in the UI: USER_DECLARED → User Confirmed, EXTRACTED → AI
    Extracted, EXTERNAL_OFFER → External Source.
    """

    USER_DECLARED = "USER_DECLARED"
    EXTRACTED = "EXTRACTED"
    EXTERNAL_OFFER = "EXTERNAL_OFFER"


class TimelineStatus(enum.StrEnum):
    NOT_STARTED = "NOT_STARTED"
    ACTIVE = "ACTIVE"
    EXPIRING = "EXPIRING"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


class WarrantySource(Base, TimestampMixin):
    """Where a warranty policy came from (provenance, SRS FR-12)."""

    __tablename__ = "warranty_sources"
    __table_args__ = (
        CheckConstraint("trust_level >= 1 AND trust_level <= 5", name="ck_warranty_sources_trust_level"),
    )

    manufacturer_id: Mapped[int | None] = mapped_column(
        ForeignKey("manufacturers.id", ondelete="SET NULL"), index=True, nullable=True
    )
    source_type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, name="warranty_source_type"), default=SourceType.OTHER, nullable=False
    )
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    document_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)  # sha256 of retrieved document
    source_title: Mapped[str] = mapped_column(String(500), nullable=False)
    source_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retrieval_status: Mapped[RetrievalStatus] = mapped_column(
        Enum(RetrievalStatus, name="retrieval_status"), default=RetrievalStatus.SUCCESS, nullable=False
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    region: Mapped[str | None] = mapped_column(String(8), nullable=True)  # ISO alpha-2
    trust_level: Mapped[int] = mapped_column(Integer, default=3, nullable=False)  # 1=low … 5=official API
    # Operational tracking (TSD §44 Data Source Management).
    owner: Mapped[str | None] = mapped_column(String(200), nullable=True)  # who owns/maintains the source
    retrieval_method: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )  # MANUAL_CURATED / WEB_FETCH / API / PDF_UPLOAD
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WarrantyPolicy(Base, TimestampMixin):
    __tablename__ = "warranty_policies"

    manufacturer_id: Mapped[int | None] = mapped_column(
        ForeignKey("manufacturers.id", ondelete="SET NULL"), index=True, nullable=True
    )
    product_category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    product_model_pattern: Mapped[str | None] = mapped_column(String(200), nullable=True)  # e.g. "QT55*"
    region: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)  # ISO alpha-2, nullable = worldwide
    policy_name: Mapped[str] = mapped_column(String(300), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), default="1.0", nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("warranty_sources.id", ondelete="SET NULL"), index=True, nullable=True
    )
    status: Mapped[PolicyStatus] = mapped_column(
        Enum(PolicyStatus, name="warranty_policy_status"), default=PolicyStatus.ACTIVE, nullable=False, index=True
    )

    coverages: Mapped[list["WarrantyCoverage"]] = relationship(back_populates="policy", cascade="all, delete-orphan")


class WarrantyCoverage(Base, TimestampMixin):
    __tablename__ = "warranty_coverages"
    __table_args__ = (
        UniqueConstraint("policy_id", "component", name="uq_coverage_policy_component"),
        CheckConstraint("(duration_unit = 'UNLIMITED') OR (duration_value > 0)", name="ck_warranty_coverages_duration"),
    )

    policy_id: Mapped[int] = mapped_column(
        ForeignKey("warranty_policies.id", ondelete="CASCADE"), index=True, nullable=False
    )
    component: Mapped[str] = mapped_column(String(120), nullable=False)  # e.g. COMPRESSOR, FULL_UNIT, PARTS
    duration_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_unit: Mapped[DurationUnit] = mapped_column(
        Enum(DurationUnit, name="warranty_duration_unit"), default=DurationUnit.YEAR, nullable=False
    )
    start_condition: Mapped[StartCondition] = mapped_column(
        Enum(StartCondition, name="warranty_start_condition"), default=StartCondition.PURCHASE_DATE, nullable=False
    )
    registration_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    coverage_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    exclusions: Mapped[str | None] = mapped_column(Text, nullable=True)

    policy: Mapped[WarrantyPolicy] = relationship(back_populates="coverages")
    timelines: Mapped[list["WarrantyTimeline"]] = relationship(back_populates="coverage", cascade="all, delete-orphan")


class WarrantyMapping(Base, UuidMixin, TimestampMixin):
    """A purchase item's warranty, resolved to a policy (TSD §14)."""

    __tablename__ = "warranty_mappings"
    __table_args__ = (
        UniqueConstraint("purchase_item_id", "warranty_policy_id", name="uq_warranty_mapping"),
    )

    purchase_item_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_items.id", ondelete="CASCADE"), index=True, nullable=False
    )
    warranty_policy_id: Mapped[int] = mapped_column(
        ForeignKey("warranty_policies.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    match_method: Mapped[MatchMethod] = mapped_column(
        Enum(MatchMethod, name="warranty_match_method"), default=MatchMethod.CATALOG_MATCH, nullable=False
    )
    match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    verification_status: Mapped[VerificationStatus] = mapped_column(
        Enum(VerificationStatus, name="verification_status"),
        default=VerificationStatus.AI_EXTRACTED,
        nullable=False,
    )
    mapped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    policy: Mapped[WarrantyPolicy] = relationship()


class PurchaseExtendedWarranty(Base, UuidMixin, TimestampMixin):
    """An extended-warranty record for a purchase (or one of its items).

    The rules engine computes a timeline per record ONLY when data supports
    it. A missing duration or a missing start anchor produces an UNKNOWN
    timeline — never a guessed date. Multiple records per component are
    allowed, so several coverage periods can be simultaneously in force
    (e.g. the base compressor cover plus a separately purchased extension).
    """

    __tablename__ = "purchase_extended_warranties"
    __table_args__ = (
        CheckConstraint(
            "(duration_unit = 'UNLIMITED') OR (duration_value > 0)",
            name="ck_extended_warranties_duration",
        ),
    )

    purchase_id: Mapped[int] = mapped_column(
        ForeignKey("purchases.id", ondelete="CASCADE"), index=True, nullable=False
    )
    purchase_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchase_items.id", ondelete="CASCADE"), index=True, nullable=True
    )
    component: Mapped[str] = mapped_column(String(120), nullable=False)  # e.g. COMPRESSOR, FULL_UNIT
    duration_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_unit: Mapped[DurationUnit] = mapped_column(
        Enum(DurationUnit, name="warranty_duration_unit"), default=DurationUnit.YEAR, nullable=False
    )
    start_condition: Mapped[StartCondition] = mapped_column(
        Enum(StartCondition, name="warranty_start_condition"),
        default=StartCondition.PURCHASE_DATE,
        nullable=False,
    )
    source_type: Mapped[ExtendedWarrantySource] = mapped_column(
        Enum(ExtendedWarrantySource, name="extended_warranty_source"),
        default=ExtendedWarrantySource.USER_DECLARED,
        nullable=False,
    )
    source_detail: Mapped[str | None] = mapped_column(Text, nullable=True)  # URL / document ref / note
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class WarrantyTimeline(Base, TimestampMixin):
    """Computed coverage window for one purchase item + coverage (TSD §15).

    A timeline originates from exactly one of:
    - a policy ``coverage`` (warranty_coverages), or
    - an ``extended_warranty`` record (purchase_extended_warranties).
    Both foreign keys are present but only one is set per row (DB CHECK).
    """

    __tablename__ = "warranty_timelines"
    __table_args__ = (
        UniqueConstraint("purchase_item_id", "coverage_id", name="uq_timeline_item_coverage"),
        UniqueConstraint("purchase_item_id", "extended_warranty_id", name="uq_timeline_item_extended"),
        CheckConstraint(
            "(coverage_id IS NOT NULL) <> (extended_warranty_id IS NOT NULL)",
            name="ck_timeline_origin",
        ),
        Index("ix_warranty_timelines_expiry_status", "expiry_date", "status"),
    )

    purchase_item_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_items.id", ondelete="CASCADE"), index=True, nullable=False
    )
    coverage_id: Mapped[int | None] = mapped_column(
        ForeignKey("warranty_coverages.id", ondelete="CASCADE"), index=True, nullable=True
    )
    extended_warranty_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchase_extended_warranties.id", ondelete="CASCADE"), index=True, nullable=True
    )
    component: Mapped[str] = mapped_column(String(120), nullable=False)
    purchase_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    duration_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_unit: Mapped[DurationUnit | None] = mapped_column(
        Enum(DurationUnit, name="warranty_duration_unit"), nullable=True
    )
    status: Mapped[TimelineStatus] = mapped_column(
        Enum(TimelineStatus, name="warranty_timeline_status"), default=TimelineStatus.UNKNOWN, nullable=False
    )

    coverage: Mapped[WarrantyCoverage | None] = relationship(back_populates="timelines")
    extended_warranty: Mapped["PurchaseExtendedWarranty | None"] = relationship()
