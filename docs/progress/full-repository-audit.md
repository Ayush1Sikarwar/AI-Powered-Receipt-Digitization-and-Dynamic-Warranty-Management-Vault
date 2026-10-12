# Phase Report — Full Repository Audit (Mandate: "STOP adding new features")

Date: 2026-09-04 · Status: **COMPLETE** — entire repository inspected (no file
accepted on existence alone), the 21-step workflow traced live, 10 layers
verified per feature, 8 attributes verified per AI feature, security/AI/
performance probes executed against the running stack, every CRITICAL/HIGH
finding fixed and regression-locked, all suites re-run green.

Deliverables:

- `docs/audit/final-audit-report.md` — verdict, workflow trace, security
  results, measured AI metrics, performance data, findings register F1–F13.
- `docs/audit/requirements-traceability.md` — 126-row PASS/PARTIAL/FAIL/N-A
  matrix over every SRS/PRD/TSD requirement: **108 PASS · 16 PARTIAL · 0
  FAIL · 2 N-A** (at audit end; the later production deployment phase then
  promoted four rows to PASS on live evidence → **112 · 12 · 0 · 2** — see
  `docs/progress/production-deployment.md` and the post-audit note in the
  traceability report).

## 1. Findings and disposition

| ID | Sev | Finding | Disposition |
|---|---|---|---|
| F1 | HIGH | Upload size only enforced after full buffering | **Fixed** — streaming 1 MB-chunk cap (`documents.py`); 20 MB → 400 in 0.1 s; regression test |
| F2 | HIGH | Image decompression bombs (70-byte PNG declaring 2025 MP ≈ 6 GB decode) | **Fixed** — 40 MP declared-dimension cap before `img.load()`; live-verified; regression tests |
| F3 | HIGH | PDF page cap checked after rendering (~2 min × 3 retries) | **Fixed** — pre-render pypdf check; 25-page PDF → FAILED in 1.0 s; regression tests |
| F4 | LOW | `/api/docs` + `/api/openapi.json` exposed in all envs | Reported (prod proxy should strip) |
| F5 | NOTE | RL backend in-memory per process (Redis when available) | Reported |
| F6 | HIGH | No production guard against default/short JWT secret | **Fixed** — `_enforce_secret_policy` in `create_app` (prod RuntimeError / dev warning); regression tests |
| F7 | HIGH | Global per-IP API rate limit configured but **not wired** | **Fixed** — middleware, 300/min, 429 + Retry-After; live burst verified (360 → 292×200 + 68×429); regression test |
| F8 | MED→fixed | Out-of-range integer IDs → 500 | **Fixed** — `sqlalchemy.exc.DataError` handler inspecting `exc.orig` → 404; regression tests |
| F9 | HIGH | Document stuck in PROCESSING (deterministic failure) | **Fixed** — DB repaired + terminal-FAILED guarantee on job **and** document; regression test |
| F10 | MEDIUM | RL keyed on socket IP only (no X-Forwarded-For) | Reported (deployment note) |
| F11 | LOW | `PATCH /auth/me` ignores unknown fields | Reported |
| F12 | HIGH | `TracePanel.tsx` render crash on partial trace payload (whole page white-screen) — found during the mandated test re-run | **Fixed** — defensive normalization + regression test |
| F13 | MEDIUM | Login p95 793.7 ms > 500 ms TSD target (Argon2 cost) | Reported — decision item, not "fixed" by weakening auth |

**CRITICAL: 0 found. HIGH: 7 (+1 promoted) — all fixed, live-verified,
regression-tested.**

## 2. New measurement infrastructure (audit scope, not features)

- `backend/ai_eval/_evaldb.py` — dedicated `warranty_vault_eval` Postgres
  database (create/migrate) so evaluations never touch dev data.
- `backend/ai_eval/product_matching_eval.py` — runs the **unmodified**
  matcher over a 20-product/5-manufacturer synthetic catalog + 117 positive
  variants + 6 negative grocery lines. Measured: **Top-1 85.59 %, Top-3
  89.19 %, false-match rate 0.0, negative rejection 100 %**.
- `backend/ai_eval/warranty_mapping_eval.py` — runs the **unmodified**
  rules engine + timeline over a 7-case deterministic set (region priority,
  versioning, unknown-region honesty, registration UNKNOWN/anchored,
  battery coverage, out-of-window no-match). Measured: **policy 100 % /
  duration 100 % / source validity 100 %**.
- `backend/tests/security/test_processing_dos.py` — 9 regression tests
  locking in F1–F3, F6, F7, F8.

Honest caveat (stated in every results artifact): both new datasets are
synthetic with ground truth by construction; they prove engine **behavior**
(ranking, honesty, version handling), not real-world accuracy.

## 3. Test totals after all fixes

| Suite | Result |
|---|---|
| Backend | **395 / 395** (95.9 s) — incl. 9 new regression tests |
| Frontend | **42 / 42** — incl. 1 new TracePanel regression test; `tsc --noEmit` clean |
| Live e2e | **133 / 133** (alerts 31 · product 11 · warranty 46 · claims 45) |
| Ruff / mypy (changed code) | clean |
| Security probes | IDOR 25/25 · privilege-escalation 7/7 · SQLi 5/5 clean · token/session 7/7 · CORS no-reflection |
| Backup/restore | backup run live (pg_dump -Fc + storage tar + manifest); restore into scratch DB verified (0 errors; 80 users / 36 documents) |

## 4. Performance (TSD §48 load-test mandate — executed)

- Load test: 841 requests, 8 concurrent, 41.6 RPS, **0 % errors**, 0 × 5xx;
  non-auth p95 85–222 ms (< 500 ms ✓); login p95 793.7 ms (F13, documented
  deviation); results in `backend/data/loadtest/`.
- 1-page receipt end-to-end: **5.1 s** (< 10 s target ✓); over-cap PDF
  rejected in 1.0 s.
- Spec notes added: TSD §36 (security), §41 (testing/AI eval), §48
  (performance); SDD §8 (confidence thresholds PROVISIONAL).

## 5. Environment rebuild + fresh re-verification (same day)

The sandbox runtime (venv, Postgres, Redis, services) does not persist
between sessions — only workspace files do. To prove the persisted workspace
is complete and self-consistent, the entire stack was rebuilt from the repo
alone (apt: postgresql 17 / redis / tesseract-ocr / poppler; `pip -e
backend`, `npm install`, migrations 0001–0012, catalog + warranty seeds, API
+ worker + vite) and every suite was re-run **fresh**:

| Suite | Fresh result |
|---|---|
| Backend | **395 / 395** (395 collected; suite exit 0) |
| Frontend | **42 / 42** + `tsc --noEmit` clean |
| Live e2e | **133 / 133** (31 + 11 + 46 + 45) |
| Pipeline smoke | 1-page receipt → REVIEW_REQUIRED in **5.1 s** (matches original measurement) |

Two environment prerequisites surfaced (now documented in the README): the
`warranty_vault_test` database must exist (the suite rebuilds its schema, not
the database), and the `warranty` role needs `CREATEDB` (backup/restore tests).

## 6. Verdict

**Not declared production-ready.** All required tests pass and every
CRITICAL/HIGH is fixed, but the specs' own completion gates remain open —
each bounded and listed in `docs/audit/final-audit-report.md` §9:

1. PRD §5 dataset floor (50/category) unmet ⇒ thresholds PROVISIONAL
2. production `AUTH_SECRET` (guarded: prod boot hard-fails without one)
3. TLS in transit / at-rest encryption (deployment playbooks documented)
4. live email provider (scheduling/idempotency verified with log provider)
5. login-latency decision (F13)
6. staging/prod deployment rehearsals, metrics pipeline, WCAG audit
