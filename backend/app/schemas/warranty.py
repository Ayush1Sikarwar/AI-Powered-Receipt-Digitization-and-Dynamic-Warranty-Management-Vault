"""Warranty API schemas (SRS FR-10/FR-12, TSD §14)."""

from typing import Literal

from pydantic import BaseModel, Field


class WarrantyReviewRequest(BaseModel):
    """User decision on a purchase item's warranty mapping (TSD §14:
    low-confidence mappings must be reviewable)."""

    item_id: int
    action: Literal["confirm", "reject", "override"]
    policy_id: int | None = Field(default=None, ge=1)


class CoverageCreate(BaseModel):
    component: str = Field(min_length=1, max_length=120)
    duration_value: int | None = Field(default=None, ge=0)
    duration_unit: str = Field(default="YEAR")
    start_condition: str = Field(default="PURCHASE_DATE")
    registration_required: bool = False
    coverage_description: str | None = None
    exclusions: str | None = None


class WarrantySourceCreate(BaseModel):
    manufacturer_id: int | None = Field(default=None, ge=1)
    source_type: str = Field(default="OFFICIAL_WEB")
    source_url: str | None = Field(default=None, max_length=2048)
    source_title: str = Field(min_length=1, max_length=500)
    source_version: str | None = Field(default=None, max_length=32)
    region: str | None = Field(default=None, min_length=2, max_length=2)
    trust_level: int = Field(default=3, ge=1, le=5)
    # Operational tracking (TSD §44).
    owner: str | None = Field(default=None, max_length=200)
    retrieval_method: str | None = Field(default=None, max_length=40)


class WarrantySourceUpdate(BaseModel):
    """Admin metadata edit; every field optional (partial update)."""

    source_type: str | None = None
    source_url: str | None = Field(default=None, max_length=2048)
    source_title: str | None = Field(default=None, max_length=500)
    source_version: str | None = Field(default=None, max_length=32)
    region: str | None = Field(default=None, min_length=2, max_length=2)
    trust_level: int | None = Field(default=None, ge=1, le=5)
    owner: str | None = Field(default=None, max_length=200)
    retrieval_method: str | None = Field(default=None, max_length=40)
    effective_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    retrieval_status: str | None = None  # SUCCESS|FAILED|PENDING
    manufacturer_id: int | None = Field(default=None, ge=1)


class WarrantyPolicyUpdate(BaseModel):
    """Admin policy management with versioning (TSD §44/§13).

    - ``status`` alone: transition the policy (DRAFT/ACTIVE/ARCHIVED).
    - any content field (name, dates, category, pattern, region,
      coverages): create a NEW policy version via the versioned upsert —
      the previous version is archived and preserved (never overwritten).
    """

    policy_name: str | None = Field(default=None, min_length=1, max_length=300)
    policy_version: str | None = Field(default=None, max_length=32)
    region: str | None = Field(default=None, min_length=2, max_length=2)
    product_category: str | None = Field(default=None, max_length=120)
    product_model_pattern: str | None = Field(default=None, max_length=200)
    effective_from: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    effective_to: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    coverages: list[CoverageCreate] | None = None
    status: str | None = None  # DRAFT|ACTIVE|ARCHIVED


class WarrantyPolicyCreate(BaseModel):
    manufacturer_id: int | None = Field(default=None, ge=1)
    product_category: str | None = Field(default=None, max_length=120)
    product_model_pattern: str | None = Field(default=None, max_length=200)
    region: str | None = Field(default=None, min_length=2, max_length=2)
    policy_name: str = Field(min_length=1, max_length=300)
    policy_version: str = Field(default="1.0", max_length=32)
    effective_from: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    effective_to: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    source: WarrantySourceCreate | None = None
    coverages: list[CoverageCreate] = Field(default_factory=list)


class ExtendedWarrantyCreate(BaseModel):
    """An extended warranty record the user adds for a purchase.

    ``duration_value`` is optional only for UNLIMITED; for a finite unit the
    rules engine reports UNKNOWN (never a guessed date) when it is absent.
    """

    component: str = Field(min_length=1, max_length=120)
    duration_value: int | None = Field(default=None, ge=1)
    duration_unit: str = Field(default="YEAR")  # DAY|WEEK|MONTH|YEAR|UNLIMITED
    start_condition: str = Field(default="PURCHASE_DATE")  # see StartCondition
    source_type: str = Field(default="USER_DECLARED")  # USER_DECLARED|EXTRACTED|EXTERNAL_OFFER
    source_detail: str | None = Field(default=None, max_length=2048)
    notes: str | None = Field(default=None, max_length=2000)
    purchase_item_id: int | None = Field(default=None, ge=1)  # None = whole purchase
