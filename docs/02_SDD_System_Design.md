# WarrantyVault AI --- Software Design Document (SDD)

**Version:** 1.0\
**Date:** 10 August 2026\
**Status:** Pre-development architecture

## 1. Architecture Goal

Build a modular AI platform in which document processing, product
intelligence, warranty intelligence, notifications, and claims
preparation can evolve independently.

## 2. Recommended High-Level Architecture

``` text
                    ┌─────────────────────┐
                    │   Web / Mobile UI   │
                    └──────────┬──────────┘
                               │ HTTPS
                    ┌──────────▼──────────┐
                    │   API Gateway       │
                    │ Auth + Rate Limits  │
                    └──────────┬──────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        │                      │                      │
┌───────▼────────┐    ┌────────▼────────┐    ┌───────▼────────┐
│ Document       │    │ Warranty        │    │ Notification   │
│ Service        │    │ Intelligence    │    │ Service        │
└───────┬────────┘    └────────┬────────┘    └───────┬────────┘
        │                      │                     │
        ▼                      ▼                     ▼
┌───────────────┐      ┌───────────────┐      ┌──────────────┐
│ AI Processing │      │ Product/Warr. │      │ Email/Push   │
│ OCR + CV + IDP│      │ Knowledge Base│      │ Scheduler    │
└───────┬───────┘      └───────────────┘      └──────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│ PostgreSQL + Object Storage + Vector Index + Job Queue │
└─────────────────────────────────────────────────────────┘
```

## 3. Suggested Technology Stack

*The stack below is a recommended default, not a binding contract.
Where the TSD specifies concepts that imply a particular stack (e.g.,
Pydantic-style schema validation), those should be read as
illustrative of the required behavior, not as mandating this exact
stack if the implementation team selects an alternative.*

### Frontend

-   React
-   TypeScript
-   Tailwind CSS
-   React Query

### Backend

-   Python
-   FastAPI
-   Pydantic
-   SQLAlchemy
-   PostgreSQL

### AI/ML

-   OpenCV
-   Tesseract/PaddleOCR or equivalent OCR engine
-   PyTorch
-   Hugging Face Transformers
-   Sentence Transformers
-   Optional LLM for schema extraction/reasoning

### Storage

-   PostgreSQL for structured records
-   S3-compatible object storage for original/processed documents
-   Vector database or pgvector for semantic product/warranty retrieval

### Async Processing

-   Redis
-   Celery/RQ or equivalent worker system

### Deployment

-   Docker
-   Nginx/API gateway
-   Cloud object storage
-   CI/CD pipeline

## 4. Core Services

### 4.1 Auth Service

Responsibilities: - Registration - Login - Session/token management -
Password reset - User authorization

### 4.2 Document Service

Responsibilities: - Upload - MIME/type validation - Virus scanning
integration - Original document storage - Document metadata - Processing
status

### 4.3 Document AI Pipeline

``` text
Upload
  ↓
File Validation
  ↓
Image/PDF Rendering
  ↓
Quality Assessment
  ↓
Preprocessing
  ↓
OCR
  ↓
Layout Detection
  ↓
Structured Extraction
  ↓
Field Validation
  ↓
Confidence Scoring
  ↓
Human Verification if required
```

### 4.4 Receipt Enhancement Pipeline

Recommended stages: 1. Detect document boundaries. 2. Correct
perspective. 3. Estimate blur/noise. 4. Normalize illumination. 5.
Increase local contrast. 6. Remove background artifacts. 7. Upscale when
useful. 8. Produce an enhanced derivative. 9. Run OCR on original and
enhanced versions. 10. Compare outputs.

The system must retain the original image and must never replace it with
an AI-generated reconstruction.

### 4.5 Product Intelligence Service

Input: - Extracted manufacturer - SKU/model - Product description -
Retailer

Processing:

``` text
Exact model/SKU match
        ↓
Manufacturer filtering
        ↓
Retailer catalog lookup
        ↓
Fuzzy matching
        ↓
Semantic similarity
        ↓
Candidate ranking
        ↓
Confidence threshold
        ↓
User confirmation if uncertain
```

### 4.6 Warranty Intelligence Service

The warranty knowledge base should store:

``` text
Manufacturer
Product Category
Model/Series
Warranty Component
Duration
Unit
Start Condition
Region
Exclusions
Source
Source Version
Retrieved At
Effective From
Effective To
```

Warranty retrieval should prefer: 1. Official manufacturer API 2.
Official manufacturer documentation 3. Approved partner/catalog source
4. Curated internal dataset

Search-engine snippets and arbitrary third-party pages should not be
treated as authoritative warranty evidence.

### 4.7 Warranty Rules Engine

Example:

``` text
Purchase Date = 2026-08-10

Parts Warranty = 1 year
Parts Expiry   = 2027-08-10

Compressor Warranty = 5 years
Compressor Expiry   = 2031-08-10
```

The rules engine must support: - Calendar durations - Component-specific
warranties - Region-specific rules - Registration-dependent warranties -
Extended warranties - Purchase-date versus installation-date starts

### 4.8 Notification Service

Default notification schedule: - 60 days before expiry - 30 days before
expiry - 7 days before expiry

Reminder moments are scheduled at 09:00 in the user's IANA timezone (from
account preferences; invalid values fall back to UTC) and stored as UTC.

Notifications should be idempotent so the same reminder is not sent
repeatedly. The idempotency key must include a snapshot of the coverage
`expiry_date` at scheduling time (not just `purchase_id + coverage_id +
reminder_type`), so that a later user correction which shifts the
purchase date or expiry date creates a new, distinct reminder rather
than silently reusing or dropping the old key. Superseded reminders
tied to a stale expiry date must be cancelled, not left pending.

Delivery runs in the background worker sweep (email via a pluggable
provider: `log` dev outbox or `smtp`; optional push via a pluggable
provider: `none` or `log` dev outbox). Per-user preferences control the
channels (email default ON, push default OFF). A failed attempt is
retried with exponential backoff (5m, 10m, 20m; default max 3 attempts)
before the notification is marked FAILED with `last_error`; retries only
re-send channels that have not already been delivered. If no channel is
enabled and configured, the notification is marked SUPPRESSED — an
honest terminal state, not a failure.

### 4.9 Claims Service

The claims module should initially generate a claim package rather than
attempt universal automatic submission. The MVP stops at `CLAIM_READY`
(TSD §57): there is no submission endpoint, and the system never
bypasses CAPTCHA, MFA, anti-bot controls, or access restrictions.
Automatic external submission may only be added where an official,
permitted integration exists.

Claim package: - Customer information - Product details - Purchase
date - Model/SKU - Serial number - Manufacturer warranty coverage -
Expiry date - Warranty source - Receipt (with extracted fields) -
Warranty document - Problem description - Supporting media (photo/
video).

Every package item retains a provenance/trust state, and the UI
distinguishes verified / AI-extracted / user-confirmed / external
source / inferred (plus computed) so AI-inferred data is never
presented as a verified fact.

Users can select already-stored documents as claim evidence with a
declared role (RECEIPT / WARRANTY / EVIDENCE). The package is
downloadable as JSON, as a claim-ready report (markdown), and as a
portable evidence bundle (ZIP with the actual file bytes, verified by
sha256).

User data export: `GET /account/export` returns the user's complete
record as portable JSON; `GET /account/export/bundle` returns the same
as `manifest.json` plus the stored document and evidence files in a
ZIP, so warranty records and supporting evidence export together.

## 5. Database Design

Core entities:

``` text
User
Document
DocumentPage
ExtractionJob
ExtractedField
Product
ProductAlias
Purchase
PurchaseItem
WarrantyPolicy
WarrantyCoverage
WarrantySource
Notification
Claim
AuditLog
```

A `Purchase` may contain multiple line items, so `PurchaseItem` sits
between `Purchase` and `Product`/`WarrantyCoverage` (see TSD §9 and
§14 for the field-level contract). This is the authoritative
relationship; earlier informal diagrams showing `Purchase` linking
directly to `Product`/`WarrantyCoverage` should be read as a
simplification of the path below.

### Relationships

``` text
User
 └── Purchase
      ├── Document(s)
      └── PurchaseItem
             ├── Product
             └── WarrantyCoverage
                    └── WarrantyPolicy
                           └── WarrantySource
```

## 6. API Design

### Authentication

``` text
POST /api/v1/auth/register
POST /api/v1/auth/login
POST /api/v1/auth/logout
POST /api/v1/auth/refresh
```

### Documents

``` text
POST   /api/v1/documents
GET    /api/v1/documents/{id}
DELETE /api/v1/documents/{id}
GET    /api/v1/documents/{id}/processing
```

### Purchases

``` text
GET  /api/v1/purchases
GET  /api/v1/purchases/{id}
PATCH /api/v1/purchases/{id}
```

### Warranty

``` text
GET /api/v1/warranties
GET /api/v1/warranties/{id}
GET /api/v1/purchases/{id}/coverage
```

### Claims

``` text
POST /api/v1/claims
GET  /api/v1/claims/{id}
GET  /api/v1/claims/{id}/package
```

## 7. AI Model Versioning

Every AI result shall record: - model name - model version -
preprocessing version - prompt/schema version where applicable -
timestamp

This allows old records to remain auditable when models change.

## 8. Confidence Strategy

Example thresholds:

``` text
>= 0.90  → Auto-accept candidate
0.70–0.89 → User review
< 0.70   → Manual correction / alternate pipeline
```

Thresholds must be calibrated using validation data rather than assumed
permanently. Calibration requires a labeled evaluation dataset covering
each quality category in the PRD's Dataset Strategy with a minimum
sample size per category sufficient to estimate precision/recall with
an acceptable confidence interval (to be defined jointly by engineering
and whoever owns the evaluation dataset before Phase 2 sign-off).
Thresholds must be re-validated whenever the underlying model, prompt,
or preprocessing version changes, using the AI Model Versioning records
in Section 7.

**Implementation note (2026-09-04):** the 0.90 / 0.70 thresholds are active
end-to-end (extraction review routing and product auto-accept) and are flagged
**PROVISIONAL** in the model registry (`app/ai/registry.py`) because the PRD §5
floor (≥50 labeled samples per quality category) is unmet — the current
evaluation set holds 38 samples. Per this section, thresholds must be
re-validated once the floor is met or whenever the extraction/matching
implementation or preprocessing version changes.

## 9. Security Architecture

``` text
Client
  ↓ HTTPS
API
  ↓ Authorization
Service Layer
  ↓
Database / Object Storage
```

Security controls: - TLS - Secure password hashing - Token/session
expiration - Per-user object authorization - Encryption at rest - Secret
manager/environment secrets - Upload validation - Audit logging - Rate
limiting - Dependency scanning

## 10. Observability

Track: - Upload success/failure - OCR latency - Extraction confidence -
Product-match confidence - Warranty retrieval failures - Notification
failures - API latency - Queue depth - Worker failures

## 10a. Throughput & Load Targets

In addition to the latency targets in the SRS/TSD, the following
throughput targets must be defined and load-tested before production
release (initial placeholders, to be refined against real usage
projections). Measured with `backend/scripts/load_test.py` (read-centric
weighted mix, 16 concurrent workers, 120 s sustained, dev stack):

``` text
Sustained API throughput (read-centric mix, 16 concurrent clients): 509.8 RPS
  p50 16.1 ms  p95 92.3 ms  p99 181.6 ms  mean 30.4 ms  errors 0.0% (0 x 5xx)
  artifact: backend/data/loadtest/loadtest_20260906_071446.json

Concurrent document uploads supported per worker pool: single worker
  processes one document at a time (serialized queue); the API accepts
  uploads regardless (NFR-04: <10 s to a reviewable state per document).
  Upload concurrency is bounded by worker count, not by the API.
Documents processed per minute at target worker replica count: 1 worker
  ~= 15-30 documents/min on the benchmark receipt class (2-4 s per document
  observed in the live release-gate journey and e2e suites). Scales
  linearly with worker replicas.
Concurrent active dashboard users at target API replica count: 509.8 RPS
  sustained with 0 errors at 16 concurrent clients on 1 API replica; the
  read path is stateless, so additional replicas scale linearly.
```

"Scalable independently" (NFR-05) is not considered satisfied until
these numbers exist and have been validated under load testing (see
PRD Phase 6). The numbers above are initial, measured on the dev stack;
they must be re-validated against production replica counts and real
usage projections before the production release.

## 11. Deployment Environments

### Development

Local Docker Compose.

### Staging

Cloud deployment with test database and test object storage.

### Production

Separate credentials, storage, database, monitoring, backups, and
deployment pipeline.

## 12. Design Principles

1.  Original evidence is immutable.
2.  AI suggestions are distinguishable from verified facts.
3.  Every warranty statement has provenance.
4.  Human correction is supported.
5.  Services are independently testable.
6.  External manufacturer data is treated as untrusted input.
7.  Claims automation is introduced only after reliable evidence
    validation.
