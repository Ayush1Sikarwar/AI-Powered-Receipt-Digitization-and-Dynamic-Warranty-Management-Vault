"""Admin catalog management schemas (TSD §43)."""

from pydantic import BaseModel, Field


class ManufacturerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    website: str | None = Field(default=None, max_length=255)
    country: str | None = Field(default=None, min_length=2, max_length=2)


class ManufacturerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    website: str | None = Field(default=None, max_length=255)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    status: str | None = None  # ACTIVE|INACTIVE|PENDING


class RetailerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    website: str | None = Field(default=None, max_length=255)
    country: str | None = Field(default=None, min_length=2, max_length=2)


class RetailerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    website: str | None = Field(default=None, max_length=255)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    status: str | None = None  # ACTIVE|INACTIVE|PENDING


class ProductCreate(BaseModel):
    manufacturer_id: int | None = Field(default=None, ge=1)
    brand: str | None = Field(default=None, max_length=200)
    canonical_name: str = Field(min_length=1, max_length=300)
    category: str | None = Field(default=None, max_length=120)
    model_number: str | None = Field(default=None, max_length=120)
    sku: str | None = Field(default=None, max_length=120)
    serial_number_pattern: str | None = Field(default=None, max_length=120)


class ProductUpdate(BaseModel):
    manufacturer_id: int | None = Field(default=None, ge=1)
    brand: str | None = Field(default=None, max_length=200)
    canonical_name: str | None = Field(default=None, min_length=1, max_length=300)
    category: str | None = Field(default=None, max_length=120)
    model_number: str | None = Field(default=None, max_length=120)
    sku: str | None = Field(default=None, max_length=120)
    serial_number_pattern: str | None = Field(default=None, max_length=120)
    status: str | None = None  # ACTIVE|INACTIVE|PENDING


class ProductAliasCreate(BaseModel):
    alias_text: str = Field(min_length=1, max_length=300)
    retailer_id: int | None = Field(default=None, ge=1)
