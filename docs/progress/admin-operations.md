# Phase Report — Administrative & Operational Functionality

Date: 2026-09-04 · Status: **COMPLETE** — the administrative and operational
functionality required by SRS/SDD/TSD is implemented (TSD §43/§44/§45):
admin-only management of manufacturers, retailers, the product catalog,
product aliases, warranty sources, and warranty policies (with versioning),
plus failed-processing review, source refresh status, audit logs, AI
evaluation results, and the model/pipeline registry. **Admin APIs have
separate authorization** (stored-role check, verified by 50 parameterized
authorization tests). **Every source tracks the full TSD §44 field set**
(identifier, type, owner, region, retrieval method, last successful
retrieval, last failure, content hash, version, status). **Model versions
are defined only in the model registry / config** — business logic imports
them, never hard-codes them. 386/386 backend tests, ruff + mypy clean,
live smoke-verified against the running API.

## 1. What was built

### 1.1 Admin API surface (`app/api/v1/admin.py`, mounted `/api/v1/admin/*`)

All routes use `Depends(require_admin)` — separate authorization that reads
the **stored** user role (`users.role == "ADMIN"`) from the database on each
request, not a JWT claim (a token minted before a role change is
re-validated; 403 for non-admins, 401 unauthenticated). Every mutating
action is audited.

| Area (TSD §43) | Endpoints |
| --- | --- |
| Manufacturer management | `GET/POST /manufacturers`, `PATCH /manufacturers/{id}` (duplicate `name_key` → 409; invalid status → 422) |
| Retailer management | `GET/POST /retailers`, `PATCH /retailers/{id}` |
| Product catalog management | `GET/POST /products` (list includes aliases), `PATCH /products/{id}` |
| Product alias management | `POST /products/{id}/aliases` (duplicate → 409), `DELETE /products/{id}/aliases/{alias_id}` |
| Warranty source management | `GET /warranty/sources` + `/{id}`, `POST`, `PATCH`, `DELETE` (409 while policies reference the source) |
| Warranty policy management | `GET /warranty/policies?include_archived`, `POST`, `PATCH /warranty/policies/{id}` |
| Policy versioning | PATCH content update → `upsert_policy_versioned` (new version row, predecessor archived, history preserved; version auto-increment when omitted; same-version changed specs → in-place correction; explicit older version → 409) |
| Failed processing review | `GET /failed-processing` (FAILED documents + FAILED/DEAD jobs + counts incl. REVIEW_REQUIRED), `POST /failed-processing/documents/{id}/retry` (requeue with fresh job + audit) |
| Source refresh status | `GET /warranty/sources` returns the full §44 set; `POST /warranty/sources/{id}/refresh` (existing, now also returns the full §44 state after the refresh) |
| Audit logs | `GET /audit-logs` (action/actor/resource filters, paginated) |
| AI evaluation results | `GET /ai-evaluation` (serves committed `backend/ai_eval/RESULTS.json` with mtime; honest `available:false` when absent) |
| Model/pipeline registry | `GET /model-registry` (all components × 5 identification fields + pipeline version) |
| Observability (existing) | `GET /observability` (db latency, queue depth, per-type counts, recent failures, storage, uptime) |

### 1.2 Source tracking — TSD §44 (`warranty_sources`, migration 0012)

New migration `7d3a9f1c5e02_0012_source_tracking` adds the four missing
operational columns; the other six fields pre-existed:

| §44 field | Column | Added in |
| --- | --- | --- |
| source identifier | `id` | pre-existing |
| source type | `source_type` | pre-existing |
| owner | `owner` (str 200) | **0012** |
| region | `region` | pre-existing |
| retrieval method | `retrieval_method` (str 40) | **0012** |
| last successful retrieval | `last_success_at` (timestamptz) | **0012** |
| last failure | `last_failure_at` (timestamptz) | **0012** |
| content hash | `document_hash` (sha256) | pre-existing |
| version | `source_version` | pre-existing |
| status | `retrieval_status` | pre-existing |

Behavior in `app/warranty/sources.py`:
- success → `last_success_at` set, `document_hash` updated to the sha256 of
  the retrieved content (so the next unchanged refresh is detected);
- failure → `FAILED` + `last_error` + `last_failure_at`; **never touches
  policies** (verified by test);
- unchanged content → no policy change, timestamps refreshed;
- changed + structured terms → new policy version via the versioned upsert
  (predecessor archived); changed without terms → `review_required`,
  current policy stays valid.

`source_out()` (used by the admin list/detail/refresh endpoints) returns
all ten fields (content hash exposed as both `document_hash` and
`content_hash`); `WarrantySourceCreate`/new `WarrantySourceUpdate` accept
`owner` + `retrieval_method`.

### 1.3 Model/pipeline registry — TSD §45, no hard-coded versions

`app/ai/registry.py` is now the **single source of truth** for model
identity; `registry()` returns all seven production components
(ocr_engine, document_classifier, image_quality_model, extraction_model,
preprocessing, **product_ranker** (newly registered — it was declared in the
module docstring but missing), embedding_model), each with model_name,
model_version, pipeline_version (from config, never hard-coded),
configuration_version, deployment_date.

Business logic now imports identity from the registry instead of
hard-coding:
- `app/ai/extraction/extractor.py` — `MODEL_NAME`/`MODEL_VERSION` from the
  registry (these are what get persisted on every `ExtractedField`);
- `app/ai/classification/classifier.py` — `Classification.method` from the
  registry;
- `app/ai/product/matching.py` + `app/services/product_service.py` — matcher
  engine name/version from the registry.

Verified: no model name/version literals remain anywhere in `app/` outside
`app/ai/registry.py` (grep audit). A version bump now happens in exactly one
place.

### 1.4 Supporting changes

- New `ConflictError` (`app/core/exceptions.py`, 409) for duplicate
  `name_key`s / referenced-source deletes / version conflicts.
- New audit actions: `MANUFACTURER_UPDATED`, `RETAILER_UPDATED`,
  `PRODUCT_UPDATED`, `PRODUCT_ALIAS_UPDATED`, `DOCUMENT_RETRY_REQUESTED`.
- New schemas: `app/schemas/catalog.py` (Manufacturer/Retailer/Product
  Create+Update, ProductAliasCreate); `WarrantySourceUpdate`,
  `WarrantyPolicyUpdate` in `app/schemas/warranty.py`.
- Policy PATCH semantics: `status` only → transition (no version fork);
  any content field → new version (auto-incremented when
  `policy_version` omitted); coverages carried over when the body omits
  them.

## 2. Verification

### 2.1 New tests (69)

**`tests/api/test_admin_management.py` (51)** — separate authorization + CRUD:
- `test_admin_route_requires_admin` — **25 admin routes × 403** for a
  non-admin (parametrized over every endpoint incl. all mutating ones);
- `test_admin_route_open_to_admin` — **25 routes** reachable by an admin
  (never 401/403);
- manufacturer / retailer / product + alias CRUD incl. 409 duplicates,
  404 unknown manufacturer, inactive filtering, 422 on invalid enum;
- warranty source CRUD with §44 fields + delete-409-while-referenced;
- policy versioning via PATCH (status transition forks nothing; content
  update creates v1.1 superseding v1.0; auto-increment v1.2; same-version
  correction stays in place);
- failed-processing listing + retry (new QUEUED job, error cleared,
  re-retry rejected);
- AI evaluation payload + model-registry completeness (7 components ×
  5 fields, extractor identity parity with the registry);
- audit-logs endpoint shows the admin's actions with actor id.

**`tests/api/test_source_versioning.py` (18)** — source versioning &
tracking (explicit task requirement):
- `source_out` exposes the **full §44 set**; content hash changes with
  content; version bump via structured terms preserves history (v1.0
  ARCHIVED / v1.1 ACTIVE, new coverages on the new version, old intact);
- failed refresh sets `last_failure_at` + FAILED + error, keeps
  `last_success_at`, hash, version, and **all policies ACTIVE/unchanged**;
  recovery refresh restores SUCCESS;
- refresh HTTP endpoint returns the full §44 state (stubbed fetch);
- model identity comes from the registry (extractor, product ranker,
  classifier).

### 2.2 Full backend suite

- `pytest tests/` → **386 passed** (317 prior + 69 new), 0 failures.
- Existing versioning tests (`test_warranty_versioning.py`, incl.
  "failed source refresh never touches policy" and
  "unchanged then versioned") still green.
- `ruff check app/ tests/` → clean; `mypy` on all touched modules → clean.
- One pre-existing test updated: `tests/ai/test_registry.py` now expects
  the `product_ranker` component (7 total).

### 2.3 Live smoke (running API :8000 + dev DB)

- Migration 0012 applied to the dev database.
- Fresh **non-admin** user → **403 on all 10 admin GET routes + 3 POST
  routes**; unauthenticated → 401.
- Admin: 200 on all read routes; `POST /manufacturers` → 201, duplicate →
  409, PATCH status → 200; `POST /warranty/sources` with
  `owner`/`retrieval_method` → 201 with full §44 output; delete of
  unreferenced source → 200; `GET /model-registry` → 7 components,
  consistent pipeline version; `GET /ai-evaluation` → committed RESULTS
  (overall + per_category); audit-logs show `MANUFACTURER_UPDATED` +
  `WARRANTY_SOURCE_UPDATED` for the admin's actor id.
- Test data created during the smoke run was removed from the dev DB.

## 3. Documents updated

- `docs/04_TSD_Technical_Specification.md` — §43 (admin API surface +
  separate-authorization mechanism + audit actions), §44 (all ten tracking
  fields persisted; refresh behavior; versioning on change), §45 (registry
  as single source of truth; business logic imports identity; 7
  components).

## 4. Notes / known boundaries

- **Admin dashboard UI**: PRD lists "Admin dashboard" as **P1 (Should
  Have)**, not P0/MVP; the TSD §43 contract is at the API level
  ("Administrative APIs must be separately authorized"). The admin API
  surface above fully satisfies the TSD contract; a React admin console is
  the natural P1 follow-up (all data is already served by these endpoints).
- `owner` / `retrieval_method` are admin-supplied metadata (string fields)
  — no separate owner entity exists in the MVP data model.
- `GET /ai-evaluation` serves the committed evaluation artifact
  (`backend/ai_eval/RESULTS.json`, 38 synthetic samples, floors unmet →
  thresholds flagged PROVISIONAL); re-running the evaluation refreshes it.
- The retry action re-queues with a **new** idempotency key
  (`docproc:{id}:retry:{ts}`) so repeated retries are each attempted;
  `max_attempts` still bounds per-job retries in the worker.
