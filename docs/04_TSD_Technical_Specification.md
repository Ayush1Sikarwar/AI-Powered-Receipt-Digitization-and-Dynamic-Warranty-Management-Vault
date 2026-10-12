# WarrantyVault AI --- Technical Specification Document (TSD)

**Version:** 1.0\
**Date:** 10 August 2026\
**Status:** Pre-development engineering baseline\
**Purpose:** Convert the SRS, SDD, and PRD into an implementation-ready
technical contract.

------------------------------------------------------------------------

# 1. Document Purpose

This document defines the technical contracts required to implement
WarrantyVault AI consistently from the existing SRS, SDD, and PRD.

It specifies:

-   repository/module boundaries
-   system interfaces
-   database entities and relationships
-   API contracts
-   document-processing workflow
-   AI/ML contracts
-   warranty-data architecture
-   background jobs
-   notification workflow
-   claims workflow
-   security boundaries
-   validation rules
-   testing strategy
-   observability
-   deployment requirements
-   implementation order
-   definition of technical completion

This document does not prescribe implementation code. It defines what
the implementation must satisfy.

------------------------------------------------------------------------

# 2. System Boundary

## 2.1 Primary System

WarrantyVault AI consists of:

``` text
Consumer Web Application
        |
        v
API / Application Layer
        |
        +--------------------+
        |                    |
        v                    v
Document AI              Warranty Intelligence
        |                    |
        +---------+----------+
                  |
                  v
          Product/Purchase Data
                  |
          +-------+-------+
          |               |
          v               v
    Notifications       Claims
```

## 2.2 External Systems

The platform may integrate with:

-   manufacturer warranty pages/APIs
-   retailer product catalogs
-   email providers
-   push notification providers
-   object storage
-   OCR/model providers
-   optional manufacturer claim portals

External data must never automatically become authoritative without
validation and provenance.

------------------------------------------------------------------------

# 3. Technical Principles

1.  Original evidence is immutable.
2.  AI output is a suggestion until sufficiently verified.
3.  Every externally sourced warranty fact has provenance.
4.  User corrections never overwrite the original extraction result.
5.  Every important AI result is versioned.
6.  Deterministic rules take precedence over probabilistic inference
    where possible.
7.  External web content is untrusted input.
8.  Every user-owned resource is authorization protected.
9.  Long-running AI processing is asynchronous.
10. APIs remain versioned.
11. Failed processing is retryable.
12. Every production-critical workflow is observable.
13. Sensitive data is minimized.
14. No AI component may silently fabricate missing receipt values.
15. Claim automation must not bypass manufacturer security controls such
    as CAPTCHA or MFA.

------------------------------------------------------------------------

# 4. Repository Contract

Recommended repository:

``` text
warranty-vault/
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   └── v1/
│   │   │       ├── auth.py
│   │   │       ├── documents.py
│   │   │       ├── purchases.py
│   │   │       ├── products.py
│   │   │       ├── warranties.py
│   │   │       ├── notifications.py
│   │   │       └── claims.py
│   │   │
│   │   ├── core/
│   │   │   ├── config
│   │   │   ├── security
│   │   │   ├── logging
│   │   │   └── exceptions
│   │   │
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── repositories/
│   │   ├── services/
│   │   ├── ai/
│   │   │   ├── preprocessing/
│   │   │   ├── classification/
│   │   │   ├── ocr/
│   │   │   ├── extraction/
│   │   │   └── matching/
│   │   ├── warranty/
│   │   │   ├── sources/
│   │   │   ├── retrieval/
│   │   │   └── rules/
│   │   ├── notifications/
│   │   ├── claims/
│   │   ├── workers/
│   │   └── main.py
│   │
│   └── tests/
│       ├── unit/
│       ├── integration/
│       ├── api/
│       ├── ai/
│       └── security/
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── layouts/
│   │   ├── services/
│   │   ├── hooks/
│   │   ├── types/
│   │   └── utils/
│   └── tests/
│
├── data/
│   ├── raw/
│   ├── processed/
│   ├── evaluation/
│   └── fixtures/
│
├── infrastructure/
│   ├── docker/
│   ├── migrations/
│   ├── monitoring/
│   └── deployment/
│
├── docs/
│
├── .env.example
├── docker-compose.yml
├── README.md
└── LICENSE
```

Module boundaries must prevent business logic from being embedded
directly in API route handlers.

------------------------------------------------------------------------

# 5. Core Domain Model

## 5.1 User

Represents an authenticated consumer.

Required concepts:

``` text
id
email
password_hash
display_name
timezone
locale
created_at
updated_at
status
```

Passwords are never stored in plaintext.

------------------------------------------------------------------------

# 6. Document Model

## 6.1 Document

Represents an uploaded original.

Required concepts:

``` text
id
user_id
document_type
original_filename
mime_type
file_size
storage_key
sha256_hash
page_count
status
created_at
updated_at
```

Allowed document types:

``` text
RECEIPT
INVOICE
WARRANTY_CARD
PURCHASE_ORDER
OTHER
UNKNOWN
```

Processing states:

``` text
UPLOADED
QUEUED
PROCESSING
REVIEW_REQUIRED
COMPLETED
FAILED
DELETED
```

The original storage object must never be modified by the processing
pipeline.

------------------------------------------------------------------------

# 7. Document Page / Derivative Model

A document may have multiple pages and multiple processed derivatives.

Examples:

``` text
ORIGINAL
ENHANCED
CROPPED
OCR_PREVIEW
THUMBNAIL
```

Each derivative stores:

``` text
id
document_id
storage_key
derivative_type
processing_version
created_at
```

------------------------------------------------------------------------

# 8. Extraction Model

## 8.1 Extraction Job

Represents one processing execution.

``` text
id
document_id
pipeline_version
status
started_at
completed_at
error_code
error_message
```

## 8.2 Extracted Field

Every extracted field should include:

``` text
id
document_id
field_name
field_value
normalized_value
confidence
source_page
source_region
extraction_method
model_name
model_version
verification_status
created_at
```

Verification states:

``` text
AI_EXTRACTED
USER_CONFIRMED
USER_CORRECTED
REJECTED
```

Supported fields:

``` text
retailer
purchase_date
invoice_number
product_name
manufacturer
sku
model_number
serial_number
quantity
unit_price
total_price
tax
currency
```

------------------------------------------------------------------------

# 9. Purchase Model

A purchase represents the normalized commercial transaction derived from
one or more documents.

``` text
id
user_id
document_id
product_id
retailer_id
purchase_date
invoice_number
currency
subtotal
tax
total
warranty_region
warranty_region_resolution_method
verification_status
created_at
updated_at
```

`warranty_region` resolves in this order: (1) region explicitly set by
the user for this purchase, (2) retailer's known country of operation,
(3) user's account-level country setting. `warranty_region_resolution_method`
records which of the three was used, so region-dependent warranty
terms remain auditable if account settings change later. If no region
resolves with sufficient confidence, warranty status for the purchase's
coverage must be `UNKNOWN` rather than silently defaulting to a region.

A purchase may contain multiple products.

Therefore the implementation should support:

``` text
Purchase
   |
   +--- PurchaseItem
            |
            +--- Product
```

------------------------------------------------------------------------

# 10. Product Model

## 10.1 Product

``` text
id
manufacturer_id
brand
canonical_name
category
model_number
sku
serial_number_pattern
created_at
updated_at
```

## 10.2 Product Alias

Stores retailer-specific or receipt-specific representations.

``` text
id
product_id
retailer_id
alias_text
alias_type
confidence
source
```

Example:

``` text
Receipt text:
"SAM 55IN LED"

Canonical:
Samsung 55-inch LED Television
```

The system must preserve the original alias.

------------------------------------------------------------------------

# 11. Manufacturer Model

``` text
id
name
website
country
status
created_at
updated_at
```

Manufacturer identity must be normalized so different spellings do not
create duplicate manufacturers.

------------------------------------------------------------------------

# 12. Retailer Model

``` text
id
name
website
country
status
created_at
updated_at
```

Retailer-specific abbreviations may be stored through product aliases or
retailer catalogs.

------------------------------------------------------------------------

# 13. Warranty Domain

## 13.1 Warranty Policy

``` text
id
manufacturer_id
product_category
product_model_pattern
region
policy_name
policy_version
effective_from
effective_to
source_id
status
created_at
```

## 13.2 Warranty Coverage

Represents an individual coverage component.

``` text
id
policy_id
component
duration_value
duration_unit
start_condition
registration_required
coverage_description
exclusions
```

Example:

``` text
component = COMPRESSOR
duration = 5 YEARS
start_condition = PURCHASE_DATE
```

## 13.3 Warranty Source

``` text
id
manufacturer_id
source_type
source_url
document_hash
retrieved_at
effective_date
region
source_title
source_version
trust_level
```

Source types:

``` text
OFFICIAL_API
OFFICIAL_WEB
OFFICIAL_DOCUMENT
APPROVED_PARTNER
CURATED_DATASET
OTHER
```

The UI must display appropriate source/provenance information for
warranty facts.

------------------------------------------------------------------------

# 14. Warranty Mapping

A purchase-to-warranty mapping must contain:

``` text
id
purchase_item_id
warranty_policy_id
match_method
match_confidence
verification_status
mapped_at
```

Match methods:

``` text
EXACT_MODEL
EXACT_SKU
CATALOG_MATCH
FUZZY_MATCH
SEMANTIC_MATCH
MANUAL
```

Low-confidence mappings must be reviewable.

------------------------------------------------------------------------

# 15. Warranty Timeline

For each applicable coverage:

``` text
purchase_date
start_date
expiry_date
duration
component
status
```

Statuses:

``` text
NOT_STARTED
ACTIVE
EXPIRING
EXPIRED
UNKNOWN
```

The rules engine must account for manufacturer-specific start
conditions.

------------------------------------------------------------------------

# 16. API Standards

Base path:

``` text
/api/v1
```

All APIs must return consistent success/error structures.

Example conceptual response:

``` text
success:
{
  data: ...
  request_id: ...
}

error:
{
  error_code: ...
  message: ...
  details: ...
  request_id: ...
}
```

No sensitive information should appear in errors.

------------------------------------------------------------------------

# 17. Authentication API

``` text
POST /api/v1/auth/register
POST /api/v1/auth/login
POST /api/v1/auth/logout
POST /api/v1/auth/refresh
POST /api/v1/auth/password-reset/request
POST /api/v1/auth/password-reset/confirm
GET  /api/v1/auth/me
```

Requirements:

-   authenticated endpoints require valid credentials
-   expired sessions/tokens must be rejected
-   logout must invalidate the applicable session/token mechanism
-   rate limiting is mandatory for login and password-reset endpoints

------------------------------------------------------------------------

# 18. Document API

``` text
POST   /api/v1/documents
GET    /api/v1/documents
GET    /api/v1/documents/{document_id}
GET    /api/v1/documents/{document_id}/status
GET    /api/v1/documents/{document_id}/derivatives
DELETE /api/v1/documents/{document_id}
```

Upload flow:

``` text
Client
 ↓
API validation
 ↓
Object storage
 ↓
Document record
 ↓
Processing job
 ↓
AI pipeline
 ↓
Extraction result
 ↓
Review/completion
```

The upload endpoint must not perform long-running OCR synchronously.

------------------------------------------------------------------------

# 19. Purchase API

``` text
GET   /api/v1/purchases
POST  /api/v1/purchases
GET   /api/v1/purchases/{purchase_id}
PATCH /api/v1/purchases/{purchase_id}
DELETE /api/v1/purchases/{purchase_id}
```

Manual purchase creation is allowed, but AI-extracted purchase data must
retain its document provenance.

------------------------------------------------------------------------

# 20. Product API

``` text
GET /api/v1/products/search
GET /api/v1/products/{product_id}
POST /api/v1/products/resolve
```

Product resolution input:

``` text
manufacturer
brand
sku
model_number
description
retailer
```

Output should contain:

``` text
candidate_product
confidence
match_method
supporting_evidence
requires_confirmation
```

------------------------------------------------------------------------

# 21. Warranty API

``` text
GET /api/v1/warranties
GET /api/v1/warranties/{warranty_id}
GET /api/v1/purchases/{purchase_id}/coverage
POST /api/v1/warranties/resolve
```

Warranty resolution must return:

``` text
policy
coverage_components
start_date
expiry_dates
source
confidence
verification_status
```

------------------------------------------------------------------------

# 22. Notification API

``` text
GET   /api/v1/notifications              history (filters: unread, status; paged)
PATCH /api/v1/notifications/{id}/read    mark one read
POST  /api/v1/notifications/read-all     mark all read
GET   /api/v1/notifications/preferences  channel toggles + timezone + channel config state
PUT   /api/v1/notifications/preferences  update channel toggles (partial body)
```

Notification categories:

``` text
WARRANTY_EXPIRING
PROCESSING_COMPLETE
REVIEW_REQUIRED
CLAIM_READY
SYSTEM
```

------------------------------------------------------------------------

# 23. Claims API

``` text
POST   /api/v1/claims
GET    /api/v1/claims
GET    /api/v1/claims/{claim_id}
PATCH  /api/v1/claims/{claim_id}                 editable fields only (NO status)
POST   /api/v1/claims/{claim_id}/evidence        upload supporting media
GET    /api/v1/claims/{claim_id}/evidence
POST   /api/v1/claims/{claim_id}/documents       attach stored document (role: RECEIPT|WARRANTY|EVIDENCE)
GET    /api/v1/claims/{claim_id}/documents
DELETE /api/v1/claims/{claim_id}/documents/{document_id}
POST   /api/v1/claims/{claim_id}/package         generate (DRAFT -> READY)
GET    /api/v1/claims/{claim_id}/package         package JSON (with provenance + report)
GET    /api/v1/claims/{claim_id}/package/download    package JSON file
GET    /api/v1/claims/{claim_id}/package/report      claim-ready report (markdown)
GET    /api/v1/claims/{claim_id}/package/bundle      portable evidence bundle (ZIP)
```

The API has no submission endpoint: a claim can only reach READY by
generating its package (TSD §57).

Claim states:

``` text
DRAFT
READY
SUBMITTED_EXTERNALLY
IN_PROGRESS
RESOLVED
CLOSED
```

The MVP generates a claim-ready package. Direct portal submission is a
later integration.

------------------------------------------------------------------------

# 24. AI Processing Contract

## 24.1 Pipeline

``` text
Document
 ↓
Quality Assessment
 ↓
Classification
 ↓
Preprocessing
 ↓
OCR Pass 1
 ↓
Enhanced Image
 ↓
OCR Pass 2
 ↓
OCR Comparison
 ↓
Structured Extraction
 ↓
Normalization
 ↓
Validation
 ↓
Confidence Scoring
 ↓
Human Review if needed
```

------------------------------------------------------------------------

# 25. Image Enhancement Contract

Inputs:

``` text
original document
document type
page metadata
```

Operations may include:

``` text
deskew
perspective correction
denoise
illumination normalization
contrast enhancement
adaptive thresholding
background removal
upscaling
sharpening
```

Output:

``` text
enhanced derivative
processing metadata
quality score
```

Critical rule:

> Enhancement may improve visibility but must not be treated as proof
> that an unreadable character actually existed.

------------------------------------------------------------------------

# 26. OCR Contract

OCR output must preserve:

``` text
text
confidence
bounding boxes
page number
line/word structure
OCR engine
OCR model/version
```

The system should support fallback OCR engines.

OCR failure must produce a recoverable processing state rather than
silently returning empty data.

------------------------------------------------------------------------

# 27. Structured Extraction Contract

Extraction must be schema-constrained.

Example conceptual output:

``` text
{
  retailer,
  purchase_date,
  product_name,
  manufacturer,
  sku,
  model_number,
  serial_number,
  quantity,
  unit_price,
  total_price,
  currency
}
```

Every field requires:

``` text
value
confidence
source
```

The model must return null/unknown when evidence is insufficient.

It must not invent values merely to satisfy a schema.

------------------------------------------------------------------------

# 28. Product Matching Contract

Resolution order:

``` text
Exact model/SKU
      ↓
Manufacturer + model
      ↓
Retailer catalog
      ↓
Alias matching
      ↓
Fuzzy matching
      ↓
Semantic matching
      ↓
User confirmation
```

False-positive product matches are more harmful than unresolved
products.

Therefore the system should prefer:

``` text
UNKNOWN / REVIEW
```

over a low-confidence incorrect product.

------------------------------------------------------------------------

# 29. Warranty Retrieval Contract

Warranty retrieval should use this priority:

``` text
Official API
    ↓
Official manufacturer document
    ↓
Official manufacturer website
    ↓
Approved/curated source
```

Each result must include provenance.

The retrieval layer should support source adapters so one manufacturer's
implementation does not affect another.

------------------------------------------------------------------------

# 30. Warranty Rules Engine

Input:

``` text
purchase date
product
manufacturer
region
policy
coverage component
registration state
installation date if applicable
```

Output:

``` text
start_date
expiry_date
status
rule_applied
```

Example:

``` text
Purchase Date: 2026-08-10
Coverage: Parts
Duration: 1 year
Start: Purchase date
Expiry: 2027-08-10
```

The engine must support multiple simultaneous coverage periods.

------------------------------------------------------------------------

# 31. Background Jobs

Long-running or scheduled operations must use workers.

Required job types:

``` text
DOCUMENT_PROCESSING
OCR_PROCESSING
PRODUCT_RESOLUTION
WARRANTY_RESOLUTION
NOTIFICATION_SCHEDULING
CLAIM_PACKAGE_GENERATION
SOURCE_REFRESH
CLEANUP
```

Each job needs:

``` text
id
type
status
attempt_count
created_at
started_at
completed_at
error
```

Retry policy must use bounded retries and avoid duplicate side effects.

------------------------------------------------------------------------

# 32. Notification Scheduling

Default:

``` text
60 days before expiry
30 days before expiry
7 days before expiry
```

Notifications must be idempotent.

A unique logical notification key should prevent duplicates. The key
must incorporate a snapshot of the coverage's `expiry_date` at
scheduling time, not only the coverage's id, so that a user correction
which shifts `purchase_date` or `expiry_date` produces a distinct key
rather than colliding with a stale one. When an `expiry_date` changes,
any previously scheduled but not-yet-sent notification tied to the old
`expiry_date` must be cancelled, not left pending or silently reused.

Example:

``` text
purchase_id + coverage_id + expiry_date_snapshot + reminder_type
```

------------------------------------------------------------------------

# 33. Claim Package Architecture

The claim package should contain:

``` text
Claim summary
Purchase information
Product information (model / SKU)
Serial number
Manufacturer warranty coverage
Warranty source
Receipt (with its extracted fields)
Warranty document
Issue description
Supporting photos
Supporting videos if applicable
```

Every item in the package carries a provenance/trust state. The generated
package must clearly distinguish:

``` text
verified information
AI-extracted information
user-confirmed information
external warranty information
inferred information (explicitly NOT verified)
computed values (deterministic, e.g. expiry timelines)
```

The package is delivered three ways: package JSON (download), a
human-readable claim-ready report (markdown), and a portable evidence
bundle (ZIP: package + report + evidence files + attached documents,
self-verifying via sha256).

Users can select already-stored documents as claim evidence with a
declared role (RECEIPT / WARRANTY / EVIDENCE) instead of re-uploading.

------------------------------------------------------------------------

# 34. Database Integrity

Required database protections:

-   foreign keys
-   unique constraints
-   appropriate indexes
-   timestamp fields
-   soft-delete strategy where required
-   transactional updates
-   migration versioning

Important indexes:

``` text
User.email
Document.user_id
Document.status
Purchase.user_id
Purchase.purchase_date
Product.sku
Product.model_number
WarrantyPolicy.manufacturer_id
WarrantyPolicy.region
WarrantyCoverage.expiry_date
Notification.user_id
Notification.scheduled_at
```

------------------------------------------------------------------------

# 35. Object Storage

Object storage must separate:

``` text
original/
processed/
enhanced/
thumbnails/
claims/
```

Storage keys must not expose user-controlled paths directly.

Documents must be accessed through authorized application logic or
short-lived signed URLs.

------------------------------------------------------------------------

# 36. Security Contract

## Authentication

-   secure password hashing
-   session/token expiration
-   refresh/revocation strategy
-   brute-force protection

## Authorization

Every resource access must verify ownership or explicit administrative
permission.

## Upload Security

-   MIME validation
-   file signature validation
-   size limits
-   filename sanitization
-   malware scanning where available
-   isolated processing
-   no executable uploads

## Data Security

-   TLS in transit
-   encrypted storage where supported
-   secret management
-   no credentials in repository
-   database least-privilege access

## AI Security

-   prompt/data separation
-   untrusted external content handling
-   schema-constrained outputs
-   no direct execution of model-generated instructions
-   source provenance

**Implementation note (security audit 2026-09-04):** every sub-contract of this
section was executed and verified live: per-user authorization (IDOR probe
suite 25/25), brute-force/auth abuse (login 10/min, password-reset 5/h,
upload 20/h, plus a global per-IP API limit of 300/min wired in the request
middleware with 429 envelope + Retry-After), upload security (MIME +
magic-byte validation, streaming 15 MB cap, 40 MP declared-dimension decode
cap against image bombs, server-generated UUID storage keys), secret
management (no secrets in repository, env-driven config, production boot
hard-fails on default/short AUTH_SECRET, development logs a startup
warning), and administrative action auditing. Evidence:
`docs/audit/final-audit-report.md` §4.

------------------------------------------------------------------------

# 37. Privacy Contract

The platform should collect only information necessary for:

-   account management
-   warranty management
-   claim preparation
-   notifications

User controls:

``` text
view data
correct data
export data
delete data
```

Retention periods must be configurable and documented. Default
baseline (see SRS NFR-02):

``` text
Active accounts: retained until user deletion or account closure
Closed accounts: purged within 90 days, unless a legal hold or an
  open claim applies
Documents linked to an open claim: retention extended until the
  claim reaches RESOLVED or CLOSED, even across a deletion request;
  the user must be notified of the hold at request time
Audit logs: retained a minimum of 1 year regardless of document
  deletion
```

A deletion request against a document referenced by a non-terminal
claim must not silently fail or silently ignore the hold --- it must
return an explicit response indicating the hold and the condition
under which deletion will proceed.

------------------------------------------------------------------------

# 38. Audit Log

Auditable events include:

``` text
LOGIN
DOCUMENT_UPLOADED
DOCUMENT_DELETED
AI_EXTRACTION_COMPLETED
FIELD_CORRECTED
PRODUCT_CONFIRMED
WARRANTY_MAPPED
WARRANTY_SOURCE_UPDATED
NOTIFICATION_SENT
CLAIM_CREATED
CLAIM_PACKAGE_GENERATED
ADMIN_ACTION
```

Audit records should include:

``` text
actor
action
resource
timestamp
request_id
metadata
```

Avoid logging raw receipt images, passwords, tokens, or unnecessary
personal data.

------------------------------------------------------------------------

# 39. Error Handling

Standard error categories:

``` text
AUTHENTICATION_ERROR
AUTHORIZATION_ERROR
VALIDATION_ERROR
FILE_ERROR
OCR_ERROR
EXTRACTION_ERROR
PRODUCT_MATCH_ERROR
WARRANTY_SOURCE_ERROR
WARRANTY_MAPPING_ERROR
PROCESSING_ERROR
NOTIFICATION_ERROR
CLAIM_ERROR
INTERNAL_ERROR
```

Errors should be actionable internally but safe externally.

Every request should have a correlation/request ID.

------------------------------------------------------------------------

# 40. Observability

Metrics:

``` text
api_request_latency
api_error_rate
upload_success_rate
document_processing_time
ocr_success_rate
extraction_confidence
manual_review_rate
product_match_accuracy
warranty_match_accuracy
notification_delivery_rate
worker_failure_rate
queue_depth
```

Logs should be structured.

Critical failures should be alertable.

------------------------------------------------------------------------

# 41. Testing Strategy

## Unit Tests

Test:

-   date calculations
-   warranty rules
-   confidence thresholds
-   normalization
-   product matching
-   notification scheduling
-   authorization helpers
-   validation

## Integration Tests

Test:

``` text
API → database
API → object storage
worker → database
AI pipeline → extraction records
warranty source → rules engine
notification → scheduler
claim → package generation
```

## AI Evaluation

Maintain a fixed evaluation dataset.

Metrics:

``` text
OCR: CER/WER
Extraction: Precision/Recall/F1
Product Matching: Top-1/Top-3
Warranty Mapping: accuracy
False Match Rate
Manual Review Rate
```

## Security Tests

Include:

-   unauthorized document access
-   IDOR/resource isolation
-   malicious file upload
-   authentication abuse
-   rate limiting
-   secret leakage
-   injection attempts
-   invalid token/session
-   privilege escalation

**Implementation note (2026-09-04):** all four AI-evaluation metric groups are
now measured on the unmodified production engines and reproducible from
`backend/ai_eval/`: OCR CER/WER + extraction P/R/F1/EM in `RESULTS.json`;
product matching Top-1/Top-3/false-match in
`PRODUCT_MATCHING_RESULTS.{json,md}`; warranty mapping policy/duration/source
validity in `WARRANTY_MAPPING_RESULTS.{json,md}`. Security tests above are
executed, not assumed: IDOR 25/25, privilege-escalation 7/7, SQLi/CORS clean,
plus regression tests for image bombs, oversized PDFs/uploads, out-of-range
IDs, the global API rate limit, and the production secret guard in
`tests/security/test_processing_dos.py`. The PRD §5 dataset floor (≥50 labeled
samples per quality category) is unmet (38 samples), so confidence thresholds
remain PROVISIONAL and are flagged as such in the model registry.

------------------------------------------------------------------------

# 42. Frontend Technical Contract

Required screens:

``` text
Landing
Login
Register
Dashboard
Upload
Processing
Document Review
Purchase Details
Warranty Details
Warranty Calendar/Timeline
Notifications
Claims
Claim Details
Settings
```

## Dashboard Requirements

Display:

``` text
Total purchases
Active warranties
Expiring soon
Expired warranties
Review required
Recent documents
```

Users must be able to open a purchase and trace:

``` text
Purchase
 ↓
Original receipt
 ↓
Extracted data
 ↓
Product
 ↓
Warranty
 ↓
Source
 ↓
Expiry
 ↓
Claim
```

This traceability is a core product feature.

------------------------------------------------------------------------

# 43. Admin Technical Contract

Admin functionality should include:

``` text
manufacturer management
retailer management
product catalog management
warranty source management
policy version management
failed processing review
AI evaluation dashboard
source refresh status
audit logs
```

Administrative APIs must be separately authorized.

**Implementation note (admin/operations):** All of the above is exposed under
`/api/v1/admin/*` (plus the product-alias routes
`/api/v1/admin/products/{id}/aliases` and `/api/v1/admin/failed-processing/
documents/{id}/retry`). Separate authorization is enforced by
`require_admin` (`app/api/deps.py`), which verifies the **stored** user role
(`users.role == "ADMIN") from the database on every request — not a JWT
claim — so a token issued before a role change is re-validated against the
current role (403 for non-admins, 401 when unauthenticated). Every mutating
admin action writes an audit record (`MANUFACTURER_UPDATED`,
`RETAILER_UPDATED`, `PRODUCT_UPDATED`, `PRODUCT_ALIAS_UPDATED`,
`WARRANTY_SOURCE_UPDATED`, `DOCUMENT_RETRY_REQUESTED`). Policy version
management goes through the versioned upsert (`upsert_policy_versioned`): a
content update creates a NEW version row and archives the predecessor; a
status-only update transitions the row without forking a version.

------------------------------------------------------------------------

# 44. Data Source Management

Manufacturer and retailer sources require:

``` text
source identifier
source type
owner
region
retrieval method
last successful retrieval
last failure
content hash
version
status
```

Source refresh jobs must not silently replace an existing policy without
retaining historical versions.

**Implementation note:** All ten tracking attributes are persisted on
`warranty_sources` (migration 0012 added `owner`, `retrieval_method`,
`last_success_at`, `last_failure_at`; the remainder — `id`, `source_type`,
`region`, `document_hash` (content hash), `source_version`,
`retrieval_status` — pre-existed) and are returned in full by
`GET /api/v1/admin/warranty/sources`, `GET /api/v1/admin/warranty/sources/{id}`
and the refresh endpoint. `refresh_source` records `last_success_at` on
successful retrieval (and updates the content hash), and `last_failure_at` +
`last_error` + status FAILED on failure — a failed refresh never deletes,
deactivates, or re-versions an existing policy. Changed content with
structured terms creates a new policy version (predecessor archived, history
preserved); changed content without terms flags `review_required` and keeps
the current policy valid.

------------------------------------------------------------------------

# 45. Model Registry

Every production AI component must identify:

``` text
model_name
model_version
pipeline_version
configuration_version
deployment_date
```

Potential components:

``` text
document_classifier
image_quality_model
ocr_engine
extraction_model
embedding_model
product_ranker
```

**Implementation note:** `app/ai/registry.py` is the single source of truth
for model identity. The registry returns all seven production components
(ocr_engine, document_classifier, image_quality_model, extraction_model,
preprocessing, product_ranker, embedding_model) with the five identification
fields above. `pipeline_version` is read from config (`settings.pipeline_version`),
never hard-coded. Business logic imports model names/versions from the
registry rather than defining them locally: the extraction model in
`app/ai/extraction/extractor.py`, the document classifier in
`app/ai/classification/classifier.py`, and the product matcher engine in
`app/ai/product/matching.py` + `app/services/product_service.py` all reference
registry constants, so a version bump happens in exactly one place.
`GET /api/v1/admin/model-registry` exposes the full registry (admin-only).

------------------------------------------------------------------------

# 46. AI Human-in-the-Loop Rules

Human review is required when:

``` text
critical field confidence < threshold
multiple products have similar scores
warranty source is ambiguous
warranty region is unknown
document is severely degraded
serial/model cannot be reliably identified
```

Critical fields include:

``` text
purchase_date
product identity
model/SKU
serial number
purchase amount
warranty expiry
```

------------------------------------------------------------------------

# 47. Data Lineage

The platform must allow:

``` text
Claim
 ↓
Warranty Coverage
 ↓
Warranty Policy
 ↓
Warranty Source

Purchase
 ↓
Purchase Item
 ↓
Product
 ↓
Extracted Field
 ↓
Receipt Region
 ↓
Original Document
```

This lineage is required for trust and debugging.

------------------------------------------------------------------------

# 48. Performance Targets

Initial engineering targets:

``` text
API p95 latency:
< 500 ms for normal non-AI API requests

Single-page typical receipt:
initial processing target < 10 seconds

Background processing:
scalable independently

Dashboard:
fast initial load with paginated data

Search:
indexed database queries
```

AI latency may vary by model and hardware and should be measured rather
than hard-coded into product guarantees.

Throughput targets (documents processed per minute at a given worker
replica count, concurrent uploads supported, concurrent dashboard
users at a given API replica count) are not yet defined and must be
established and load-tested before the "scalable independently" claims
in Section 49 and SRS NFR-05 are considered satisfied (see PRD Phase 6
load-testing deliverable).

**Implementation note (load test 2026-09-04, §48 mandate):**
`backend/scripts/load_test.py` was executed (8 concurrent workers, 20.2 s,
841 requests, 41.6 RPS, 0.0 % errors, 0 × 5xx). Measured p95: non-auth
endpoints 85–222 ms (the < 500 ms target is met); single-page receipt
end-to-end 5.1 s (the < 10 s target is met); over-cap PDF rejected in 1.0 s.
**Documented deviation:** `POST /auth/login` p95 was 793.7 ms under
8-concurrency — Argon2id at t=3 / m=64MiB / p=4 is the configured cost, and
the target is deliberately not met by weakening authentication (audit finding
F13). A product/security decision is required: amend the target with
justification or tune the cost parameters. Results are persisted in
`backend/data/loadtest/`. The global API rate limit was temporarily raised
during the load-test window so the limiter would not contaminate latency
stats; the limiter's own behavior was separately verified with a 360-request
burst (292 × 200 + 68 × 429).

------------------------------------------------------------------------

# 49. Scalability

The architecture must allow:

``` text
API replicas
Worker replicas
OCR workers
AI inference workers
Notification workers
```

Document processing must not block API servers.

Large files must use streaming/upload mechanisms where appropriate.

------------------------------------------------------------------------

# 50. Reliability

Required:

-   retryable jobs
-   transactional database writes
-   immutable originals
-   backup strategy
-   health checks
-   graceful worker failure
-   idempotent notifications
-   idempotent processing jobs where possible

A failed AI job must not corrupt the purchase or original document.

**Implementation note (production readiness 2026-09-04):** every item is
implemented and live-verified. Retryable jobs: the DB-based queue tracks
`attempt_count` with bounded retries; a failed job transitions the
document to `FAILED` and never mutates the purchase or the original
storage object. Transactional writes: all persistence goes through
SQLAlchemy sessions (commit on success only). Immutable originals:
derivatives are written under separate storage prefixes; the original
file is never overwritten. Backups: `infrastructure/scripts/backup.sh`
wraps `backend/scripts/backup.py` (`pg_dump -Fc` + storage tar + manifest
with sha256 + retention via `--keep`), scheduled via the compose `backup`
profile or cron; the restore procedure (`backend/scripts/restore.py`,
runbook §1) was rehearsed — dump restored into a scratch database and the
manifest sha256s verified. Health checks: `/health/live` (process),
`/health/ready` (database + queue + worker heartbeat) and
`/metrics` (Prometheus text) are exposed and wired into
`docker-compose.prod.yml` healthchecks. Graceful worker failure: the
worker publishes a Redis heartbeat every 15 s; `/health/ready` degrades
and the Prometheus alert rules fire when the heartbeat is stale
(> 120 s configurable). Idempotent notifications: the logical key is
purchase/coverage identity + reminder type + expiry-date snapshot (§32).
Idempotent processing: document jobs deduplicate on job identity.
Evidence: `backend/scripts/production_readiness_test.py` (42 live checks,
all passing against the production-configured stack).

------------------------------------------------------------------------

# 51. Deployment Contract

## Development

``` text
Frontend
Backend
PostgreSQL
Redis
Object Storage
Workers
```

should be reproducible through local environment configuration.

## Staging

Separate:

``` text
database
storage
credentials
notification provider
AI configuration
```

## Production

Must include:

``` text
TLS
database backups
object-storage backups/versioning where appropriate
monitoring
logging
secret management
rate limiting
health checks
CI/CD
rollback strategy
```

**Implementation note (production readiness 2026-09-04):** every item in
the Production list is implemented. `infrastructure/docker/` holds
`backend.Dockerfile`, `frontend.Dockerfile` (multi-stage, non-root) and
`docker-compose.prod.yml` — one-shot `migrate` job gates the API, and
`${VAR:?}` guards make the stack refuse to start with any production
variable missing. `infrastructure/nginx/nginx.conf` is the reverse
proxy / API gateway: TLS termination, HTTP→HTTPS 301, HSTS / CSP /
X-Frame-Options / nosniff / no-referrer headers, `/api/` → backend, SPA
static serving, and proxy buffers sized for the 15 MB upload cap.
`infrastructure/nginx/generate-certs.sh` produces the certificate
material (self-signed profile for staging; production must place
CA-issued certificates in the same location) — `infrastructure/nginx/certs/`
is git-ignored, never committed. `infrastructure/postgres/production-
postgresql.conf` hardens PostgreSQL (memory sizing, statement timeouts,
log format / min_messages). Backups: `infrastructure/scripts/backup.sh` +
runbook §1. Object storage: S3 backend with least-privilege credentials,
short-lived signed URLs, and bucket-level versioning recommended (TSD §35
separation is enforced by the storage keys). Monitoring:
`infrastructure/monitoring/` (Prometheus scrape config, alert rules,
runbook with backup/restore, deploy/rollback, TLS, alerts, and log/metric
sources). CI/CD: `.github/workflows/ci.yml` (build, lint, type-check,
test, pip-audit, npm audit on every PR) and
`.github/workflows/deploy.yml` — staging deploys automatically on merge
to `main` after CI is green; production deploys only via manual
`workflow_dispatch` behind a GitHub environment protection rule, with a
separate rollback dispatch. Environment files are supplied from the
base64 secrets `STAGING_ENV_FILE` / `PRODUCTION_ENV_FILE` and are never
stored in the repository. Rollback: `infrastructure/scripts/rollback.sh`
restores the previous immutable git-tagged image recorded in
`.deploy-state` (git-ignored, per host). Development / staging /
production each run separate databases, storage, and credentials
(`infrastructure/environments/production.env.example` and
`staging.env.example`; filled files `.env.production` / `.env.staging`
are git-ignored). All of the above was executed and verified live against
a production-configured stack (TLS edge with secure headers, prod boot
guards, backup + sha256-verified restore, worker heartbeat, rate
limiting, dependency and secret scans):
`backend/scripts/production_readiness_test.py`.

------------------------------------------------------------------------

# 52. Environment Configuration

Required configuration categories:

``` text
APP_ENV
DATABASE_URL
REDIS_URL
OBJECT_STORAGE_CONFIG
AUTH_SECRET
EMAIL_PROVIDER_CONFIG
AI_MODEL_CONFIG
LOG_LEVEL
CORS_CONFIG
UPLOAD_LIMITS
```

Secrets must exist only in secure environment/secret-management systems.

`.env.example` may contain names and placeholders but never real
secrets.

**Implementation note (production readiness 2026-09-04):** the production
and staging templates are `infrastructure/environments/production.env.
example` and `infrastructure/environments/staging.env.example`
(placeholders only). Filled-in files (`.env.production`,
`.env.staging`) live at the repo root, are git-ignored via `.env.*`, and
are delivered at deploy time from the CI/CD secret manager
(`STAGING_ENV_FILE` / `PRODUCTION_ENV_FILE`). Enforcement is layered:
`docker-compose.prod.yml` refuses to start on any missing `${VAR:?}`,
`deploy.sh` dies if a required variable is empty, and the application
itself refuses to boot in production with a weak/default `AUTH_SECRET`,
`DEBUG` on, or a development CORS origin (verified live: boot with a
weak secret fails with `RuntimeError: Refusing to start in production`).
The readiness suite also scans the repository for committed development
secrets and private-key material.

------------------------------------------------------------------------

# 53. API Versioning

Current:

``` text
/api/v1
```

Breaking changes require a new version.

Backward-compatible additions may remain within the same version.

------------------------------------------------------------------------

# 54. Pagination

List endpoints must support:

``` text
page
page_size
sort
filter
```

Large document/purchase/notification lists must never return unlimited
records.

------------------------------------------------------------------------

# 55. Search

Search should support:

``` text
product name
manufacturer
model
SKU
retailer
purchase date
warranty status
```

Search results should expose match type where relevant.

------------------------------------------------------------------------

# 56. Scheduled Source Refresh

Warranty sources may be refreshed on a schedule.

The refresh process must:

``` text
retrieve
 ↓
validate
 ↓
hash/version
 ↓
compare
 ↓
store new version if changed
 ↓
mark previous version historical
```

A failed refresh must not delete a previously valid policy.

------------------------------------------------------------------------

# 57. Claim Automation Safety

The system should initially stop at:

``` text
claim-ready package
```

Automatic external submission can be added only when an official
integration exists and the manufacturer permits automation.

The system must not attempt to bypass:

-   CAPTCHA
-   MFA
-   anti-bot controls
-   access restrictions

------------------------------------------------------------------------

# 58. MVP Technical Sequence

Implementation must proceed in this order:

``` text
1. Repository + environment
2. Database + migrations
3. Authentication
4. Object storage
5. Document upload
6. Basic document processing
7. OCR
8. Structured extraction
9. Review/correction
10. Product catalog
11. Product matching
12. Warranty source model
13. Warranty policies
14. Warranty rules engine
15. Purchase/warranty dashboard
16. Notifications
17. Claim package
18. Testing
19. Security hardening
20. Deployment
```

Advanced degraded-receipt generative reconstruction should be added
after the baseline OCR pipeline is measurable.

------------------------------------------------------------------------

# 59. Definition of Technical Completion

The project is technically ready for MVP release only when:

### Backend

-   all required APIs implemented
-   validation implemented
-   authorization verified
-   database migrations stable
-   background jobs working
-   error handling implemented

### AI

-   OCR benchmark completed
-   extraction benchmark completed
-   confidence thresholds calibrated
-   product matching evaluated
-   warranty mapping evaluated
-   model versions recorded

### Frontend

-   all MVP screens functional
-   upload/review workflow complete
-   warranty dashboard complete
-   notification display complete
-   claim package workflow complete

### Security

-   authentication tested
-   authorization tested
-   file-upload security tested
-   secrets reviewed
-   rate limiting enabled
-   audit logging enabled

### Operations

-   health checks
-   logging
-   metrics
-   backups
-   deployment process
-   rollback procedure

### Product

A real test user can complete:

``` text
Register
  ↓
Upload degraded receipt
  ↓
AI processes receipt
  ↓
Review extracted information
  ↓
Confirm product
  ↓
Warranty is mapped
  ↓
Expiry is calculated
  ↓
Warranty appears on dashboard
  ↓
Expiration reminder is scheduled
  ↓
User creates claim
  ↓
Claim package is generated
```

------------------------------------------------------------------------

# 60. Final Engineering Rule

No feature should be considered complete merely because its UI exists.

Every feature must have:

``` text
UI
+
API
+
Business Logic
+
Database
+
Validation
+
Error Handling
+
Authorization
+
Logging
+
Tests
+
Documentation
```

For AI features additionally:

``` text
Model
+
Evaluation Dataset
+
Metrics
+
Confidence
+
Versioning
+
Human Review
+
Provenance
```

This TSD is the technical bridge between the existing project documents
and implementation.
