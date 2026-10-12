# Phase Report — Complete Notification System

Date: 2026-09-04 · Status: **COMPLETE** — reminder scheduling (60/30/7 days
before expiry), per-user preferences, read/unread history, retry with
exponential backoff, pluggable email provider, optional pluggable push
provider, and the background worker are implemented, unit/integration
tested, and verified **live against a running worker**. All verification
green.

## 1. What was built

### 1.1 Data model (migration `c9d1e5f8a2b3_0010_notification_delivery`)

- `notifications`: delivery columns added — `status` now includes
  `SUPPRESSED` (honest terminal state when no channel is enabled and
  configured), `attempts`, `max_attempts`, `last_error`, `next_retry_at`,
  `sent_at`, `email_sent_at`, `push_sent_at` (per-channel idempotency
  markers), `read_at`. `notification_key` unique-indexed.
- `notification_preferences` (new): per-user `email_enabled` (default
  ON), `push_enabled` (default OFF), `timezone` (IANA name).
- `push_outbox` (new): dev-transport mirror of `email_outbox` for the
  `log` push provider.

### 1.2 Scheduling (`backend/app/services/notification_service.py`)

- Default tiers **60 / 30 / 7 days** before each coverage / extended-
  warranty expiry (`warranty_reminder_days` setting).
- **Idempotency key (TSD §32)**:
  `purchase-{pid}:coverage-{cid}:{expiry_date_snapshot}:{tier}d`
  (or `extended-{eid}` for extended warranties). The expiry snapshot at
  scheduling time is part of the key: a user correction that shifts the
  purchase/expiry date creates a **distinct** key, so the new correct
  reminder is created and the stale SCHEDULED row is **CANCELLED** —
  never suppressed by accident.
- **Timezone handling**: a reminder for due date D is scheduled at
  09:00 (`reminder_local_hour`) on D **in the user's IANA timezone**
  (`zoneinfo`, DST-aware), stored as UTC. Invalid timezone ⇒ UTC fallback
  (logged). A moment already in the past is skipped unless it is the
  smallest tier, in which case the reminder is created immediately due
  so the user is still alerted inside the 7-day window.
- Triggered synchronously (best effort) at purchase create/update and by
  the worker sweep; stale-row cancellation runs on every sweep.

### 1.3 Worker sweep (`app/workers/run.py`, `NOTIFICATION_SWEEP_SECONDS`)

Each sweep: (1) schedule/cancel as above; (2) dispatch due rows
(`scheduled_at <= now` or `next_retry_at <= now`), per channel:

- **Email** via provider abstraction (`app/notifications/email.py`):
  `log` (default; writes `email_outbox`) or `smtp` (real send, configured
  via env).
- **Push** via optional provider abstraction (`app/notifications/push.py`):
  `none` (default) or `log` (writes `push_outbox`); `get_push_provider()`
  is a single seam where a FCM/APNs provider plugs in.
- **Retry**: `attempts` increments at each dispatch attempt (a row
  succeeds after 2 failures ⇒ attempts=3); on provider failure the row
  stays SCHEDULED with `next_retry_at = now + 5m·2^(attempts−1)`
  (`retry_base_seconds`) until `max_attempts` (default 3) ⇒ `FAILED` +
  `last_error`. Retries re-send **only channels without a sent marker**.
- **Suppression**: zero enabled-and-configured channels ⇒ `SUPPRESSED`
  (attempts stays 0, no outbox rows, not a failure).
- Logs a per-sweep summary: `{'created', 'cancelled_stale',
  'timelines_scanned', 'sent', 'failed', 'suppressed', 'retrying'}`.

### 1.4 API (`/api/v1/notifications`)

| Endpoint | Notes |
| --- | --- |
| `GET /notifications` | paged history; filters `unread`, `status`; `unread_count` always in payload |
| `PATCH /notifications/{id}/read` | mark one read |
| `POST /notifications/read-all` | mark all read |
| `GET /notifications/preferences` | toggles + timezone + **honest channel state** (provider name, `configured` flag, note — e.g. "log transport: recorded in email_outbox, no real delivery", "no push transport configured") |
| `PUT /notifications/preferences` | partial update (any of `email_enabled`, `push_enabled`, `timezone`) |

`GET /system/email-status` now also returns the push provider state.

### 1.5 Frontend

- `src/pages/Notifications.tsx`: history list with status pills
  (sent / scheduled / retrying / failed / suppressed / cancelled),
  retry + last-error meta lines, unread badge, mark-read / read-all.
  New **PreferencesCard**: email/push toggles (partial PUT, independent),
  honest per-channel configuration notes, and the reminder-timezone
  select (stored in preferences; used for 09:00 scheduling).
- `src/pages/Settings.tsx`: profile timezone select; Notifications card
  shows email **and** push provider status with a link to the
  notifications page.
- `src/types/notifications.ts`, `src/services/notifications.ts`: new
  fields (`attempts`, `last_error`, `next_retry_at`, `SUPPRESSED`,
  `ChannelStatus`) + preferences service.

## 2. Verification

### 2.1 Backend — `tests/api/test_notification_system.py` (13 tests)

60-day reminder, 30-day reminder, 7-day reminder, **duplicate prevention**
(idempotent key), **changed expiry date** (stale row CANCELLED, new key
row created), **timezone handling** (09:00 user-local ⇒ correct UTC;
invalid tz ⇒ UTC fallback), **worker retry** (flaky provider ⇒
attempts/next_retry_at/backoff, success after retries re-sends only the
unsent channel), **provider failure** (max attempts ⇒ FAILED +
last_error), **disabled notifications** (both channels off ⇒ SUPPRESSED,
attempts 0, no outbox), preferences roundtrip (defaults, partial update),
and read/unread/read-all.

- New suite: **13/13 passed** · full backend: **308/308 passed**
  (`pytest tests/ -q -p no:warnings`) · ruff + mypy clean on changed
  files.

### 2.2 Frontend

- New `tests/notifications.test.tsx` (2 tests: history + status pills;
  preference toggles issue the correct partial PUT bodies).
- Full vitest: **38/38 passed** (10 files) · `tsc --noEmit` clean.

### 2.3 Live worker run (required: "actually run the worker")

Worker restarted with the new code (`NOTIFICATION_SWEEP_SECONDS=5`,
`PUSH_PROVIDER=log`); live scenario against the dev API:

1. First sweep cancelled **12 stale** SCHEDULED rows left by earlier e2e
   runs whose timelines had since changed (stale-cancel path, live).
2. New user (tz Asia/Kolkata) + purchase with FULL_UNIT expiring in 5
   days → 7d reminder created immediately due.
3. Worker log: `email_logged to=notify-live-…@example.com
   subject='[WarrantyVault AI] Warranty expiring in 7 days: SPLIT AC
   1.5T'` followed by `notification_sweep {'sent': 1}`.
4. Second purchase (push enabled): row **SENT with both channels** —
   `email_outbox` row + `push_outbox` row (payload carries
   `notification_id`), `email_sent_at`/`push_sent_at` set, attempts=1.
5. Timezone proof: future COMPRESSOR reminders scheduled at
   **03:30:00 UTC = 09:00:00 Asia/Kolkata** (09:00 local hour rule).
6. API: `unread_count` 2 → 1 after `PATCH …/read`.

### 2.4 E2E regression (live API + worker)

- `e2e_alerts.py` **31/31** — scheduling tiers per coverage snapshot,
  worker delivery of the due 7d reminder, no duplicates across sweeps,
  CLAIM_READY notification + read/read-all. One assertion adjusted for
  the new correct behaviour: an immediately-due row may already be SENT
  by the time the "initially scheduled" check runs (nothing may be
  FAILED/CANCELLED/SUPPRESSED).
- `e2e_warranty.py` **46/46** · `e2e_product.py` **11/11** — no
  regressions.

## 3. Documents updated

- TSD §22 (Notification API): aligned with the implemented surface
  (unread filter, read-all, `/notifications/preferences` GET/PUT).
- TSD §32 / SDD §4.8: idempotency-key + stale-cancel rules (from the
  updated spec); SDD §4.8 now also documents timezone scheduling,
  provider abstraction, retry/backoff, and SUPPRESSED semantics.

## 4. Notes / known boundaries

- `smtp` and real push (FCM/APNs) are untested seams: both sit behind
  the same provider interfaces exercised by the `log` providers, with
  env-driven config.
- A row whose due moment passed before its first sweep is created
  immediately due (smallest tier only) — deliberate, so a purchase made
  inside the 7-day window still alerts once.
- Preferences live in `notification_preferences`; the account
  `timezone` remains the profile-level default used for display, while
  scheduling uses the preferences timezone when set.
