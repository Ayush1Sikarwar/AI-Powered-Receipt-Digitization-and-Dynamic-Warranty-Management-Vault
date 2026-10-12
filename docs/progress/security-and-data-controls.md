# Security & Data Controls (SRS FR-18/19/20, NFR-01/02) (complete)

**Status:** ✅ Complete & verified

Task: "Implement authentication and foundational security completely" — every
user-owned resource isolated, SRS data controls real, no TODO endpoints.

## Audit of what already existed (verified real, kept)

| Requirement | State before this work |
| --- | --- |
| Registration / login / logout | ✅ Real (Argon2id, timing-equalized unknown-user login, JWT access 15 min + hashed rotating refresh 30 d) |
| Refresh/revocation | ✅ Rotating refresh; reuse detection → whole-family revocation; logout revokes; password change/reset revokes all sessions |
| Password reset | ✅ Request (enumeration-safe, rate-limited) + confirm (single-use token, revokes all sessions) |
| /me + profile update | ✅ Real DB reads/writes |
| Authorization layer | ✅ `current_user` (role from DB, not token claim), `require_admin` |
| Resource ownership | ✅ Documents, fields, purchases (404 for foreign rows) |
| Rate limiting | ✅ Redis fixed-window (in-memory fallback): login 10/min, pwreset 5/h, uploads 20/h |
| Safe error responses | ✅ Standard envelope `error_code/message/details/request_id`; no stack traces; 500 = generic |
| Secure configuration | ✅ pydantic-settings, `.env.example` placeholders only |
| Audit logging | ✅ LOGIN/LOGOUT/REGISTER/PASSWORD_RESET/DOCUMENT_* /PURCHASE_* /ACCOUNT_DELETED |
| Account closure | ✅ `POST /account/close` (status + session revocation + audit) — but **purge was a promise, not code** |

## Implemented this phase (all real DB + storage operations)

### 1. SRS data controls

- **FR-19 Data export** — `GET /account/export`: portable JSON bundle of the
  user's complete personal data (profile, documents + extracted fields +
  classification + pipeline version, purchases + items, claims + evidence
  metadata, notifications, own audit trail ≤ 500 entries).
- **FR-20 Deletion** — document deletion now **schedules a deferred hard
  purge** (CLEANUP job, `RETENTION_DELETED_DOCUMENT_DAYS=7` grace, re-checks
  the open-claim hold at purge time).
- **NFR-02 Retention engine** — `app/services/data_retention.py`:
  - `purge_document`: hard-deletes document + pages + derivatives + fields +
    extraction jobs + storage objects (re-checks holds, idempotent).
  - `purge_user_data`: full closed-account cascade — storage objects first,
    then claims + evidence, purchases (+ items cascade warranty
    mappings/timelines), documents, notifications, refresh/reset tokens,
    outbox rows, user row. **Audit logs are deliberately retained** (≥ 365 d).
  - `retention_sweep`: periodic hook enqueues purges for closed accounts past
    their window with no open claims (idempotent).
  - **CLEANUP worker handler**: executes due jobs; on an open-claim hold it
    re-queues (+24 h backoff, attempt count not burned) instead of failing.
  - Job scheduling: `background_jobs.scheduled_at` + the worker only claims
    jobs that are due (migration 0007).
  - Closed account → purge due `RETENTION_CLOSED_ACCOUNT_DAYS=90` after
    closure; login on a closed account is rejected.

### 2. User-owned resource APIs with ownership checks

- **Notifications** (SRS FR-15): `GET /notifications` (page, `?unread=`,
  `unread_count`), `PATCH /notifications/{id}/read`,
  `POST /notifications/read-all`.
- **Claims** (SRS FR-16, TSD §23): `POST /claims` (purchase must be owned —
  404 otherwise; audit `CLAIM_CREATED`), `GET /claims` (status filter),
  `GET /claims/{id}` (incl. evidence list).
- **Evidence files** (new `claim_files` table, migration 0007):
  `POST /claims/{id}/evidence` (multipart; reuses upload validation —
  extension + **content-signature** checks, 15 MB cap; stored in object
  storage under `claims/`; sha256 recorded; audit `EVIDENCE_UPLOADED`),
  `GET /claims/{id}/evidence`, `GET /claims/{id}/evidence/{fid}/download`
  (authenticated, sha256 header).
- **Warranty records** (TSD §14/§15): `GET /purchases/{id}/warranty` —
  per-item resolved policy + coverages + computed timelines + source
  provenance (trust level); unmapped items are honestly reported as unmapped.
- **Products**: shared catalog (SDD) — no user-facing write endpoints exist
  (verified by test: POST/PUT/DELETE → 404/405); users "own" products only
  through their purchases, which are isolated.

### 3. Authorization & security test suite

- **`tests/security/test_authorization.py` (7 tests)** — User A vs User B:
  documents (7 verb/path combos incl. download/DELETE/fields-PATCH),
  purchases (incl. warranty endpoint + list isolation), notifications
  (list/read/read-all isolation), claims + evidence (list/detail/download/
  upload-on-foreign-claim/create-on-foreign-purchase), export (no cross-user
  leakage), products catalog not user-writable, unauthenticated 401s.
- **`tests/security/test_data_controls.py` (9 tests)** — export content +
  audit trail, deletion → deferred purge scheduling, purge hard-delete +
  storage + audit, hold re-queueing (attempt count preserved), terminal
  claim → purge proceeds, account close (session revocation + 90-d job +
  login rejection), full-account cascade (user gone, **audit retained**),
  open-claim hold on user purge, retention sweep (due/fresh/blocked +
  idempotent), scheduled jobs not claimable before due.
- **`tests/api/test_claims.py` (5)**, **`tests/api/test_notifications.py` (2)**
  — functional coverage incl. evidence content validation, oversized/
  executable rejection, sha256 integrity, ownership on download.
- Pre-existing `tests/security/` suites (token tampering, privilege
  escalation, plaintext check, upload security) kept green; one
  window-boundary flake in the login rate-limit test made deterministic.

## Files created

- `backend/alembic/versions/092fe14af9eb_claim_files_and_job_scheduling.py`
- `backend/app/services/data_retention.py`
- `backend/app/api/v1/notifications.py`
- `backend/app/api/v1/claims.py`
- `backend/app/api/v1/account.py`
- `backend/app/schemas/claim.py`
- `backend/tests/security/test_authorization.py`
- `backend/tests/security/test_data_controls.py`
- `backend/tests/api/test_claims.py`
- `backend/tests/api/test_notifications.py`

## Files modified

- `backend/app/core/config.py` — `retention_deleted_document_days`
- `.env.example` — `RETENTION_DELETED_DOCUMENT_DAYS`
- `backend/app/core/audit.py` — `ACCOUNT_PURGED`, `DOCUMENT_PURGED`, `EVIDENCE_UPLOADED` actions
- `backend/app/models/job.py` — `scheduled_at`
- `backend/app/models/claim.py` — `ClaimFile`
- `backend/app/workers/jobs.py` — `scheduled_at` param + due-only claiming
- `backend/app/workers/handlers.py` — CLEANUP handler with hold re-queueing
- `backend/app/workers/run.py` — retention sweep + reschedule guard
- `backend/app/services/document_service.py` — deletion schedules purge
- `backend/app/services/auth_service.py` — closure schedules purge
- `backend/app/api/v1/__init__.py` — new routers
- `backend/app/api/v1/purchases.py` — `GET /{id}/warranty`
- `backend/alembic/versions/{0001…0006}` — downgrades now drop their ENUM types (clean `downgrade base` cycle verified)
- `backend/tests/security/test_auth_security.py` — rate-limit test flake fix

## Verification

| Suite | Result |
| --- | --- |
| Backend pytest | **154 / 154 passed** (131 pre-phase + 23 new), verified on 2 consecutive runs |
| ruff / mypy | clean |
| Live E2E (API + worker) | full pass — see below |

Live E2E (two users, real API + worker):
- 11 cross-user accesses (documents/fields/purchases/warranty/claims/
  evidence/delete) → all 404, no mutation
- Export: owner bundle complete (docs+fields, purchases, claim+evidence),
  other user's bundle provably clean
- Deletion: HOLD while claim open → RESOLVED claim → DELETED → CLEANUP job
  scheduled 2026-09-07 (+7 d)
- `GET /purchases/{id}/warranty` → 200, honestly reports unmapped
- Account close → refresh 401, login 401, purge scheduled 2026-11-29 (+90 d)
- Purge handler executed for real: user + documents + purchases + claims +
  notifications gone from the DB, `ACCOUNT_PURGED` audit row written,
  audit history retained

## Notes & honest limitations

- Notification *generation* (expiry reminders, FR-15 scheduling) is a later
  feature; the API + model + idempotency keys are real and tested, with rows
  created by the (future) scheduler or directly.
- No frontend UI for claims/notifications/evidence/export yet — the API
  layer is complete and tested; the UI is a subsequent phase.
- `RETENTION_DELETED_DOCUMENT_DAYS` is new (SRS defines no grace period for
  user-deleted documents); default 7, configurable. The open-claim hold is
  enforced both at request time and re-checked at purge time.
- Warranty mapping itself (policy resolution from catalog/sources) is a later
  phase; the endpoint returns the real current state (unmapped until then).
