# Phase Report — Warranty Intelligence (SRS FR-09/FR-10/FR-11/FR-12/FR-13/FR-14, TSD §13–§15)

Date: 2026-08-31 · Status: **COMPLETE** — rules engine, retrieval service,
coverage timelines, reviewable mappings, source registry (real official
data + labeled fixtures), purchase hooks, dashboard buckets, UI, and full
test coverage (unit, API, frontend, live E2E) implemented and verified.

## 1. What was already in place (Phase 0/1)
- Warranty domain models (TSD §13–§15): `WarrantySource`, `WarrantyPolicy`,
  `WarrantyCoverage`, `WarrantyMapping`, `WarrantyTimeline`
  (migration `a3f81c2d9b4e` applied)
- `GET /purchases/{id}/warranty` read endpoint with FR-12 source display
- FR-11a region resolution in purchases (explicit → retailer → user →
  UNRESOLVED, stored on the purchase)
- Dashboard `warranties` buckets + `warranty_status` search filter wired
  to a Phase-1 placeholder that returned UNKNOWN/0
- `app/warranty/` package scaffold (empty `retrieval/`, `rules/`, `sources/`)

## 2. What was built in this phase

### 2.1 Retrieval service (`app/warranty/retrieval/policies.py`)
Active-policy queries scoped by manufacturer (or generic), region
(exact or worldwide; unknown region ⇒ worldwide only) and effective
window (purchase date within `effective_from..effective_to`).
Source priority per SDD §5: OFFICIAL_API > OFFICIAL_WEB >
OFFICIAL_DOCUMENT > APPROVED_PARTNER > CURATED_DATASET > OTHER,
trust level as tiebreak.

### 2.2 Matching rules engine (`app/warranty/rules/matching.py`)
Per purchase item:
1. **EXACT_MODEL** — policy `product_model_pattern` (fnmatch, case-insensitive)
   vs item/canonical model — 0.95
2. **EXACT_SKU** — pattern vs SKU — 0.95
3. **CATALOG_MATCH** — manufacturer + product category — 0.85
Region precision: exact-region policy keeps base confidence; a worldwide
fallback is discounted (×0.95). No match ⇒ honest note trail
("no active policy in scope", "policies in scope but none matched
model/sku/category"). Manufacturer mismatch never matches.

### 2.3 Coverage timeline engine (`app/warranty/rules/timeline.py`)
- Start anchored to the manufacturer's start condition. Only
  `PURCHASE_DATE` is known from a receipt; REGISTRATION/INSTALLATION/
  ACTIVATION/MANUFACTURE-anchored coverages are honestly **UNKNOWN**
  (no silent guess — FR-11a spirit).
- Duration: DAY/WEEK/MONTH/YEAR (relativedelta, month-end clamped) or
  UNLIMITED (no expiry).
- Status: NOT_STARTED (today < start), EXPIRED (today > expiry),
  EXPIRING (≤60 days to expiry — the outer FR-15 band), else ACTIVE.

### 2.4 Service layer (`app/warranty/service.py`)
- `sync_purchase_warranties` — idempotent re-run over a purchase's items:
  resolves the policy, upserts the mapping, recomputes timelines
  (bulk-delete + insert to respect the unique constraint).
- **User-verified mappings (USER_CONFIRMED / USER_CORRECTED) are
  authoritative** — re-sync keeps them and only refreshes timelines.
- `review_mapping` — confirm / reject / override (override = user-picked
  policy, MANUAL method, USER_CORRECTED). All decisions audited
  (`WARRANTY_MAPPED`).
- Wired into purchase create/update (best-effort: a warranty failure is
  logged, never fails the purchase).

### 2.5 Real rules computation (`app/warranty/rules/coverage.py`)
Replaced the Phase-1 placeholder: per-purchase aggregate status is the
most conservative component status (EXPIRED > EXPIRING > ACTIVE >
NOT_STARTED > UNKNOWN); unresolved region or no mapping ⇒ UNKNOWN.
Dashboard buckets now include `incomplete` (region resolved but unmapped)
distinct from `unknown` (region unresolvable) — SRS FR-13.
`list_purchases` warranty filter now returns a correct `total` (the
status is computed, so it filters the full set and paginates in memory).

### 2.6 API
- `GET /warranty/policies?manufacturer=&region=&category=` — registry
  browse (SRS FR-09) with coverages + FR-12 source block
- `POST /warranty/policies/{purchase_id}/sync` — manual re-sync
- `POST /purchases/{purchase_id}/warranty/review` — confirm / reject /
  override (TSD §14: low-confidence mappings stay reviewable)
- Admin registry: `GET/POST /admin/warranty/sources`,
  `GET/POST /admin/warranty/policies` (nested source + coverages), all
  `require_admin` + audited (TSD §43 Phase-4 line)

### 2.7 Seed data (real-data-only rule respected)
`data/warranty/warranty_policies.json` + `scripts/import_warranties.py`
(idempotent; upserts by name key / (title,url) /
(manufacturer,policy_name,version,region) / (policy,component)):
- **Samsung India** (4 policies: TV, AC, Refrigerator, Washing Machine)
  — curated from the official warranty page
  (`samsung.com/in/support/warranty/`, retrieved 2026-08-31,
  OFFICIAL_WEB, trust 5): 12-month TV; AC 12 months + 60-month
  compressor + 120-month digital-inverter compressor (part warranty);
  etc.
- **LG India** (2 policies: AC, Washing Machine) — from official purchase
  terms clause 5.19 (`lg.com/in/terms-and-conditons-of-purchase/`,
  trust 5): 1-year product + 10-year inverter-compressor /
  direct-drive-motor warranty.
- **QTech / VoltHaus** (3 policies) — explicitly labeled
  "SAMPLE FIXTURE — development only" (fictitious brands, trust 2) so the
  dev catalog's E2E flow works without fabricating real-world terms.
- 4 real-brand products (Samsung/LG, names+categories only, no invented
  model numbers) added to the dev catalog to exercise retrieval.
Executed: 4 sources / 9 policies / 17 coverages; second run all zeros.

### 2.8 Frontend
- `WarrantyPanel.tsx` (mounted in PurchaseDetail): per-item mapped
  state, policy + version + region, timeline rows with status chips
  (Active/Expiring/Expired/Not started/Unknown) and dates, expandable
  coverage details, **FR-12 source block** (type, title, URL, trust
  level, retrieval date), review actions (Confirm / Reject / Undo),
  "Re-check policies" sync, unresolved-region warning, and the
  **FR-11b disclaimer** ("manufacturer warranty information… not a
  complete statement of your legal consumer-protection rights").
- Dashboard: "Warranties needing attention" (incomplete) card.
- Types/services for the warranty endpoints; defensive payload checks so
  stale mocks/404s render nothing.

## 3. Bugs found & fixed during this phase
1. **Timeline unique-constraint race** — ORM delete-then-insert of the
   same `(purchase_item, coverage)` pair in one flush inserted before the
   delete. Fixed with bulk deletes issued immediately
   (`_recompute_timelines`, `_drop_item_mapping`).
2. **`warranty_status` filter returned stale totals** (filter applied
   after SQL count/pagination). Now filters the full set, total reflects
   the filtered count, pagination in memory.
3. **Test-DB cross-phase pollution** — the Phase-0 warranty model unit
   tests committed fixtures without cleanup; the new engine's teardown
   hit RESTRICT FKs on those rows. Added a proper autouse teardown.
4. Ruff/mypy: 5 style + 9 type errors across the importer/API/tests —
   all fixed (reused-variable typing in the importer, unused imports,
   `datetime.UTC` alias, narrowing in `_sync_item`).

## 4. Verification
- **Backend suite: 229/229** (was 200; +29: 10 timeline unit,
  11 matching unit, 8 warranty API)
  - timeline: anchors, units (incl. leap-month clamping), UNLIMITED,
    60/61/0/1-day EXPIRING boundaries, UNKNOWN start conditions
  - matching: exact-model > category, exact-SKU, region preference,
    worldwide discount, region-specific exclusion, unknown-region →
    worldwide only, effective window, archived ignored, wrong
    manufacturer excluded, official-source tiebreak, no-match notes
  - API: auto-map + timeline + FR-12 source shape, FR-11a unknown
    bucket, confirm/reject/override + user-verified survives re-sync,
    override validation, cross-user 404, dashboard buckets +
    warranty_status filter, admin registry (incl. 403 non-admin),
    policy browse
- **Frontend: 20/20 vitest** (was 17; +3 warranty panel tests) +
  `tsc --noEmit` clean
- **Live E2E (`e2e_warranty.py`): 19/19** on the running dev stack —
  QTech AC → CATALOG_MATCH 0.85, timelines 2026-08-30→2027-08-30
  (FULL_UNIT) and →2031-08-30 (COMPRESSOR); Samsung TV → real
  "Samsung India TV & AV Standard Warranty" with official URL + trust 5;
  confirm/reject decisions persist; unresolved region → unmapped →
  unknown bucket; dashboard + warranty_status search; policy browse
- **Phase-3 regression (`e2e_product.py`): 11/11** still green
- `ruff check` + `mypy` clean

## 5. Known limits / next (Phase 5)
- Registration/installation-anchored coverages stay UNKNOWN until
  registration data is captured (by design; user can still see the
  policy and the UI offers re-check).
- 60/30/7-day expiration **alerts** (SRS FR-15) are next: the
  notification scheduler scans `warranty_timelines` by `expiry_date`
  (indexed) — the timeline rows and statuses it needs now exist.
- Claim package generation (SRS FR-16) is next: claims API + models
  already exist; the package will pull from purchase + warranty
  timeline + document evidence.
- Fixture warranty policies (QTech/VoltHaus) must be removed before
  production; real policies are the only trustworthy source rows.
