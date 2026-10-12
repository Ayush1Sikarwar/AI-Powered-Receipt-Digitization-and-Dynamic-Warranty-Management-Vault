# WarrantyVault AI — Full Repository Audit: Final Report

**Date:** 2026-09-04 · **Mandate:** stop adding features; inspect the entire repository (never assume complete because a file exists); trace the 21-step workflow; verify 10 layers per feature; verify 8 attributes per AI feature; security-test (IDOR, cross-user access, malicious/MIME-spoofed/oversized uploads, injection, rate limits, tokens/sessions, secret exposure, privilege escalation); **measure** AI metrics (never invent); load-test before scalability claims; fix CRITICAL/HIGH; rerun all tests; produce requirements traceability; do not declare production readiness while required tests fail.

Companion document: `docs/audit/requirements-traceability.md` (full PASS/PARTIAL/FAIL/N-A matrix, 126 rows).

---

## 1. Verdict (stated up front)

**The system is NOT declared production-ready.** All required tests pass, and every CRITICAL/HIGH finding from this audit is fixed and live-verified. The blockers are bounded and are listed in §7: the PRD §5 dataset floor (50 samples/quality category) is unmet, so confidence thresholds remain PROVISIONAL by the specs' own rule (TSD §59 "confidence thresholds calibrated" therefore cannot be checked); the dev environment runs on the default `AUTH_SECRET` (guarded: production mode hard-fails, dev logs a startup warning); no TLS/at-rest encryption, no live email provider, and no WCAG audit exist in this deployment. These are environment/data-scale gaps, not failing tests — but the audit mandate forbids a readiness claim while they stand.

> **Post-audit update (same day):** the production deployment mandate was
> then implemented and verified on a live production-configured stack
> (42/42-check readiness suite, exit 0). It closed remaining items 2, 3 and
> most of 6 above (production secrets, TLS at the edge, deployment ops +
> metrics + trusted proxy), plus findings F4 and F10. The dataset floor
> (item 1), the F13 login-latency decision (item 5), live email delivery
> (item 4), and the WCAG audit remain open. See §9A.

## 2. Method

1. Re-read all four spec documents (`docs/01..04`).
2. Read every backend module (`app/api/v1/*`, `app/services/*`, `app/ai/*`, `app/warranty/*`, `app/workers`, `app/models/*`, `app/core/*`, `app/main.py`, config, migrations 0001–0012) and every frontend page/component/service/test. No file was taken on faith.
3. Traced the full workflow live (below).
4. Executed security, AI-measurement, and performance probes against the running stack (Postgres 127.0.0.1:5432, Redis 6379, API :8000, worker, vite :5173).
5. Fixed all CRITICAL/HIGH findings; added regression tests for each fix.
6. Re-ran every test suite (backend, frontend, e2e).

## 3. 21-step workflow trace (live, 2026-09-04)

| # | Step | Status | Verified by |
|---|---|---|---|
| 1 | User register | ✓ | e2e + live probe (issues a 15-min access token) |
| 2 | Login | ✓ | e2e; Argon2 verification; RL 10/min |
| 3 | Upload receipt | ✓ | 201 in 0.03 s; streaming 15 MB cap (20 MB → 400 in 0.1 s) |
| 4 | Secure storage | ✓ | UUID keys under category dirs; sha256 pinned; originals immutable |
| 5 | Processing job | ✓ | Redis queue → worker; retry ×3 → terminal FAILED/DEAD |
| 6 | Classification | ✓ | rule-based classifier (registry 1.0.0) |
| 7 | Image enhancement | ✓ | OpenCV deterministic pipeline; derivative stored |
| 8 | OCR | ✓ | tesseract 5.x; per-line confidence + engine version stored |
| 9 | Extraction | ✓ | schema-constrained; 11 field types |
| 10 | Confidence | ✓ | per-field confidence + method + page; 0.90/0.70 PROVISIONAL bands |
| 11 | Human correction | ✓ | field-review UI; original_value preserved; original untouched |
| 12 | Product matching | ✓ | 5-stage matcher; auto-accept requires gap to runner-up; measured §5 |
| 13 | Region resolution | ✓ | explicit → retailer → account, else UNKNOWN (stored + auditable) |
| 14 | Warranty retrieval | ✓ | source registry (trust/region/hash/version); effective-window scoping |
| 15 | Warranty mapping | ✓ | rules-first engine; component coverages; measured §5 |
| 16 | Rules engine (timelines) | ✓ | anchor conditions; honest UNKNOWN; 120-month case verified |
| 17 | Dashboard | ✓ | buckets incl. incomplete; pagination |
| 18 | Notification | ✓ | 60/30/7; idempotency key with expiry snapshot; e2e 31/31 |
| 19 | Claim | ✓ | stops at CLAIM_READY (§57); provenance per item |
| 20 | Claim package | ✓ | ZIP: package.json + claim-report.md + evidence/ + documents/ |
| 21 | Export/Delete | ✓ | export ZIP incl. stored receipt; retention-aware delete; backup+restore verified |

Every step also verified across the 10 required layers (frontend, API, DB, business logic, validation, authorization, error handling, logging, tests, documentation) — see the traceability matrix.

## 4. Security audit (executed, not assumed)

| Test class | Result |
|---|---|
| IDOR / cross-user document·purchase·claim·notification access | **25/25 PASS** (cross-user reads 404, writes 403/404) |
| Privilege escalation (role via PATCH /auth/me; forged HS256 role=ADMIN; access-token-as-refresh) | **7/7 PASS** (role read from DB per request; forged token 403/401) |
| Token/session (tampered, expired, wrong secret, wrong type) | all rejected (401/403) |
| SQL injection (5 probe payloads through search/params) | clean — no injection, no error leakage |
| XSS surface (dangerouslySetInnerHTML) | none in codebase |
| CORS | no arbitrary origin reflection |
| Malicious uploads (image bombs) | 256 MP and 2025 MP hand-crafted PNGs (≈70-byte files) → FAILED in 2.0 s each, `DecompressionBombError` / 40 MP decode cap; pre-fix they would have decoded 768 MB–6 GB |
| MIME spoofing | signature (magic-byte) check rejects mismatched content |
| Oversized upload | 20 MB → 400 FILE_ERROR in 0.1 s (streaming cap, no full buffering) |
| Oversized PDF (25 pages vs 20 cap) | pre-render pypdf check → FAILED in 1.0 s (pre-fix: ~2 min render × 3 attempts, document stuck in PROCESSING) |
| Out-of-range integer IDs | `…/999999999999` and `/documents/-5` → 404 (pre-fix: 500) |
| Rate limiting | login 10/min, pw-reset 5/h, upload 20/h, **global API 300/min/IP (new)**: live burst 360 → 292×200 + 68×429, Retry-After present |
| Secret exposure | no secrets in repo; no `.env` committed; default-secret guard: dev → `SECURITY_WARNING` at boot (observed), production → RuntimeError (regression-tested) |
| Admin authorization | 26/26 routes `require_admin`; non-admin 403 (separate from user auth) |
| Stack/trace leakage | storage + DB outage tests return safe envelopes, no partial state |

## 5. AI audit — measured, not invented

All metrics below were produced by running the **unmodified production code** on defined datasets in a dedicated `warranty_vault_eval` database (scripts in `backend/ai_eval/`, results as JSON+MD next to them). Datasets are deterministic and synthetic; that caveat is stated in every results file.

| Feature | Model (registry identity) | Measured | Artifact |
|---|---|---|---|
| OCR | tesseract 5.x | **CER 3.36 %, WER 7.36 %** (38 samples, 8 quality categories; worst: water_damaged EM 0.0, shadowed EM 0.2) | `ai_eval/RESULTS.json` |
| Extraction | rule-based-schema-constrained 1.0.0 | **P 95.71 %, R 93.71 %, F1 94.7 %, EM 63.2 %** | `ai_eval/RESULTS.json` |
| Product matching | rule-based-product-matcher 1.0.0 | **Top-1 85.59 %, Top-3 89.19 %, auto-match 63.96 %, false-match rate 0.0 (0/117 auto-decisions), negative-line correct rejection 100 %** (117 positive variants + 6 grocery negatives over a 20-product/5-manufacturer catalog) | `ai_eval/PRODUCT_MATCHING_RESULTS.json` (new this audit) |
| Warranty mapping | rules engine + timeline (deterministic) | **policy correctness 100 %, duration correctness 100 % (incl. honest UNKNOWN), source validity 100 %** (7-case set: region priority, versioning v1-archived vs v2-active, unknown-region no-fabrication, registration UNKNOWN vs anchored, battery coverage, out-of-window no-match) | `ai_eval/WARRANTY_MAPPING_RESULTS.json` (new this audit) |

Per-feature 8-attribute check (model/provider, dataset, metrics, confidence, version, provenance, human review, hallucination protection): satisfied for all four features; confidence thresholds are **PROVISIONAL** because the PRD §5 floor (50/category) is unmet — flagged in the model registry exactly as the spec requires.

## 6. Performance audit (TSD §48 load-test mandate — executed)

Load test: `scripts/load_test.py`, 8 concurrent workers, 20.2 s, **841 requests, 41.6 RPS, 0.0 % errors, 0 × 5xx** (global API RL temporarily raised for the window — its correctness was separately proven by the 360-burst; result JSON in `backend/data/loadtest/`).

| Endpoint | n | p50 | p95 | max |
|---|---|---|---|---|
| GET /purchases | 235 | 93.4 ms | 135.9 ms | 202.6 ms |
| GET /dashboard/summary | 172 | 86.6 ms | 128.7 ms | 197.4 ms |
| GET /warranty/policies | 150 | 162.9 ms | 222.4 ms | 263.9 ms |
| GET /products/search | 99 | 58.2 ms | 106.3 ms | 129.4 ms |
| GET /purchases/{id}/warranty | 95 | 56.6 ms | 85.6 ms | 153.4 ms |
| POST /auth/login | 90 | 629.9 ms | **793.7 ms** | 887.5 ms |

- **Non-AI API p95 < 500 ms: PASS** on every non-auth endpoint (85–222 ms).
- **Login p95 793.7 ms > 500 ms: documented deviation** — Argon2id at t=3/m=64MiB/p=4 is the configured cost; not "fixed" by weakening authentication.
- **Receipt processing < 10 s: PASS** — measured 5.1 s end-to-end for a 1-page receipt (upload → REVIEW_REQUIRED); queue depth 0 at this volume; 25-page over-cap PDF rejected in 1.0 s.
- Worker independent from API (separate process, Redis queue) — NFR-05 architecture verified; multi-replica scaling not demonstrated in this sandbox.

## 7. Findings register

| ID | Severity | Finding | Disposition |
|---|---|---|---|
| F1 | **HIGH** | Upload size enforced only after full body buffering (a 15 MB+ body was fully read into memory before rejection) | **FIXED** — `_read_upload_capped` streams 1 MB chunks, rejects mid-stream at the cap · live-verified 20 MB → 400 in 0.1 s · regression test |
| F2 | **HIGH** | Image decompression bombs: a 70-byte PNG declaring 2025 MP would decode ≈6 GB (DoS) | **FIXED** — 40 MP declared-dimension cap checked before `img.load()` + PIL native guard · live-verified 256/2025 MP → FAILED in 2.0 s · regression tests |
| F3 | **HIGH** | PDF page cap checked after rendering: 25-page PDF rasterized ~2 min, retried 3×, document stuck in PROCESSING | **FIXED** — pre-render pypdf page check (1.0 s rejection) + `ProcessingError` now forces terminal FAILED on job **and** document before re-raise · live-verified · regression tests |
| F4 | LOW | `/api/docs` + `/api/openapi.json` exposed in all environments | Reported (dev convenience kept; prod proxy should strip — deployment note) |
| F5 | NOTE | Rate-limit backend is in-memory per process (Redis used when available) | Reported — multi-replica deploys must use shared Redis (already the configured path) |
| F6 | **HIGH** | No enforcement that production cannot boot with the default/short JWT secret | **FIXED** — `_enforce_secret_policy` in `create_app`: production + weak secret → RuntimeError; dev → startup warning · live warning observed · 3 regression tests |
| F7 | **HIGH** | Global per-IP API rate limit (SRS Security: "protect … API endpoints") existed in config but was **not wired** | **FIXED** — middleware applies `rl_api_per_min=300` to all `/api/*` (except health) with 429 envelope + Retry-After · live-verified (360-burst) · regression test |
| F8 | MEDIUM | Out-of-range integer IDs (999999999999, −5) → 500 (SQLAlchemy DataError wrapping NumericValueOutOfRange) | **FIXED** (promoted during verification because it was a 500, not a 4xx) — handler on `sqlalchemy.exc.DataError` inspecting `exc.orig` → 404 NOT_FOUND · live-verified on documents/purchases/claims · regression tests |
| F9 | **HIGH** | Document 25 stuck in PROCESSING (deterministic failure never reached terminal state) | **FIXED** — DB repaired; root cause (F3/F7-class retry without terminal state) fixed in code · regression test |
| F10 | MEDIUM | RL keyed on socket IP only; no X-Forwarded-For handling (collapses per-user limits behind a proxy) | **Reported** — deployment must set a trusted-proxy-aware client IP (proxy config note) |
| F11 | LOW | `PATCH /auth/me` accepts and silently ignores unknown fields | Reported |
| F12 | **HIGH** | (found this audit, during test re-run) `TracePanel.tsx:252` render crash — `Cannot read properties of undefined (reading 'length')` on any trace payload missing arrays (older API shape, proxy truncation, partial mocks): the entire purchase-detail page white-screens | **FIXED** — defensive normalization of all trace arrays/legend at panel entry · live-verified in the previously-crashing test · regression test added |
| F13 | MEDIUM | Login p95 793.7 ms > TSD §48 500 ms target under 8-concurrency load | **Reported** — Argon2 cost trade-off; options (lower t/m, session-cached verify, or raised target with justification) left to product/security decision |

**CRITICAL:** none found. **HIGH:** 7 found (F1, F2, F3, F6, F7, F9, F12) — **all fixed, live-verified, regression-tested.** Plus F8 (MEDIUM, promoted to fixed because it returned 500, not 4xx). Remaining MEDIUM/LOW (F4, F10, F11, F13, F5-note) reported, not fixed, per the audit mandate.

## 8. Test totals after all fixes

| Suite | Result |
|---|---|
| Backend (unit + API + security, incl. 9 new regression tests) | **395 / 395 passed** (95.9 s) |
| Frontend (11 files, incl. 1 new TracePanel regression test) | **42 / 42 passed**; `tsc --noEmit` clean |
| Live e2e (repo root, 4 suites) | **133 / 133 passed** (alerts 31, product 11, warranty 46, claims 45) |
| Ruff / mypy on changed code | clean |
| Security probe suites | IDOR 25/25 · privilege-escalation 7/7 · SQLi 5/5 clean · token/session 7/7 |
| Backup/restore | backup script run live (pg_dump -Fc + storage tar + manifest); restore into scratch DB verified (0 errors; 80 users / 36 documents) |

## 9. Remaining items (what would need to happen for production readiness)

1. **Dataset floor (PRD §5):** collect/curate ≥ 50 labeled samples per quality category, re-run `ai_eval`, then re-calibrate and un-flag the PROVISIONAL confidence thresholds (TSD §59 AI bullet).
2. **Secrets:** set a long random `AUTH_SECRET` (and all other secrets) in the target environment — production mode already hard-fails without one.
3. **Transport/storage encryption:** terminate TLS at the reverse proxy (deployment playbook documents it) and add at-rest encryption per NFR-01.
4. **Email delivery:** configure a real SMTP provider (scheduling/idempotency/retry already verified with the log provider).
5. **Login latency decision:** accept the measured 793 ms p95 (Argon2 cost) with a documented TSD amendment, or tune cost parameters — a product/security decision (F13).
6. **Deployment ops:** run the documented staging/prod playbooks (TLS proxy, worker replicas, Redis-backed RL, trusted-proxy client IP — F10), stand up a metrics pipeline (NFR-07), and perform a WCAG audit (NFR-08).
7. **ML-03 note (honest scope):** extraction is a deterministic rule-based layer, not a learned model/LLM — stated as PARTIAL in the traceability matrix; a model-based extraction layer is an addition, not an audit fix.

## 9A. Post-audit update — production deployment phase (2026-09-04)

The production deployment mandate (TSD §50/§51/§52; "do not add unrelated
functionality") was then implemented and verified on a live
production-configured stack. Disposition of §9's remaining items:

| §9 item | Status after the phase |
|---|---|
| 1. Dataset floor (PRD §5) | **Open** — unchanged; data-scale item (thresholds stay PROVISIONAL). |
| 2. Secrets | **Closed** — production env uses a unique generated `AUTH_SECRET` + DB/Redis/S3/SMTP credentials; production boot **hard-fails** on a weak/default secret (verified live); no committed secrets (readiness-suite scan); dev credentials never used in production (env isolation verified). |
| 3. Transport/storage encryption | **Closed** — TLS enforced at the production edge (nginx; **TLSv1.3 verified live**, HTTP→HTTPS 301); production object storage is S3 (server-side encryption, versioning recommended — runbook). |
| 4. Email delivery | **Open** — production template configures a real SMTP provider, but live delivery cannot be exercised without a provider (none in sandbox). Scheduling/idempotency/retry already verified. |
| 5. Login latency (F13) | **Open** — unchanged; product/security decision item. |
| 6. Deployment ops | **Mostly closed** — production stack implemented and **run live**: TLS proxy, Redis-backed rate limiting with trusted-proxy client IP (**F10 closed**), Prometheus `/metrics` + scrape config + alert rules (**NFR-07 closed**), CI/CD (staging auto / production manual / rollback), backup + verified restore, runbook. **WCAG audit (NFR-08) remains open.** |
| 7. ML-03 | **Unchanged** — by design, rule-based extraction layer (PARTIAL). |

**Findings closed in this phase:** **F4** (`/api/docs` + `/api/openapi.json`
→ 404 in production, verified at the edge) and **F10** (rate-limit client IP
honors `X-Forwarded-For` behind a trusted proxy, verified).

**Re-verification on the post-change code:** backend **395/395** · frontend
**42/42** + tsc clean (upgraded toolchain vite 6.4.3 / vitest 3.2.7 — the 1
HIGH + 1 CRITICAL npm advisories found during the phase were fixed; 2
MEDIUM react-router advisories classified + reported) · live e2e **133/133**
· ruff + mypy clean · **production-readiness suite 42/42 (exit 0)**.

**Traceability:** four rows promoted PARTIAL → PASS (NFR-01, NFR-07,
TSD §51, TSD §52) — totals moved **108/16 → 112/12** (0 FAIL).

**Still open for a final MVP release declaration:** the dataset floor
(item 1) and the F13 login-latency decision (item 5) — data-scale and
product-decision items — plus the WCAG audit and live email delivery, which
need preconditions outside this sandbox. Everything else is closed or by
design. Full detail: `docs/progress/production-deployment.md`.

---

## 10. Statement on the audit's own integrity

- No AI metric in this report was estimated — every number is reproducible via the cited script/artifact against the eval database.
- The two eval datasets (matching, warranty mapping) are synthetic with ground truth by construction; the results prove the engines' **behavior** (ranking, honesty, version handling) but not real-world accuracy — stated in the artifacts and in the traceability report.
- Load-test results were taken with the global rate limit temporarily raised so the limiter itself doesn't contaminate latency stats; the limiter's behavior was separately verified live (360-burst).
- F12 was found by the re-run of the existing frontend suite, fixed as a bug fix, and is reported with the same register as every other finding.

**Bottom line:** the 21-step workflow works end-to-end and is tested at every layer; every CRITICAL/HIGH defect found is fixed and regression-locked; all 570 executed tests pass. The system is *not* declared production-ready because the specs' own completion gates (calibrated thresholds on a floor-sized dataset, production secrets, TLS/at-rest encryption, live email, WCAG audit, deployment rehearsals) remain open — each is bounded, listed, and testable when its precondition is met.
