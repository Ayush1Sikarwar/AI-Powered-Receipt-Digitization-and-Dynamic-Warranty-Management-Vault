"""Purchase model (TSD §9).

A purchase is the normalized commercial transaction derived from one or more
documents. A purchase contains one or more PurchaseItems; each item references
a Product (and later a WarrantyCoverage, added in Phase 4).
"""

import enum
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UuidMixin


class PurchaseVerification(enum.StrEnum):
    DRAFT = "DRAFT"
    AI_EXTRACTED = "AI_EXTRACTED"
    USER_CONFIRMED = "USER_CONFIRMED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"


class RegionResolution(enum.StrEnum):
    """How warranty_region was resolved (SRS FR-11a)."""

    USER_EXPLICIT = "USER_EXPLICIT"  # 1: user set region for this purchase
    RETAILER_COUNTRY = "RETAILER_COUNTRY"  # 2: retailer's known country
    USER_ACCOUNT = "USER_ACCOUNT"  # 3: user's account-level country
    UNRESOLVED = "UNRESOLVED"  # none resolved with confidence -> status UNKNOWN


class Purchase(Base, UuidMixin, TimestampMixin):
    __tablename__ = "purchases"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    # A purchase is typically derived from one primary document, but may
    # reference more via purchase items; keep the primary for provenance.
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), index=True, nullable=True)
    retailer_id: Mapped[int | None] = mapped_column(ForeignKey("retailers.id", ondelete="SET NULL"), nullable=True)
    purchase_date: Mapped[str | None] = mapped_column(String(10), index=True, nullable=True)  # ISO YYYY-MM-DD
    invoice_number: Mapped[str | None] = mapped_column(String(120), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    subtotal: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    tax: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    total: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    warranty_region: Mapped[str | None] = mapped_column(String(8), nullable=True)
    warranty_region_resolution_method: Mapped[RegionResolution | None] = mapped_column(
        Enum(RegionResolution, name="region_resolution"), nullable=True
    )
    verification_status: Mapped[PurchaseVerification] = mapped_column(
        Enum(PurchaseVerification, name="purchase_verification"), default=PurchaseVerification.DRAFT, nullable=False
    )
    # Soft-delete (TSD §34).
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list["PurchaseItem"]] = relationship(back_populates="purchase", cascade="all, delete-orphan")


class PurchaseItem(Base, UuidMixin, TimestampMixin):
    __tablename__ = "purchase_items"

    purchase_id: Mapped[int] = mapped_column(ForeignKey("purchases.id", ondelete="CASCADE"), index=True, nullable=False)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), index=True, nullable=True)
    # Raw extracted values retained for lineage even before product resolution.
    product_name_raw: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sku_raw: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model_number_raw: Mapped[str | None] = mapped_column(String(120), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(120), nullable=True)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit_price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    line_total: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    # User-supplied anchor dates for coverage windows (TSD §15). Optional:
    # when absent, coverage anchored to them stays UNKNOWN (never guessed).
    installation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    registration_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    purchase: Mapped["Purchase"] = relationship(back_populates="items")
