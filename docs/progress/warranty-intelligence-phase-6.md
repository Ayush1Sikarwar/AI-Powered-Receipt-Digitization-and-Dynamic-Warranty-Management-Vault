# Phase Report — Warranty Intelligence, Hardened Subsystem (Phase 6)

Date: 2026-09-04 · Status: **COMPLETE** — source-registry retrieval
status + failure-safe refresh, anchor-aware warranty timelines
(purchase / installation / registration), 5-stage matching
(deterministic first, fuzzy + semantic as supporting evidence),
policy versioning with history preservation, region-resolution
priority enforcement, and the explicit **Manufacturer Warranty
Coverage** legal label in the UI — all implemented and verified
(unit, API, frontend, live E2E).

## 1. Requirements addressed (task spec)

| Requirement | Where it lives | Verified by |
|---|---|---|
| Source registry: URL/document, retrieval date, manufacturer, region, version, content hash, effective date, **retrieval status** | `warranty_sources` (migration `4c9e2b7a1f60`, adds `retrieval_status` + `last_error`), `GET /api/v1/warranty/sources` | `test_source_refresh_*` (API), E2E "source registry lists sources with retrieval status" |
| **Failed refresh never deletes the previous valid policy** | `app/warranty/sources.py::refresh_source` — failure ⇒ `FAILED` + `last_error` + audit only; success-with-spec ⇒ versioning; success-without-spec ⇒ `review_required`, policy stays valid | `test_failed_source_refresh_never_touches_policy`, live E2E (dead-URL refresh ⇒ FAILED, fresh purchase **still maps**) |
| Policy versions, effective dates, regions, model patterns, categories, exclusions, component coverage | `app/warranty/versioning.py::upsert_policy_versioned` — same version idempotent, newer version archives predecessor (`effective_to = new_from − 1d`, `ARCHIVED`, **never deleted**); `_version_key` parses "2.10" → (2, 10) | `test_policy_versioning_preserves_history`, `test_source_refresh_unchanged_then_versioned` |
| Coverage: parts, labor, compressor, battery, display, accessories, other components | `WarrantyCoverage.component` values incl. `PARTS`/`LABOR`/`MOTHERBOARD` (other) | `test_component_warranties_via_api` (6 components → 6 timelines), parametrized timeline tests |
| Region resolution exactly: explicit → retailer country → user account; store resolved region + method; insufficient ⇒ `UNKNOWN`, **no silent default** | `purchase_service._resolve_warranty_region` (existing) + new coverage | `test_region_resolution_priority` (all 4 tiers incl. UNRESOLVED), live E2E "retailer country fallback" (US via retailer id 1) |
| Mapping chain Purchase→Product→Manufacturer→Region→Policy→Coverages→Timeline; deterministic first, NLP/semantic as supporting evidence; every mapping has provenance | `app/warranty/rules/matching.py` — 5 stages: `EXACT_MODEL`/`EXACT_SKU`/`CATALOG_MATCH` (deterministic) + `FUZZY_MATCH` (rapidfuzz `token_set_ratio` ≥ 80, conf 0.60–0.70) + `SEMANTIC_MATCH` (Jaccard ≥ 0.5 on product vs policy text, conf 0.40–0.70); support stages only fire when deterministic stages didn't; evidence strings on the mapping | `test_fuzzy_match_near_miss_model`, `test_semantic_match_supporting_evidence`, `test_below_threshold_support_does_not_match`, `test_deterministic_rules_outrank_support_evidence`, `test_fuzzy_outranks_semantic` |
| UI explicitly labeled **Manufacturer Warranty Coverage**, not a complete statement of statutory/legal consumer-protection rights; no legal interpretation | `frontend/src/components/WarrantyPanel.tsx` heading renamed; footer disclaimer retained ("Coverage shown is manufacturer warranty information. It is not a complete statement of your legal consumer-protection rights, which may continue beyond these dates.") | vitest `warranty-panel.test.tsx` (heading + disclaimer asserted) |
| Historical versions preserved on policy change | versioning (above) + `scripts/import_warranties.py` rewritten on `upsert_policy_versioned` (re-import updates version/region/hash/trust, never resets retrieval status) | API tests (v1.0 ARCHIVED + `effective_to=2026-06-30`, v1.1 ACTIVE, pre-change purchase still mapped to v1.0, both versions browsable) |
| Required test areas: region selection / fallback / unknown, policy versioning, source failure, component warranties, multiple warranties, purchase-date start, installation-date start, registration-dependent coverage | see per-row verification | all green (below) |

## 2. What was built in this phase

### 2.1 Migration `4c9e2b7a1f60` (0008)
- `warranty_sources.retrieval_status` (enum, default `SUCCESS`) + `last_error`
- `purchase_items.installation_date` + `registration_date` (anchor dates)

### 2.2 Anchor-aware timelines — `app/warranty/rules/timeline.py`
- `compute_timeline(coverage, purchase_date, today, *, anchors)` where
  `anchors = {"PURCHASE_DATE": …, "INSTALLATION_DATE": …, "REGISTRATION_DATE": …}`
  (item rows carry them; service fetches the `PurchaseItem` per mapping).
- `INSTALLATION_DATE` start condition with no installation date ⇒ `UNKNOWN`
  (never guessed); `registration_required` coverage with no registration
  date ⇒ `UNKNOWN`; the clock starts from the recorded date.
- `app/warranty/service.py::_recompute_timelines` passes the anchors dict;
  a later `PATCH /purchases/{id}` (user records installation/registration)
  recomputes timelines in place.

### 2.3 Five-stage matching — `app/warranty/rules/matching.py`
- Deterministic: exact model → exact SKU → catalog/category.
- Supporting evidence only: **FUZZY_MATCH** (rapidfuzz
  `token_set_ratio` on `_strip_wildcards(pattern)` vs item model/product,
  threshold 80) and **SEMANTIC_MATCH** (Jaccard ≥ 0.5 between product and
  policy text, stopwords removed). Lower confidence bands + `evidence`
  strings, so low-confidence mappings surface as reviewable, never silent.

### 2.4 Versioning — `app/warranty/versioning.py` (new)
- `upsert_policy_versioned(db, *, manufacturer_id, policy_name, region,
  policy_version, effective_from, …, source_id, coverages)` →
  `{"policy", "created", "superseded"}`.
- Same version ⇒ idempotent no-op / in-place content correction.
- Newer version ⇒ predecessor gets `effective_to = new_from − 1d` (if unset)
  + `ARCHIVED`. Rows are never deleted; historical mappings keep pointing
  at the archived version with its original terms.

### 2.5 Failure-safe source refresh — `app/warranty/sources.py` (new)
- `fetch_source_content(url)` (httpx, 15 s, redirects, 2 MB cap).
- `refresh_source(db, source_id, *, fetch=fetch_source_content,
  policies=None, actor_id, request_id)`:
  - no URL ⇒ `SKIPPED`; fetch exception ⇒ `FAILED` + `last_error` + audit,
    **previous policy untouched** (`policies_unchanged: True`);
  - success ⇒ sha256; equal hash ⇒ `UNCHANGED` (retrieved_at/status only);
  - changed + structured `policies` spec ⇒ per-spec
    `upsert_policy_versioned`; changed without spec ⇒ `review_required`,
    existing policy stays valid.
- Endpoints: `GET /api/v1/warranty/sources` (any authed user) and
  `POST /api/v1/warranty/sources/{source_id}/refresh` (`require_admin`,
  optional body `{"policies": [spec]}`).

### 2.6 API surface + import script
- `PurchaseCreate`/`PurchaseUpdate` item schema: `installation_date`,
  `registration_date` (ISO, pattern-validated); `purchase_out` returns
  them per item; `purchase_service._apply_item` persists them
  (`_parse_iso_date`) on create **and** update.
- `scripts/import_warranties.py` rewritten onto
  `upsert_policy_versioned` + `document_hash`; idempotent re-run (0 new
  manufacturers/sources/policies on an up-to-date DB).

### 2.7 Frontend
- `WarrantyPanel.tsx`: heading **"Warranty coverage"** →
  **"Manufacturer Warranty Coverage"**; FR-11b footer disclaimer kept.

## 3. Verification

| Suite | Result |
|---|---|
| Backend `pytest tests` | **263/263 passed** (incl. 22 timeline, 16 matching, 10 new API versioning/refresh tests) |
| `ruff check app tests scripts` | clean |
| `mypy app` | clean (94 files) |
| Frontend `tsc --noEmit` | clean |
| Frontend `vitest run` | **29/29** across 7 files (panel heading + disclaimer asserted) |
| `e2e_warranty.py` (live) | **27/27** (was 19; +8: registry retrieval status, non-admin 403, dead-URL refresh ⇒ FAILED, policies unchanged, FAILED+last_error in registry, policy intact (still maps) after failure, retailer-country fallback US) |
| `e2e_product.py` (live) | **11/11** |
| `e2e_alerts.py` (live) | **31/31** |

Required-test-area coverage:
- **Region selection** — `test_region_resolution_priority` (explicit beats retailer beats account) + existing FR-11a tests.
- **Region fallback** — same test (RETAILER_COUNTRY) + live E2E (retailer id 1, US).
- **Unknown region** — same test, case 4 (UNRESOLVED, no silent default) + existing no-country user test.
- **Policy versioning** — `test_policy_versioning_preserves_history` (v1.0 ARCHIVED, window closed, v1.1 ACTIVE, pre/post-change purchases map to their own versions, both browsable) + `test_source_refresh_unchanged_then_versioned`.
- **Source failure** — `test_failed_source_refresh_never_touches_policy` + live E2E with a real connection-refused URL.
- **Component warranties** — `test_component_warranties_via_api` (PARTS/LABOR/BATTERY/DISPLAY/ACCESSORIES/MOTHERBOARD ⇒ one timeline each) + parametrized unit tests.
- **Multiple warranties** — `test_multiple_warranties_one_purchase` (one purchase, AC + TV ⇒ two distinct policies + timelines; warranty endpoint shows both).
- **Purchase-date start** — baseline timeline tests (start = purchase date).
- **Installation-date start** — timeline unit tests (unknown without date; computed from date) + `test_installation_date_start_via_api` (PATCH with `installation_date` recomputes; 2026-07-01 + 120 mo ⇒ 2036-07-01).
- **Registration-dependent coverage** — timeline unit tests + `test_registration_dependent_coverage_via_api` (UNKNOWN until registered; clock starts 2026-09-15 ⇒ expiry 2036-09-15).

## 4. Environment note
The sandbox reset had also wiped `poppler-utils` and `tesseract-ocr`
(same class as the Postgres/Redis wipe); both reinstalled. The
PDF-render/OCR tests failed until then — now green.

## 5. State
- Dev DB `warranty_vault` re-seeded: catalog (5 mfr / 3 retailer / 14 product)
  + 9 warranty policies / 17 coverages; real sources = Samsung India +
  LG India (OFFICIAL_WEB, trust 5); QTech/VoltHaus rows remain explicitly
  labeled fixtures (CURATED_DATASET, trust 2).
- Services: API :8000 (api-server-d5bdde93), AI worker, frontend dev :5173.
