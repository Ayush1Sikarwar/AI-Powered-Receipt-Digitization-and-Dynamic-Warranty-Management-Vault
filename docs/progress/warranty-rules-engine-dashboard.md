# Phase Report — Warranty Rules Engine & Consumer Dashboard

Date: 2026-09-04 · Status: **COMPLETE** — rules engine computes
start/expiry/state from data only (never guesses), extended warranties
supported end-to-end, the TSD §42 traceability chain is user-reachable
with per-value provenance chips, and the consumer dashboard has the full
KPI + filter set. All verification green.

## 1. What was built

### 1.1 Rules engine (`backend/app/warranty/rules/timeline.py`)

Deterministic timeline computation per coverage/extension. States:
`NOT_STARTED / ACTIVE / EXPIRING / EXPIRED / UNKNOWN`.

- **Anchors**: `PURCHASE_DATE` and `INSTALLATION_DATE` start conditions,
  plus registration-aware coverages. Missing anchor ⇒ `UNKNOWN` timeline
  (start `None`), never a fabricated date.
- **Never-guess rule**: a finite duration unit with no recorded
  `duration_value` ⇒ `UNKNOWN` (no expiry invented); `UNLIMITED` ⇒
  `expiry None` with `ACTIVE`/`NOT_STARTED` state; zero/negative
  durations ⇒ `UNKNOWN`.
- **`compute_extended_timeline(...)`**: same never-guess semantics for
  extended-warranty records, keyed to the record's `start_condition`
  and anchored to purchase/installation dates.
- **Multiple simultaneous coverage periods**: one timeline row per
  (item, coverage) *and* per (item, extended warranty) — base and
  extended windows coexist (unique constraints `uq_timeline_item_coverage`
  / `uq_timeline_item_extended` + CHECK exactly-one origin).
- Unit coverage: `tests/warranty/test_timeline.py` — **30 tests**
  (incl. 8 new for the never-guess + extended paths).

### 1.2 Extended warranties (where the data supports them)

- **Model**: `PurchaseExtendedWarranty` (migration
  `d5a3c8e21b74_0009_extended_warranties`, applied to dev DB) — component,
  duration (or none ⇒ open-ended), `PURCHASE_DATE`/`INSTALLATION_DATE`
  start, `USER_DECLARED`/`EXTERNAL_OFFER` source + detail/notes,
  purchase-level or item-level scope.
- **API** (`/purchases/{id}/extended-warranties`): list / add / delete,
  ownership-enforced (404 for foreign purchases). Add/remove recompute
  the item's timelines **keeping the base coverage** (a recompute bug
  found in testing — the recompute wiped coverage timelines — is fixed
  and regression-tested).
- **Service** (`app/warranty/service.py`): `_recompute_item_timelines`
  merges coverage timelines + extended timelines; purchase updates
  (date/region/items/installation) re-sync timelines through the same
  path, so an installation-anchored extension flips from `UNKNOWN` to
  computed once the date is recorded.

### 1.3 TSD §42 traceability chain — `GET /purchases/{id}/trace`

`app/warranty/trace.py` builds the full chain the user can walk:

**Purchase → Original Receipt → Extracted Data → Product → Warranty →
Warranty Source → Expiry → Claim**

- Every node carries provenance: `AI_EXTRACTED`, `USER_CONFIRMED`,
  `VERIFIED`, `EXTERNAL_SOURCE`, `INFERRED`, plus `COMPUTED` for
  derived dates, with a `provenance_legend` (human explanations) in the
  payload.
- Label decisions: exact match methods (`EXACT_SKU`, `EXACT_MODEL`,
  `MANUFACTURER_MODEL`, `ALIAS_MATCH`) are `VERIFIED`; fuzzy/semantic
  and `REVIEW_REQUIRED` are `INFERRED`; pipeline fields are
  `AI_EXTRACTED` until confirmed; region provenance reflects the FR-11a
  resolution path (explicit / retailer / account / unresolved); all
  expiry dates are `COMPUTED`.
- Extracted fields keep `original_value` so corrections are auditable;
  receipts show sha256 + page count; warranty sources show URL,
  retrieval date, trust level; missing durations render as "no duration
  stated in the source — not guessed".

### 1.4 Consumer dashboard & purchases (frontend)

- **`Dashboard.tsx`**: KPI row = total purchases · active warranties ·
  expiring soon · expired · **incomplete/review-required**
  (`warranties.incomplete + warranties.unknown`, links into
  `?warranty_status=NEEDS_REVIEW`), plus documents-needing-review,
  documents, not-yet-started, and recent documents.
- **`Purchases.tsx`**: filter bar = product/SKU/model (text),
  manufacturer, purchase date from/to, warranty status (incl.
  `NEEDS_REVIEW`), all URL-driven (deep-linkable from dashboard tiles);
  new warranty-status column rendered with provenance-chip styling
  (UNKNOWN/INFERRED are never shown with verified styling).
- **`PurchaseDetail.tsx`**: the static `<ol>` placeholder is replaced by
  **`TracePanel`** — the 7-node chain with chips, receipt/field/product/
  source/expiry/claim details, and the legend; edit form now records
  installation/registration dates (the extension anchors).
- **`WarrantyPanel.tsx`**: mapping badges upgraded to provenance chips
  (AI matched = Inferred, exact = Verified, your confirmation = User
  Confirmed); extended-warranty section (list / add form / remove) with
  "extended" timeline tags; the required `Manufacturer Warranty Coverage`
  label + legal disclaimer kept verbatim.
- **`ProvenanceChip.tsx`**: one component, six visually distinct
  categories — inferred/AI values can never be mistaken for verified.
- Services/types: `getPurchaseTrace`, extended-warranty CRUD, and the
  matching TS types.

## 2. Verification

| Suite | Result |
|---|---|
| Backend unit+API (`pytest tests/`) | **295/295** (275 hardening baseline + 8 timeline + 12 extended/trace API) |
| Rules-engine unit (`tests/warranty/test_timeline.py`) | 30/30 |
| New API suite (`tests/api/test_extended_warranty_trace.py`) | 12/12 — add/no-duration/installation-anchor/UNLIMITED/delete/ownership ×2/trace chain/legend/inferred-never-verified/dashboard buckets/status filters |
| Frontend (`vitest`) | **36/36** (29 + 7 new: trace chain, extended add/remove, validation, filters, dashboard tiles) |
| **E2E against live dev API (`e2e_warranty.py`)** | **46/46** (27 + 19 new: trace chain + legend, 24mo extension w/ simultaneous timelines, unknown-duration, installation-anchor flip after date recorded, delete keeps base, NEEDS_REVIEW filters per user, dashboard incomplete bucket) |
| Lint / types | `ruff` clean · `mypy` clean (3 errors found & fixed: trace.py narrowing ×2, notification key for nullable coverage_id) · `tsc --noEmit` clean |

Notable bug caught by the new tests: `add/remove_extended_warranty`
recomputed item timelines with `mapping=None`, silently **deleting the
base coverage timelines** whenever an extension was added — fixed to
pass the live mapping, and covered by the "base timelines intact after
extension delete" e2e check.

## 3. Constraints honored

- **Never guess a duration** — finite-no-value ⇒ UNKNOWN; e2e asserts
  `expiry_date is None` + status `UNKNOWN` through the real API.
- **Provenance honesty** — chips + legend; INFERRED/AI_EXTRACTED use
  amber/purple styling, never green; the trace panel footer states
  explicitly that inferred values are not verified facts.
- **FR-11a** — region provenance shown per purchase (explicit →
  retailer → account, else UNKNOWN, never silently defaulted).
- **FR-12 / versioning** — failed refresh still leaves the previous
  valid policy mapping (e2e, unchanged).
- **UI label** — coverage panel remains "Manufacturer Warranty Coverage"
  with the non-legal-statement disclaimer.

## 4. Artifacts

- `backend/app/warranty/rules/timeline.py`,
  `backend/app/warranty/trace.py`,
  `backend/app/warranty/service.py`,
  `backend/app/api/v1/purchases.py`,
  `backend/app/schemas/warranty.py`,
  `backend/app/models/warranty.py`,
  `backend/app/services/purchase_service.py`,
  `backend/app/services/notification_service.py` (nullable coverage key)
- `backend/alembic/versions/d5a3c8e21b74_0009_extended_warranties.py`
- `backend/tests/warranty/test_timeline.py`,
  `backend/tests/api/test_extended_warranty_trace.py`
- `e2e_warranty.py` (46 checks)
- `frontend/src/components/{TracePanel,ProvenanceChip,WarrantyPanel}.tsx`,
  `frontend/src/pages/{Dashboard,Purchases,PurchaseDetail}.tsx`,
  `frontend/src/services/purchases.ts`, `frontend/src/types/purchases.ts`
- `frontend/tests/{trace-extended-warranty,dashboard-filters}.test.tsx`
  (+ fixture updates in `warranty-panel`, `field-review` tests)
