"""Schemas for purchase endpoints (TSD §19)."""

from datetime import datetime

from pydantic import BaseModel, Field


class PurchaseItemCreate(BaseModel):
    product_id: int | None = None
    product_name: str | None = Field(default=None, max_length=300)
    sku: str | None = Field(default=None, max_length=120)
    model_number: str | None = Field(default=None, max_length=120)
    serial_number: str | None = Field(default=None, max_length=120)
    quantity: int | None = Field(default=None, ge=1)
    unit_price: float | None = Field(default=None, ge=0)
    line_total: float | None = Field(default=None, ge=0)
    # Warranty anchor dates (ISO): installation / product registration.
    # Used by the timeline engine for INSTALLATION_DATE / registration-
    # dependent coverage; absent => the timeline is UNKNOWN, never guessed.
    installation_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    registration_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class PurchaseCreate(BaseModel):
    document_id: int | None = None
    retailer_id: int | None = None
    # Free-text retailer name (e.g. from a confirmed document field). Resolved
    # server-side via get_or_create_retailer when retailer_id isn't given
    # explicitly. retailer_id always wins if both are present.
    retailer_name: str | None = Field(default=None, max_length=200)
    purchase_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    invoice_number: str | None = Field(default=None, max_length=120)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    subtotal: float | None = Field(default=None, ge=0)
    tax: float | None = Field(default=None, ge=0)
    total: float | None = Field(default=None, ge=0)
    # Region resolution (SRS FR-11a): explicit region for this purchase.
    warranty_region: str | None = Field(default=None, min_length=2, max_length=2)
    items: list[PurchaseItemCreate] = Field(default_factory=list)


class PurchaseUpdate(BaseModel):
    purchase_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    invoice_number: str | None = Field(default=None, max_length=120)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    subtotal: float | None = Field(default=None, ge=0)
    tax: float | None = Field(default=None, ge=0)
    total: float | None = Field(default=None, ge=0)
    warranty_region: str | None = Field(default=None, min_length=2, max_length=2)
    retailer_id: int | None = None
    retailer_name: str | None = Field(default=None, max_length=200)
    items: list[PurchaseItemCreate] | None = None


class PurchaseItemOut(BaseModel):
    id: int
    product_id: int | None
    product_name: str | None
    product_canonical_name: str | None = None
    manufacturer_name: str | None = None
    sku: str | None
    model_number: str | None
    serial_number: str | None
    quantity: int | None
    unit_price: float | None
    line_total: float | None


class PurchaseOut(BaseModel):
    id: int
    uuid: str
    document_id: int | None
    document_status: str | None = None
    document_filename: str | None = None
    retailer_name: str | None
    purchase_date: str | None
    invoice_number: str | None
    currency: str | None
    subtotal: float | None
    tax: float | None
    total: float | None
    warranty_region: str | None
    warranty_region_resolution_method: str | None
    verification_status: str
    items: list[PurchaseItemOut]
    created_at: datetime
    updated_at: datetime
