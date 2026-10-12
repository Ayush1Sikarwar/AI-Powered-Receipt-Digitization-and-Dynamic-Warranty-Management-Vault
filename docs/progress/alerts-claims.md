# Phase Report — Alerts & Claims (SRS FR-15 / FR-16, TSD §22/§23/§32/§35)

Date: 2026-08-31 · Status: **COMPLETE** — configurable 60/30/7-day expiry
reminder scheduling + worker-driven delivery, pre-filled claim package
generation (purchase + warranty + source + evidence), notifications UI,
claims UI, and full test coverage (unit/API, frontend, live E2E)
implemented and verified.

## 1. What was already in place (Phase 0–4)
- `Notification` model with the TSD §32 idempotency
  `notification_key` (purchase + coverage + expiry_snapshot + reminder_type)
- `GET /notifications` (list + `unread_count`), `PATCH /{id}/read`,
  `POST /read-all`
- Email provider abstraction (`LogEmailProvider` → `email_outbox` +
  structured log; `SmtpEmailProvider` when SMTP configured)
- `Claim`/`ClaimFile` models, claim CRUD + evidence upload/list/download
  through the object-storage layer (`claims/` category, TSD §35)
- `JobType.NOTIFICATION_SCHEDULING` / `CLAIM_PACKAGE_GENERATION` enum
  values; worker idle-sweep pattern (retention every 15 ticks)
- `AuditAction.NOTIFICATION_SENT` / `CLAIM_CREATED` /
  `CLAIM_PACKAGE_GENERATED`

## 2. What was built in this phase

### 2.1 Reminder scheduler (`app/services/notification_service.py`)
- **Configurable tiers** — `warranty_reminder_days` (default `60,30,7`),
  parsed + validated from settings/env.
- **Tier semantics (honest, no backfill spam)**: a tier's reminder moment
  is `expiry − tier` days. Only moments still in the future are scheduled;
  once the expiry has already entered the smallest window, that reminder is
  created immediately due so a late-added purchase still alerts. A 50-day
  expiry gets 30d + 7d reminders (the 60d moment passed); a 5-day expiry
  gets one 7d reminder, due now; a 14-month-old purchase gets nothing for
  its expired 12-mo coverage and three future reminders for its 60-mo
  compressor coverage.
- **Idempotency (TSD §32)**: key
  `purchase-{pid}:coverage-{cid}:{expiry}:{tier}d`; a changed expiry
  produces a distinct key and the stale `SCHEDULED` row is `CANCELLED`,
  never reused.
- **Delivery**: due `SCHEDULED` rows are sent through the email provider
  (log outbox in dev, `delivery="LOG"`), marked `SENT` + `sent_at` with a
  `NOTIFICATION_SENT` audit; failures are marked `FAILED` (never dropped).
- **Triggers**: best-effort scheduling right after purchase
  create/update warranty sync and after a warranty review decision
  (confirm/override), plus the worker sweep as the canonical path.

### 2.2 Worker sweep (`app/workers/run.py`)
`notification_sweep` (schedule → cancel stale → send due) runs in the
worker idle loop every `notification_sweep_seconds` (default 60s),
alongside the retention sweep. Sweep failures are logged and never kill
the worker.

### 2.3 Claim package generator (`app/services/claim_service.py`)
`POST /claims/{id}/package` builds the FR-16 pre-filled package:
- purchase (date, invoice, currency, total, retailer, warranty region +
  resolution method)
- item rows (name, SKU, model, serial, qty, line totals)
- warranty section per mapped item: full policy (name, version, region,
  status, coverages) + **source provenance** (type/title/URL/trust) +
  computed timelines (start, expiry, status) + match method/confidence +
  verification status
- source document (filename + sha256) when the purchase came from one
- uploaded evidence (filenames + sha256 + sizes)
- an honest disclaimer that computed windows are not a statement of legal
  rights

Generation sets `package_generated_at`, moves `DRAFT → READY`, writes a
`CLAIM_PACKAGE_GENERATED` audit, and creates an idempotent `CLAIM_READY`
notification (key `claim-READY-{id}`, referencing the new
`notifications.claim_id` column). Regeneration is allowed before terminal
status; terminal claims are rejected.

`GET /claims/{id}/package` returns the JSON (409 `CLAIM_ERROR` when not
yet generated); `GET /claims/{id}/package/download` streams it as an
attachment. The MVP does not submit the package anywhere — the user
downloads it (SRS MVP scope).

### 2.4 Schema (migration `9b41c7d20a35`)
`notifications.claim_id` (FK → claims, `ON DELETE SET NULL`, indexed) so
CLAIM_READY notifications deep-link to the claim. Notifications API now
returns `claim_id`, `expiry_date_snapshot`, `reminder_type`.

### 2.5 Frontend
- **Services/types**: `services/notifications.ts`, `services/claims.ts`
  (list/create/evidence-upload/generate/download), `types/*.ts`
- **Notifications page** — list with status dots, unread "N new" badge,
  per-row Mark read, Mark all read, deep links (warranty alerts →
  purchase, CLAIM_READY → claim)
- **Claims page** — create-from-purchase form (purchase picker, issue
  description/category, contact fields, client-side validation) + claims
  table (status chip, package state)
- **Claim detail page** — issue info, evidence list + upload (jpg/png/
  webp/pdf), Generate/Regenerate package button, JSON download, and a
  rendered package view (purchase, items, warranty policy + source +
  timelines table, document/evidence counts, disclaimer)
- Nav: Claims + Notifications links with a live unread-count badge;
  PurchaseDetail step 5 is now a real "create claim from this purchase"
  link; StatusChip gained claim statuses.

## 3. Bugs found & fixed during this phase
- **`app.models.retailer` doesn't exist** — Retailer lives in
  `app.models.product`; package generator 500'd on first run.
- **Purchase date is a `String(10)` ISO**, not a `date` — package builder
  called `.isoformat()` on it (mypy caught).
- **Tier backfill spam** (caught by tests): the first draft scheduled all
  tiers with `remaining <= tier`, so one late-added purchase produced 3
  notifications at once. Fixed to "future moments only + immediate
  delivery inside the smallest window".
- **Policy serializer duplication** — the warranty API had a private
  `_policy_out`; promoted to `app.warranty.service.policy_out` so the API
  and claim packages emit the identical policy/coverage/source shape.
- **Frontend test harness bugs** (not product bugs): fetch mock
  handler args were `(url, method)` where the handler expected
  `(method, url)`; `useParams` requires a matched `<Route>` in
  MemoryRouter; React select change events need the option to exist
  first; the 409 download mock needed `text()`-parseable error bodies.

## 4. Verification
- **Backend unit/API**: **236 passed** (was 229; +4 alert tests, +3 claim
  package tests) — tier boundaries, idempotency, stale cancellation,
  outbox delivery, expired-coverage exclusion, package content/audit/
  notification, 409-before-generation, cross-user 404s.
- **ruff + mypy**: clean (93 files).
- **Frontend**: **vitest 29/29** (was 20; +9 across
  `claims-notifications.test.tsx`) + `tsc --noEmit` clean.
- **Live E2E `e2e_alerts.py`: 31/31** against the running API + worker:
  - three backdated QTech AC purchases → correct per-coverage tiers
    (50d → 30d+7d; 5d → immediate 7d; expired FULL_UNIT → none, 60-mo
    COMPRESSOR → 3 future tiers); 12 reminders, all SCHEDULED
  - **worker sweep** delivered the due 7d reminder (SENT + outbox) within
    one sweep cycle; no duplicates across repeated sweeps
  - claim → evidence (real PNG, sha256) → package 409-before → generate →
    READY → package contains purchase/invoice/region, item,
    "QTech Sample AC Warranty (fixture)" policy with trust-2 source,
    timelines matching the computed windows, evidence sha256, disclaimer
  - JSON download (correct content-type + claim_uuid)
  - CLAIM_READY notification created + worker-delivered; mark-read +
    read-all clear the unread count; claims list + status filter
- **Regressions**: `e2e_warranty.py` **19/19**, `e2e_product.py` **11/11**
  live; full backend suite re-run green.

## 5. Known limits / next
- Email delivery is the dev log outbox (`delivery="LOG"`); SMTP is
  configured via env when needed (no behaviour change here).
- Tiers are global config (`warranty_reminder_days`), not per-user
  preferences (none specified in SRS).
- Package is generated on demand (no background `JobType` handler yet —
  the enum exists for a future async variant; the sync path is simpler
  and fully covered).
- Remaining roadmap: source refresh/cleanup phase + final hardening
  (auth hardening review, rate-limit review, docs pass).
