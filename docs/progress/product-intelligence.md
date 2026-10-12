# Phase Report — Product Intelligence (SRS ML-04 / ML-06 / ML-07, TSD §10/§12/§20/§28, SDD §4.5)

Date: 2026-08-31 · Status: **COMPLETE** — entities, normalization, staged
matcher, catalog API, per-document match record, user confirmation UI,
purchase linkage, seed catalog, and full test coverage (unit, API,
frontend, live E2E) all implemented and verified.

## 1. What was built

### 1.1 Entities (TSD §10)
`app/models/product.py` (migration `372a08d2314f`):
- `Manufacturer` (name, name_key, country, status), `Retailer` (same shape)
- `Product` (canonical_name, brand, category, model_number, sku,
  manufacturer_id FK, status)
- `ProductAlias` (product FK, alias_text, alias_type
  [RECEIPT_TEXT/SHORT/TRANSLATION/OTHER], source provenance, status)
- `RetailerCatalogEntry` (retailer FK, product FK, catalog_sku,
  catalog_model, catalog_name, source) — retailer-specific catalog rows
- `ProductMatch` — the per-document resolution record (see 1.4)

All entities enforce per-tenant visibility through the owning document/user;
catalog entities are global reference data.

### 1.2 Normalization (`app/ai/product/normalization.py`)
- `normalize_sku`: uppercase, strip separators/whitespace (brand-abbrev
  expansion intentionally NOT done — kept deterministic)
- `normalize_text` / `make_name_key`: lowercase, strip punctuation and
  filler tokens ("the", "of"), collapse whitespace
- `normalize_manufacturer`: title-case canonical brand forms
  (QTech Electronics, VoltHaus Appliances, FreshFoods, plus generic rules)
- `normalize_retailer_abbreviation`: RETAILER_ABBREVIATIONS map
  (nortech → Nortech Home Appliances, bluebox → BlueBox Electronics,
  citymart → CityMart Grocers) — retailer abbreviations resolve to full
  catalog names

### 1.3 Staged matcher (`app/ai/product/matching.py`)
Candidate generation + ranking in the TSD §28 order, each stage recorded in
`evidence`:
1. **EXACT_SKU** — normalized SKU equality (0.97)
2. **EXACT_MODEL** — normalized model equality with manufacturer constraint
   (0.98); wrong manufacturer ⇒ hard exclusion
3. **MANUFACTURER_MODEL** — manufacturer + partial model (0.85)
4. **CATALOG_MATCH** — retailer catalog row (catalog_sku/catalog_model)
   when the query carries a known retailer (0.9)
5. **ALIAS_MATCH** — exact normalized alias text (0.90)
6. **FUZZY_MATCH** — token-set + sequence similarity on names/models, with
   manufacturer constraint as a *prefix-tolerant* key match
   (`_mfr_match`, min length 2) so "QTech" constrains "QTech Electronics"
   but "Samsung" still excludes QTech products (0.55–0.95)
7. **SEMANTIC_MATCH** — embedding similarity (real when an embedding model
   is configured; honest "not configured" slot otherwise)

`decide()` applies the confidence policy:
- top candidate ≥ `confidence_auto_accept` (0.9) AND unambiguous
  (gap to 2nd > 0.05) → **AUTO_MATCHED**
- top ≥ `confidence_user_review` (0.7) OR any candidate ≥ 0.5 →
  **REVIEW_REQUIRED** with the full candidate list
- else **NO_MATCH**

A low-confidence candidate is **never** linked as the user's product
(`product_id` stays NULL unless AUTO_MATCHED at high confidence or the user
explicitly confirms).

### 1.4 Document resolution record (SRS ML-06 preservation contract)
`product_service.resolve_document_product` runs after field storage in the
pipeline (`app/ai/pipeline.py`) and stores, verbatim, on `ProductMatch`:
- original receipt values: `product_name_raw`, `sku_raw`, `model_raw`,
  `manufacturer_raw`, `retailer_raw`
- normalized values: `sku_normalized`, `model_normalized`
- the **full ranked candidate list** (JSON) with per-candidate method,
  confidence, engine + version, evidence
- `match_method`, `match_confidence`, `status`, `evidence` (including
  exclusion reasons, e.g. "manufacturer 'X' contradicts query 'Y'")
- `confirmed_by` / `confirmed_at` after a user decision

Document re-routing: `extraction_service._refresh_document_status` treats a
REVIEW_REQUIRED match the same as review-required fields — a pending product
match blocks COMPLETED. Audit metadata (AI_EXTRACTION_COMPLETED) carries
match status/method/confidence.

### 1.5 API (TSD §20)
- `GET/POST /api/v1/products/search|resolve|{id}` — ad-hoc catalog
  resolution; resolve returns the TSD §20 shape
  (`candidate_product`, `confidence`, `match_method`,
  `supporting_evidence`, `requires_confirmation`)
- `GET /api/v1/documents/{id}/product-match` — the match record
- `POST /api/v1/documents/{id}/product-match` — user decision:
  - `confirm` (optional `product_id`; omitted ⇒ top candidate) — preserves
    discovery method/confidence for the top candidate, MANUAL for a
    non-top pick; sets `confirmed_by/at`
  - `reject` — status REJECTED, product_id cleared, document re-routed
  - `create_confirm` — creates the product **from the receipt text**
    (canonical name = user-confirmed verbatim value), links it, marks
    CONFIRMED
- Cross-user access ⇒ 404 (document ownership check)

### 1.6 Purchase linkage
`purchase_service._apply_item`: when the purchase belongs to a document
whose match is CONFIRMED/AUTO_MATCHED, the match's `product_id`
**overrides** any client-supplied product_id — the confirmed identity wins.

### 1.7 Frontend (user confirmation UI)
`src/components/ProductMatchReview.tsx` (mounted in DocumentDetail above
field review):
- status chips (auto-matched / needs your review / confirmed / rejected /
  no match) + method + confidence
- the original received values (product / SKU / model / manufacturer)
  displayed as-is
- candidate list with per-candidate name, manufacturer/model/SKU,
  confidence %, method, evidence; radio selection
- actions: **Confirm selection** (top candidate pre-selected), **Reject
  all candidates**, **Create product from receipt text**; CONFIRMED state
  shows the linked product with a Change link; REJECTED state allows
  re-picking
- 404 (no match record — older documents) renders nothing

### 1.8 Catalog seed (real-data-only rule respected)
`backend/data/catalog/sample_catalog.json` — **10 products / 13 aliases /
6 retailer catalog entries / 3 manufacturers / 3 retailers**, all tagged
`_meta.source = "sample-fixture"` with an explicit note that they are
development fixtures mirroring retail formats, NOT scraped retailer data,
and must be replaced with sourced catalog exports before production.
`scripts/import_catalog.py` is the idempotent importer (upserts by
name_key / sku-or-name / (product, alias) / (retailer, catalog_sku));
verified: first run 3/3/10/13/6 rows, second run 0/0/0/0/0.

## 2. Bugs found & fixed during this phase
1. `match_out` lacked the `db` parameter for product expansion (service).
2. Stray `get_or_create_manufacturer` call in confirm path; stray
   `product.source_note` reference; dead helpers `db_get_product` /
   `_fields_needing_review` — removed.
3. Confirming the top candidate wrongly downgraded method/confidence to
   MANUAL/1.0 — now preserves discovery method/confidence; non-top picks
   are MANUAL/1.0.
4. Manufacturer full-key compare rejected "QTech" vs "QTech Electronics"
   (title-case fallback) — `_mfr_match` prefix tolerance in stage-2 filter
   and final constraint (found via live smoke; 16/16 unit tests still green
   after the fix).
5. `AuditAction` names — added `PRODUCT_REJECTED` alongside existing
   `PRODUCT_CONFIRMED`.
6. **Live E2E: retailer header set as manufacturer** — "NORTECH HOME
   APPLIANCES" made the extractor emit `manufacturer='Nortech'` (dictionary
   hit on the header line), and the matcher's manufacturer constraint then
   excluded the correct alias match → NO_MATCH on a receipt that obviously
   contains the product. Fix: the extractor's manufacturer scan skips the
   retailer header line (a brand token repeated on a product line is still
   honored — existing extraction test unchanged). Post-fix E2E 11/11.
7. Ruff: duplicate "aircon" in a synonym group (B033), unused loop var,
   import order; mypy: 29 errors across matcher/service/script/API — all
   fixed (`ruff check` clean, `mypy app/` clean, 85 files).

## 3. Verification
- **Backend suite: 200/200** (was 175; +16 matcher unit, +9 product API)
  - matcher unit (`tests/ai/test_product_matching.py`): exact SKU, exact
    model, manufacturer+model, alias, abbreviation, typo → REVIEW (never
    auto), ambiguous multi-candidate, no match, false-positive prevention
    (low confidence unique candidate not auto-linked), manufacturer
    mismatch exclusion
  - product API (`tests/api/test_products.py`): search/get + 401, TSD §20
    resolve shape, auto-match record shape (raw/normalized/candidates/
    evidence), low-confidence never linked + document REVIEW_REQUIRED,
    confirm preserves originals, reject, create-from-receipt, cross-user
    404, purchase picks confirmed product
- **Frontend: 17/17 vitest** (was 12; +5 product-match component tests:
  review rendering + original preservation, confirm top, reject→re-pick
  alternate, create-from-receipt, 404 hides section) + `tsc --noEmit`
  clean
- **Live E2E (`e2e_product.py`, real AI pipeline on the dev DB): 11/11**
  - clean alias receipt → `AUTO_MATCHED` ALIAS_MATCH 0.90, product linked
  - typo receipt ("Keyboad") → `REVIEW_REQUIRED`, 1 candidate, confirm via
    API → `CONFIRMED`
  - unknown product → `REVIEW_REQUIRED` → `create_confirm` → product
    created with verbatim receipt name, linked
  - cross-user product-match access → 404

## 4. Known limits / next
- Semantic stage is a real slot but "not configured" until an embedding
  model is wired (honest evidence string in candidates).
- Sample catalog is a dev fixture by design; production requires sourced
  catalog exports through `import_catalog.py` with real `source` values.
- Confidence thresholds remain PROVISIONAL (0.9 auto / 0.7 review) —
  calibrate from review decisions once user volume exists.
- `match_confidence` is NULL on REVIEW when no candidate cleared the
  review threshold (by design); the per-candidate confidences are always
  present in `candidates`.
