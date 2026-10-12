# Phase Report — Production Hardening (Roadmap Phase 6)

Date: 2026-09-04 · Status: **COMPLETE** — audited every hardening
deliverable; implemented the three real gaps (backup/restore,
monitoring, load testing), hardened error-envelope coverage, and
re-measured the model evaluation. Full regression green.

Roadmap Phase 6 deliverables: *security audit · rate limits · file
scanning · backup/restore · monitoring · error handling · model
evaluation · load testing.*

## 1. Audit of what already existed (verified real, kept)

| Deliverable | State found | Evidence |
|---|---|---|
| Security audit | ✅ Done in prior phase | `docs/progress/security-and-data-controls.md`: Argon2id auth, rotating refresh + reuse detection, ownership 404s, safe errors, account closure + deferred purge, FR-19 export / FR-20 deletion / NFR-02 retention engine |
| Rate limits | ✅ Real, Redis w/ memory fallback | `app/core/rate_limit.py`: login 10/min, password-reset 5/h, uploads 20/h; tested (`test_upload_rate_limit_enforced`, `test_auth_security.py`) |
| File scanning | ✅ Magic-bytes validation | `app/services/file_validation.py`: header never trusted; signature checked against declared MIME; 8 tests (`tests/security/test_uploads.py`): executables, zips, MIME spoofing, disguised executables, path-traversal filenames, oversized uploads |
| Error handling | ✅ Envelope + logging (extended this phase) | `main.py` handlers: 400/404/422/4xx/500 all return `{error_code, message, details, request_id}`; structured error log with request-id; `tests/api/test_vault_failures.py` proves 500 leaks no traceback/exception and leaves no partial state |
| Model evaluation | ✅ Runner + dataset + measured results | `ai_eval/run_evaluation.py` over 38 labeled samples (8 quality categories + multi-retailer/currency); CER/WER + field P/R/F1 + product top-k + false-match rate; honest caveats (synthetic dataset, thresholds PROVISIONAL below the 50/category PRD floor) |
| Health checks | ✅ Basic | `GET /health` (env/version/rate-limit backend), `GET /health/ready` (DB check, 503 on failure) |

**Gaps implemented this phase:** backup/restore (missing), monitoring
beyond `/health` (missing), load testing (missing), plus an
error-envelope audit suite.

## 2. What was built

### 2.1 Backup & restore (new) — `backend/scripts/backup.py`, `backend/scripts/restore.py`
- **Backup set** layout: `data/backups/backup_<ts>/` containing
  `database.dump` (pg_dump `-Fc` custom format), `storage.tar.gz`
  (object-storage tree), and `manifest.json` (db name, per-table counts,
  storage file count/bytes, sha256 of both files, creation time).
- Credentials via `PG*` env vars — the password never appears on the
  process command line.
- **Restore safety**: refuses to restore into a database that already
  has data (non-empty `users`) without explicit `--force`; post-restore
  verification that the schema actually materialized (missing tables ⇒
  hard failure with the pg_restore stderr tail); storage tar extracted
  only to an explicit target.
- `--keep N` retention prunes the oldest backup sets (name-validated
  `backup_\d{8}_\d{6}` dirs only; other dirs ignored).
- **Round-trip verified live**: dev DB (10 users / 14 purchases / 9
  policies / 14 timelines / 121 storage files) backed up, restored into
  a scratch DB — every table count matched the manifest exactly.

### 2.2 Monitoring (new) — `GET /api/v1/admin/observability` (admin only)
- `app/api/v1/admin.py::collect_observability`: database reachability +
  query latency, background-job queue depth (non-terminal), jobs by
  status and type, last 10 failed/dead jobs (type, error_code, error
  excerpt, time), storage usage (file count + bytes for the local
  backend), process uptime (`BOOT_TIME` marker added in `app/main.py`).
- Read-only, safe to poll; 403 for non-admins.
- Live response verified: db latency 0.56 ms, 6 COMPLETED jobs, 131
  storage files / 2.8 MB, uptime in seconds, request-id present.

### 2.3 Load testing (new) — `backend/scripts/load_test.py`
- asyncio + httpx; N concurrent workers, fixed duration; weighted mix
  (purchases list 28%, dashboard 18%, warranty browse 18%, product
  search 14%, item warranty 12%, login 10%); seed users + warm-up
  purchases per worker; overall + per-endpoint p50/p95/p99/max, RPS,
  error rate; JSON persisted under `backend/data/loadtest/`.
- **Measured (dev box, single uvicorn worker, 8 concurrency × 20 s):**
  1,929 requests · **96.1 RPS** · p50 58 ms · p95 175 ms · p99 222 ms ·
  **0% errors, 0 5xx**.
- Notes: `POST /auth/login` p99 1,039 ms is Argon2id hashing under CPU
  contention (expected, security trade-off — do not "fix" by weakening
  the KDF); `GET /warranty/policies` p95 208 ms is an unindexed browse
  that scales with policy registry size (fine at current scale; index
  when it grows).
- Result artifact: `backend/data/loadtest/loadtest_20260904_043851.json`.

### 2.4 Error-handling audit (new) — `tests/api/test_error_envelope.py`
Proves every failure class returns the standard envelope with
`request_id` and no traceback leak: 404 `NOT_FOUND`, 422
`VALIDATION_ERROR`, 403 `AUTHORIZATION_ERROR`, 401
`AUTHENTICATION_ERROR`, 429 `RATE_LIMITED` (drives the login limiter
until it trips).

### 2.5 Model evaluation re-run
`python -m ai_eval.run_evaluation` re-executed this phase (38 samples,
tesseract 5.5.0): mean CER 3.36%, WER 7.36%, field F1 **0.947**,
exact-all-fields 63.2%. Honest degradation: clear/faded/crumpled ≈
perfect; shadowed CER 15%, water-damaged exact 0 — thresholds remain
PROVISIONAL (27 labeled samples < 50/category PRD floor). Fresh
artifacts: `backend/ai_eval/RESULTS.json` / `RESULTS.md`.

### 2.6 Dependency scan
`pip-audit` over the venv: **no known vulnerabilities found** (only the
local project itself is skipped, as expected).

## 3. Verification

| Suite | Result |
|---|---|
| Backend `pytest tests` | **275/275** (was 263; +12: backup/restore 4, observability 3, error envelope 5) |
| `ruff check app tests scripts` | clean |
| `mypy app` | clean (94 files) |
| `pip-audit` | no known vulnerabilities |
| `ai_eval.run_evaluation` | re-run, measured results recorded |
| `e2e_warranty.py` (live) | **27/27** |
| `e2e_product.py` (live) | **11/11** |
| `e2e_alerts.py` (live) | **31/31** |
| Frontend vitest / tsc | 29/29 / clean (unchanged this phase) |

### Tests added
- `tests/unit/test_backup_restore.py` (4): complete backup set +
  manifest hash verification; **full restore round-trip into a scratch
  DB with per-table count comparison + storage object restoration**;
  non-empty-DB refusal without `--force` (and success with it);
  retention pruning (oldest removed, non-backup dirs ignored).
- `tests/api/test_admin_observability.py` (3): 403 for non-admin;
  payload shape; queue depth / by-status / by-type / recent-failures
  reflect seeded job rows.
- `tests/api/test_error_envelope.py` (5): envelope + request_id for
  404/422/403/401/429, no tracebacks.

## 4. Environment notes
- Granted `CREATEDB` to the dev `warranty` role (scratch restore DBs in
  tests).
- The `warranty` role and scratch DB pattern are dev-only; production
  restore would use a dedicated restore account.

## 5. Explicitly deferred (not Phase 6 scope)
- **Admin UI** (roadmap lists it under P1): the observability data is
  API-complete; a dashboard page can consume
  `GET /admin/observability` directly.
- S3-backed storage introspection in observability (local backend only).
- Load-test scaling beyond the single-worker dev box (production would
  validate with the intended deployment topology).
- Fixture warranty policy removal (pre-production data task, tracked in
  the warranty phase report).
