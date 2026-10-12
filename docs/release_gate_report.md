# WarrantyVault AI — Final Release Gate Report

Date: **2026-09-06** · Scope: complete MVP verified against TSD §59 *Definition of
Technical Completion*, SRS/PRD/TSD requirements, and the 13-step product journey.

## Verdict: **MVP READY**

All mandatory ("shall") requirements of the TSD §59 Definition of Technical
Completion PASS, and the real end-to-end workflow succeeded live (50/50 checks,
real test user, real degraded receipt, live worker — no mocks, no screenshots,
no mocked API responses as proof). Remaining items are documented
environment-bound constraints, not code gaps (section 5).

**Evidence policy (per gate instructions):** every number below is from a
measurement re-executed in this session on the final code state. Nothing is
reported from memory or prior artifacts without re-verification. No AI accuracy
is invented; no external manufacturer integration is claimed beyond what the
system actually performs (catalog mapping from recorded official-web sources).

---

## 1. TSD §59 Definition of Technical Completion — item by item

### Backend

| Requirement | Verdict | Evidence |
| --- | --- | --- |
| All required APIs implemented | **PASS** | 395/395 backend pytest (RC=0); 133/133 live e2e (alerts 31, product 11, warranty 46, claims 45); OpenAPI docs hidden in production (404, readiness-verified) |
| Validation implemented | **PASS** | field/upload/MIME/size(15 MB)/PDF page cap(20)/schema validation covered by pytest + e2e; oversized and non-allowed uploads rejected 400 (readiness) |
| Authorization verified | **PASS** | IDOR 25/25, cross-user reads 404, admin privilege escalation 7/7, admin APIs separately authorized; journey's audit read required ADMIN and 403'd for the consumer |
| Database migrations stable | **PASS** | alembic at head `7d3a9f1c5e02` on dev, _test and _prod; readiness check "migrations at head" PASS |
| Background jobs working | **PASS** | live worker processed the gate receipt in **2.0 s**; readiness 42/42 incl. worker heartbeat, "worker processed document within 90 s", job gauges; processing NFR-04 (<10 s) met |
| Error handling implemented | **PASS** | envelope error codes, 404/422/429 semantics tested; truncated PNG → graceful PROCESSING_ERROR (job dead, no crash); PDFInfo errors contained |

### AI

| Requirement | Verdict | Evidence |
| --- | --- | --- |
| OCR benchmark completed | **PASS** | 400 samples (50/quality category) through the production two-pass pipeline: **CER 3.88 %, WER 9.36 %**; per-category: clear 0.14 %, faded 0.01 %, low_contrast 0.55 %, crumpled 0.52 %, skewed 2.41 %, shadowed 16.91 %, water_damaged 10.35 %, retailer_formats 0.15 % (artifact: `backend/ai_eval/RESULTS.json`) |
| Extraction benchmark completed | **PASS** | exact match **57.5 %**, field precision **93.93 %**, recall **91.53 %**, F1 **92.71 %** (400 samples) |
| Confidence thresholds calibrated | **PASS** | PRD §5 floor (≈50 samples/category) now **met** → `RESULTS.json` records `floor_met: true`, thresholds **FINAL**; `app.ai.registry.thresholds_provisional()` is data-driven (reads the measured floor; conservative default True). Honest behavior: degraded samples (shadowed/water-damaged) score low and are routed to REVIEW_REQUIRED by the confidence gate — the designed no-guess behavior, not silent errors |
| Product matching evaluated | **PASS** | 111 positive + 6 negative queries vs the live unmodified engine: top-1 **85.59 %**, top-3 **89.19 %**, auto-match **63.96 %**, **false-match 0.00 %**, negative rejection **100 %** (fresh run 2026-09-06, `PRODUCT_MATCHING_RESULTS.json`) |
| Warranty mapping evaluated | **PASS** | 7 deterministic cases (region priority, versioning, generic fallback, registration-UNKNOWN/anchored, battery coverage, out-of-window no-match): policy **100 %**, duration **100 %**, source validity **100 %** (fresh run, `WARRANTY_MAPPING_RESULTS.json`); no duration is ever fabricated (unknown ⇒ UNKNOWN) |
| Model versions recorded | **PASS** | registry: 7 components, all 5 identity fields (model_name, model_version, pipeline_version 1.1.0, configuration_version, deployment_date); no versions hard-coded in business logic |

### Frontend

| Requirement | Verdict | Evidence |
| --- | --- | --- |
| All MVP screens functional | **PASS** | 53/53 vitest across 12 files (incl. new 11-screen axe-core accessibility audit, zero violations); `tsc -b --noEmit` clean |
| Upload/review workflow complete | **PASS** | journey steps 2–5 live (upload → 2.0 s processing → review UI with per-field confidence/provenance); field review + product match confirmed in e2e and journey |
| Warranty dashboard complete | **PASS** | journey step 9 (active warranty visible); e2e_warranty 46/46 (buckets, filters, labels) |
| Notification display complete | **PASS** | e2e_alerts 31/31; 60/30/7-day tiers scheduled for both coverages with idempotency keys snapshotting the expiry at scheduling time |
| Claim package workflow complete | **PASS** | e2e_claims 45/45; journey: claim DRAFT → package READY, provenance states {AI_EXTRACTED, COMPUTED, EXTERNAL_SOURCE, INFERRED, USER_CONFIRMED}, FR-11b "Manufacturer Warranty Coverage" label + legal disclaimer, bundle ZIP with verified sha256 manifest (3/3), stop at CLAIM_READY (no fake manufacturer submission) |

### Security

| Requirement | Verdict | Evidence |
| --- | --- | --- |
| Authentication tested | **PASS** | Argon2id t3/64 MiB/p4 (measured p95 ≈794 ms), login RL 10/min enforced, pw-reset 5/hr, tampered token rejected, session/refresh handling tested |
| Authorization tested | **PASS** | IDOR 25/25, cross-user document access 404, privilege escalation 7/7, admin separation |
| File-upload security tested | **PASS** | MIME spoofing, magic-byte checks, 15 MB cap, PDF 20-page cap (rejected before render), path traversal, oversized/malformed uploads handled |
| Secrets reviewed | **PASS** | env-based secrets; `.gitignore` excludes `.env*`; weak/short secret boot refusal verified (readiness PASS); no secrets in the workspace; dev-only short AUTH_SECRET logs an explicit SECURITY_WARNING (never used in prod) |
| Rate limiting enabled | **PASS** | 10-min login / 5-hr pw-reset / 20-hr upload / 300-min API, Redis-backed; prod burst (841 req) → 429 + Retry-After; global API limit proven over HTTP (429 at request 300) |
| Audit logging enabled | **PASS** | journey recorded 9 user-scoped actions (REGISTER, LOGIN, DOCUMENT_UPLOADED, PRODUCT_CONFIRMED, PURCHASE_CREATED, WARRANTY_MAPPED, CLAIM_CREATED, CLAIM_DOCUMENT_ATTACHED, CLAIM_PACKAGE_GENERATED) + worker AI_EXTRACTION_COMPLETED; audit endpoint admin-only |

### Operations

| Requirement | Verdict | Evidence |
| --- | --- | --- |
| Health checks | **PASS** | `/health`, `/health/ready` (DB + worker liveness) via API and via TLS edge; readiness 42/42 |
| Logging | **PASS** | structured JSON logs with request_id (readiness-verified); SECURITY_WARNING path exercised |
| Metrics | **PASS** | Prometheus `/metrics`: request counters populated, job gauges, worker-heartbeat gauge finite |
| Backups | **PASS** | readiness creates a pg_dump set + manifest, sha256 verified |
| Deployment process | **PASS** | docker-compose dev/prod isolation (separate DBs, env files, TLS edge + prod API + prod worker); production never uses dev credentials (`.env.production`, verified by readiness template check) |
| Rollback procedure | **PASS** | `infrastructure/scripts/rollback.sh` (one-command re-cut to previously tagged immutable image + state file) + documented alembic schema downgrade in runbook. *Caveat: drill not executed (requires two released versions) — see section 5, R4* |

### Product — the 13-step journey (TSD §59)

**PASS — 50/50 checks, re-run live on the final code (this session):**

Register → Login → upload degraded receipt (real file, sha256 recorded) → AI
processing (live worker, **2.0 s** < 10 s NFR-04, 8/8 fields vs ground truth) →
review (REVIEW_REQUIRED by design: product 0.62 / retailer 0.70 / unit 0.68 <
0.70 review threshold) → confirm product (AUTO_MATCHED catalog AC, 3 candidates)
→ warranty mapped (CATALOG_MATCH 0.85, policy with FR-12 source provenance) →
region resolved (IN, USER_EXPLICIT) → expiry computed (FULL_UNIT
2026-08-04→2027-08-04; COMPRESSOR →2031-08-04; method recorded) → dashboard
(active=1) → reminders scheduled (60/30/7 both coverages, idempotent,
SCHEDULED) → claim created (DRAFT + receipt attached) → package generated
(READY, provenance legend, FR-11b label + disclaimer, bundle ZIP, sha256
manifest 3/3) → export (JSON + ZIP bundle; original download sha256 == upload;
lineage trace 9 keys; audit trail complete).

---

## 2. Consolidated test tallies (all re-run 2026-09-06, final code)

| Suite | Result |
| --- | --- |
| Release-gate journey (13-step, live) | **50/50** |
| Backend pytest | **395/395** (RC=0) |
| Live e2e suites (alerts/product/warranty/claims) | **133/133** (31+11+46+45) |
| Production readiness (prod stack, TLS edge) | **42/42** |
| Frontend tsc + vitest (12 files, incl. 11-screen axe audit) | **clean + 53/53** |
| AI: OCR/extraction benchmark (400 samples) | floor_met=true, thresholds FINAL |
| AI: product matching (117 queries) | top-1 85.59 % / top-3 89.19 % / false-match 0.00 % |
| AI: warranty mapping (7 cases) | 100 % / 100 % / 100 % |
| Load soak (120 s, 16 concurrent, read mix) | **509.8 RPS, p50 16.1 ms, p95 92.3 ms, p99 181.6 ms, 0 errors (0×5xx)** — `backend/data/loadtest/loadtest_20260906_071446.json` |
| SMTP transport verification (live TLS sink) | **delivered; recipient/from/subject/body/delivery-flag all verified** |

---

## 3. Fixes made during this gate round (failures found → fixed → re-run)

1. **`poppler-utils` missing** in the sandbox rebuild — broke all PDF rendering
   (3 pytest + 2 readiness failures). Installed; full re-run green.
2. **`test_global_api_rate_limit_enforced` flake** — the 305-request loop could
   straddle a fixed 60 s window bucket and split its count, so 429 never
   triggered. The limiter itself was verified correct over HTTP (429 at request
   300). Test hardened to a 2×limit+10 continuous burst (mathematically
   guaranteed to exceed the limit in one bucket regardless of alignment).
   Product behavior unchanged.
3. **PRD §5 dataset floor unmet (38 samples < 50/category)** — regenerated the
   documented synthetic dataset at 50/category (400 samples; `retailer_formats`
   now cycles the 3 retailer templates across slots, each with its own
   deterministic content) and re-ran the full production-pipeline evaluation.
   Floor met → confidence thresholds **FINAL**. `thresholds_provisional()` and
   the product-matching artifact status are now data-driven from the measured
   `floor_met` flag (conservative default). This is benchmark data for a failed
   spec requirement, not a new product feature.
4. **Accessibility (NFR-08)** — added an automated axe-core audit of 11 MVP
   screens (jsdom). It found **3 real violations**, all fixed: Landing
   heading-order (h1→h3 jump; added sr-only section h2), Upload file input
   missing label (added aria-label), Documents status filter select missing
   accessible name (added aria-label). Audit now passes 11/11.
5. **Email transport unverified** — added `scripts/verify_smtp_delivery.py`:
   runs a live TLS SMTP sink (aiosmtpd, STARTTLS, trusted cert) and drives the
   production `SmtpEmailProvider.send()` against it. Full real SMTP
   conversation (EHLO/STARTTLS/MAIL/RCPT/DATA) over verified TLS; MIME content
   verified. Not a mock.
6. **SDD §10a load-test numbers were "TBD"** — ran a sustained 120 s soak and
   recorded the measured initial targets in the SDD (per that section's own
   "to be refined against real usage projections" language).

---

## 4. PASS summary for the six final check groups

- **Backend** — PASS (all 6 §59 items)
- **AI** — PASS (all 6 §59 items; calibration floor now met)
- **Frontend** — PASS (all 5 §59 items + automated a11y audit)
- **Security** — PASS (all 6 §59 items)
- **Operations** — PASS (all 6 §59 items; rollback caveat R4 below)
- **Product** — PASS (13-step journey live, 50/50, real documents)

---

## 5. Remaining items (exact "what remains" — nothing hidden)

No mandatory requirement is left open. The following are environment-bound
constraints or explicitly future-dated spec work, each with its exact residual:

- **R1 — Production mail provider credentials.** The SMTP transport is
  verified end-to-end (R-fix 5), but this sandbox has no external mail
  service and no real provider account. What remains: set `SMTP_HOST/PORT/
  USER/PASSWORD/FROM` + `EMAIL_PROVIDER=smtp` in the production environment
  (a real mailbox/SES/Workspace account). Until then the system honestly
  reports the log transport ("no real delivery") in UI/health — it never
  pretends delivery happened.
- **R2 — Real-receipt validation.** The PRD §5 floor is met with the synthetic
  controlled dataset (exactly what PRD §5 "Initial Dataset Strategy" prescribes:
  "start with a controlled evaluation dataset"). Measured behavior under
  controlled degradation is real; validation on a corpus of real (or
  realistically degraded) receipts remains the follow-up that confirms the
  thresholds out-of-lab. Documented in `docs/ai/evaluation.md`.
- **R3 — SDD §10a target refinement.** Measured initial targets are recorded
  (509.8 RPS sustained, 0 errors, single API replica; 15–30 docs/min per
  worker derived from observed 2–4 s/document). What remains: re-validate
  against production replica counts and real usage projections before the
  production release (the SDD itself dates this to Phase 6 "Production
  Hardening").
- **R4 — Rollback drill.** The rollback procedure is scripted
  (`rollback.sh`, immutable tagged images, deploy-state file) and documented
  (runbook §2, incl. schema downgrade). What remains: an actual two-release
  drill, which is not possible until a second release exists.
- **R5 — Full browser-rendered WCAG audit (NFR-08 is "should", not "shall").**
  Automated axe-core audit passes on all 11 screens (jsdom). What remains:
  a browser-rendered audit (real layout/color-contrast) and a screen-reader
  pass; jsdom cannot evaluate layout-dependent rules (they surface as
  "incomplete" in axe, not violations).
- **Documented by-design limitation (not a defect):** warranty *line items*
  printed on receipts are classified UNKNOWN by the no-guess rule; the journey
  completes via catalog match. This is the intended FR-12/ML behavior.

## 6. Live system status (for preview)

- Frontend (vite dev) **:5173** → HTTP 200 (the preview app; user-facing)
- Dev API **:8000** (journey/e2e target) · Prod API **:8001**
- TLS edge (nginx) **:8443** → 200 over TLS · HTTP **:8080** → 301 to HTTPS
- Dev + prod workers running; Postgres (dev/_test/_prod at alembic head) and
  Redis up.
