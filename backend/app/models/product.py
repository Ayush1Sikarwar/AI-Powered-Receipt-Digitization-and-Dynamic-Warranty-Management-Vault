"""Manufacturer / Retailer / Product models (TSD §10-12)."""

import enum
from datetime import datetime

from sqlalchemy import JSON, Enum, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UuidMixin


class EntityStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    PENDING = "PENDING"


class Manufacturer(Base, UuidMixin, TimestampMixin):
    __tablename__ = "manufacturers"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Normalized key (lowercase, deduped) so different spellings do not create
    # duplicate manufacturers (TSD §11).
    name_key: Mapped[str] = mapped_column(String(200), unique=True, index=True, nullable=False)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country: Mapped[str | None] = mapped_column(String(8), nullable=True)
    status: Mapped[EntityStatus] = mapped_column(Enum(EntityStatus, name="entity_status"), default=EntityStatus.ACTIVE, nullable=False)


class Retailer(Base, UuidMixin, TimestampMixin):
    __tablename__ = "retailers"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_key: Mapped[str] = mapped_column(String(200), unique=True, index=True, nullable=False)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Known country of operation: 2nd priority in warranty region resolution (SRS FR-11a).
    country: Mapped[str | None] = mapped_column(String(8), nullable=True)
    status: Mapped[EntityStatus] = mapped_column(Enum(EntityStatus, name="entity_status"), default=EntityStatus.ACTIVE, nullable=False)


class Product(Base, UuidMixin, TimestampMixin):
    __tablename__ = "products"

    manufacturer_id: Mapped[int | None] = mapped_column(ForeignKey("manufacturers.id", ondelete="SET NULL"), index=True, nullable=True)
    brand: Mapped[str | None] = mapped_column(String(200), nullable=True)
    canonical_name: Mapped[str] = mapped_column(String(300), nullable=False)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model_number: Mapped[str | None] = mapped_column(String(120), index=True, nullable=True)
    sku: Mapped[str | None] = mapped_column(String(120), index=True, nullable=True)
    serial_number_pattern: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[EntityStatus] = mapped_column(Enum(EntityStatus, name="entity_status"), default=EntityStatus.ACTIVE, nullable=False)

    aliases: Mapped[list["ProductAlias"]] = relationship(back_populates="product", cascade="all, delete-orphan")


class AliasType(enum.StrEnum):
    RETAILER_ABBREVIATION = "RETAILER_ABBREVIATION"
    RECEIPT_TEXT = "RECEIPT_TEXT"
    MODEL_VARIANT = "MODEL_VARIANT"
    TRANSLATION = "TRANSLATION"
    OTHER = "OTHER"


class ProductAlias(Base, TimestampMixin):
    """Retailer-specific / receipt-specific representation of a product (TSD §10.2).

    The original alias text is always preserved. Example:
      alias_text = "SAM 55IN LED"  ->  canonical: Samsung 55-inch LED Television
    """

    __tablename__ = "product_aliases"
    __table_args__ = (UniqueConstraint("product_id", "alias_text", name="uq_alias_text"),)

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True, nullable=False)
    retailer_id: Mapped[int | None] = mapped_column(ForeignKey("retailers.id", ondelete="SET NULL"), nullable=True)
    alias_text: Mapped[str] = mapped_column(String(300), nullable=False)
    alias_type: Mapped[AliasType] = mapped_column(Enum(AliasType, name="alias_type"), default=AliasType.RECEIPT_TEXT, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str | None] = mapped_column(String(120), nullable=True)

    product: Mapped[Product] = relationship(back_populates="aliases")


class RetailerCatalogEntry(Base, TimestampMixin):
    """How a retailer lists a product in its catalog/receipts (TSD §12:
    "Retailer-specific abbreviations may be stored through product aliases or
    retailer catalogs"). Used by the retailer-catalog stage of product
    matching (TSD §28)."""

    __tablename__ = "retailer_catalog_entries"
    __table_args__ = (UniqueConstraint("retailer_id", "catalog_sku", name="uq_retailer_catalog_sku"),)

    retailer_id: Mapped[int] = mapped_column(ForeignKey("retailers.id", ondelete="CASCADE"), index=True, nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True, nullable=False)
    catalog_sku: Mapped[str | None] = mapped_column(String(120), nullable=True)
    catalog_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    catalog_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    source: Mapped[str | None] = mapped_column(String(120), nullable=True)


class ProductMatchMethod(enum.StrEnum):
    """TSD §28 resolution stages (+ MANUAL/NONE outcomes)."""

    EXACT_SKU = "EXACT_SKU"
    EXACT_MODEL = "EXACT_MODEL"
    MANUFACTURER_MODEL = "MANUFACTURER_MODEL"
    CATALOG_MATCH = "CATALOG_MATCH"
    ALIAS_MATCH = "ALIAS_MATCH"
    FUZZY_MATCH = "FUZZY_MATCH"
    SEMANTIC_MATCH = "SEMANTIC_MATCH"
    MANUAL = "MANUAL"
    NONE = "NONE"


class ProductMatchStatus(enum.StrEnum):
    AUTO_MATCHED = "AUTO_MATCHED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    NO_MATCH = "NO_MATCH"


class ProductMatch(Base, TimestampMixin):
    """Per-document product resolution record (SRS ML-04/ML-06/ML-07,
    TSD §28).

    Preservation contract (SRS ML-06): the original receipt values are
    stored verbatim alongside the normalized values, the full candidate
    list, the match method, confidence and supporting evidence. A
    low-confidence candidate is NEVER linked as the user's product —
    only AUTO_MATCHED (high confidence + unambiguous) or CONFIRMED
    (explicit user action) sets product_id.
    """

    __tablename__ = "product_matches"

    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), unique=True, index=True, nullable=False)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), index=True, nullable=True)
    match_method: Mapped[ProductMatchMethod] = mapped_column(
        Enum(ProductMatchMethod, name="product_match_method"), default=ProductMatchMethod.NONE, nullable=False
    )
    match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[ProductMatchStatus] = mapped_column(
        Enum(ProductMatchStatus, name="product_match_status"), default=ProductMatchStatus.REVIEW_REQUIRED, nullable=False
    )

    # Original receipt values (verbatim, never normalized in place).
    product_name_raw: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sku_raw: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model_raw: Mapped[str | None] = mapped_column(String(120), nullable=True)
    manufacturer_raw: Mapped[str | None] = mapped_column(String(200), nullable=True)
    retailer_raw: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Normalized values used for matching.
    sku_normalized: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model_normalized: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Full ranked candidate list: [{product_id, product_name, manufacturer,
    # model_number, sku, method, confidence, evidence: [str]}].
    candidates: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # Supporting evidence for the selected/top candidate.
    evidence: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(nullable=True)
