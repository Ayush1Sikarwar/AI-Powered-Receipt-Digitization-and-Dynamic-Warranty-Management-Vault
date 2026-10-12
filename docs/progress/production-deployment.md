# Phase Report — Production Deployment Preparation

Date: 2026-09-04 · Status: **COMPLETE** — production stack built, deployed to a
live production-configured sandbox, and verified end-to-end by the 42-check
production-readiness suite (**42/42 PASS**). Scope kept to the mandate:
*infrastructure, security, data, operations, CI/CD* — no unrelated
functionality added.

Standing constraints honored: dev / staging / production are separate
stacks with separate credentials; **no development credentials are used in
production; no secrets are committed** (env templates are placeholders only;
filled files are git-ignored and delivered from the CI/CD secret manager).

## 1. What was built

### 1.1 Backend production features
| Item | Where |
|---|---|
| Prometheus `/metrics` (request counters/latency, job gauges, worker heartbeat age) — internal-only, never exposed at the public edge | `app/core/metrics.py`, `app/main.py` |
| Production boot guards: weak/default `AUTH_SECRET`, `DEBUG=true`, or a development/`*` CORS origin ⇒ `RuntimeError: Refusing to start in production` (dev ⇒ loud startup warning) | `app/main.py::_enforce_secret_policy` + `_enforce_production_policy` |
| `/health/ready` extended: database + queue + **worker heartbeat freshness** (degrades when stale) | `app/main.py` |
| Secure response headers (HSTS `includeSubDomains`, CSP, X-Frame-Options DENY, nosniff, no-referrer) — edge + direct | `app/main.py` |
| Redis-backed rate limiting with `X-Forwarded-For` client identification behind a proxy (`TRUST_PROXY`, first XFF entry) — closes audit finding **F10** | `app/core/rate_limit.py`, `app/main.py` |
| Worker heartbeat published to Redis every 15 s (readiness + Prometheus alert on staleness > 120 s) | `app/workers/run.py` |
| Operations settings block (`METRICS_ENABLED`, `SECURITY_HEADERS_ENABLED`, `HEALTH_CHECK_WORKER`, `TRUST_PROXY`, `WORKER_HEARTBEAT_MAX_AGE_SECONDS`) | `app/core/config.py` |

### 1.2 Infrastructure (`infrastructure/`)
| Item | Where |
|---|---|
| Production Docker images: multi-stage, non-root backend + frontend | `docker/backend.Dockerfile`, `docker/frontend.Dockerfile` |
| `docker-compose.prod.yml`: postgres + redis + migrate (one-shot, gates the API) + backend + worker + frontend + backup profile; `${VAR:?}` guards refuse to start on any missing production variable | `docker/docker-compose.prod.yml` |
| TLS reverse proxy / API gateway: HTTP→HTTPS 301, secure headers, `/api/` proxy (15 MB buffers), SPA static serving | `nginx/nginx.conf` |
| Certificate generation (self-signed profile; production places CA-issued certs in the same location; `nginx/certs/` git-ignored) | `nginx/generate-certs.sh` |
| PostgreSQL hardening (memory sizing, statement timeout, log format/min_messages) | `postgres/production-postgresql.conf` |
| Environment templates — **placeholders only**: `production.env.example`, `staging.env.example` (separate domains, databases, buckets, SMTP identities) | `environments/` |
| Deploy / rollback / backup scripts (`deploy.sh` validates required vars; immutable git-tagged images; `.deploy-state` rollback tracking; `backup.sh` = pg_dump -Fc + storage tar + sha256 manifest + `--keep` retention) | `scripts/` |
| Monitoring: Prometheus scrape config, alert rules (queue > 100/5 m, 5xx > 5%/5 m, p95 > 500 ms/10 m, worker heartbeat > 120 s, backup older than 25 h), operations runbook (backup/restore, deploy/rollback, TLS, alerts, log & metric sources) | `monitoring/` |

### 1.3 CI/CD (`.github/workflows/`)
- `ci.yml` — build, lint (ruff/mypy/tsc), type-check, backend + frontend
  tests, **`pip-audit`**, **`npm audit --audit-level=high`** on every PR.
- `deploy.yml` — **staging: automatic on merge to `main`** (gated on green CI
  via `environment: staging`); **production: manual `workflow_dispatch`
  behind a GitHub environment protection rule**; separate rollback dispatch.
  Environment files come from base64 secrets `STAGING_ENV_FILE` /
  `PRODUCTION_ENV_FILE` — never in the repository.

### 1.4 Production-readiness verification suite
`backend/scripts/production_readiness_test.py` (`make readiness`) — 42 live
checks executed against the production-configured stack: TLS, HTTP→HTTPS
redirect, SPA over TLS, edge proxy readiness, secure headers, CORS
allow/block, register/login/me, **IDOR cross-user probe (404)**, oversized
upload (400), non-allowed upload (400), **rate-limit 429 + Retry-After**,
OpenAPI hidden in production, **production boot refused with weak secret**,
backup set + sha256-verified manifest, migrations at head, retention-aware
deletion, `/metrics` content (counters, job gauges, heartbeat age),
worker processing a real document end-to-end (5.0 s), completed-jobs
counter, dependency scans (pip-audit + npm audit), **secret scan of the
repository**, environment isolation (no dev credentials in the production
template), structured JSON logs with request_id.

## 2. Verification (all executed live, 2026-09-04)

### 2.1 Production-readiness suite — **42/42 PASS** (exit 0)
Run 4 against the production-configured sandbox (nginx TLS edge :8443 /
:8080, prod API :8001, prod worker on Redis db1, `warranty_vault_prod` at
Alembic head `7d3a9f1c5e02`): every check PASS. Highlights:
TLSv1.3 chain verified; readiness through the edge
(`database: ok, worker: ok (1s old), queue: ok`); all five secure headers
via the edge; CORS allows the configured origin and blocks an unknown one;
IDOR cross-user document read → **404**; 16 MB PNG → **400 FILE_ERROR**;
`.exe` → **400**; login burst → **429 Retry-After=59**; `/api/docs` →
**404** in production; weak-secret production boot → **RuntimeError**;
backup set created and **sha256 verified against the manifest**; document
upload → worker → `REVIEW_REQUIRED` in 5.0 s; completed-jobs counter
incremented; **no committed secrets / private keys**; production template
uses the production DB (not dev).

### 2.2 Regression after the production changes
- Backend: `pytest -q` — **395/395 PASS** (after restoring the
  `_enforce_secret_policy` contract split — see §3.1).
- Frontend: `tsc -b` clean; `vitest` **42/42 PASS**; production build OK
  (322.22 kB JS / 92.10 kB gzip) — on the upgraded toolchain (§3.2).
- E2E: **133/133 PASS** (alerts 31 · product 11 · warranty 46 · claims 45),
  re-run against the dev API + worker restarted on the post-change code.

### 2.3 Dependency security scans
- **pip-audit (backend): clean** — the only finding was the sandbox venv's
  own `pip 26.1.2` (PYSEC-2026-3721), upgraded to 26.2 →
  “No known vulnerabilities”.
- **npm audit (frontend): 0 high / 0 critical.** The initial scan found
  1 HIGH + 1 CRITICAL, both **dev-only toolchain** packages:
  - `vite <= 6.4.2` (HIGH — path traversal in optimized-deps handling,
    `server.fs.deny` bypass, Windows NTLMv2 disclosure via launch-editor)
  - `vitest <= 3.2.5` (CRITICAL — arbitrary file read/execute **when the
    Vitest UI server is listening**; tests run in non-UI mode)
  Fixed by toolchain upgrade: `vite 5.4.21 → 6.4.3`,
  `vitest 2.1.9 → 3.2.7`, `@vitejs/plugin-react 4.3.1 → 4.5.0` (first 4.x
  supporting vite 6). Verified: tsc clean, 42/42 tests, build OK.
- **Remaining (MEDIUM, reported per the audit rule “fix only CRITICAL/HIGH”):**
  `react-router` / `react-router-dom` ≤ 7.17.0 (open-redirect bypass via
  backslash in `Link`/`useNavigate`; constructor injection in
  `deserializeErrors` SSR hydration). The fix is a **major** 6 → 7 upgrade
  of a runtime dependency with API-breaking changes; it is recorded here as
  a classified MEDIUM item and does not block the `--audit-level=high`
  gate. No LOW/INFO items.

## 3. Issues found and fixed during this phase

1. **`_enforce_secret_policy` contract broken by the production rewrite.**
   The regression suite (`tests/security/test_processing_dos.py`) pins a
   module-level `_enforce_secret_policy()` that reads module `settings`.
   The production rewrite had folded the secret check into
   `_enforce_production_policy()`, which also flags the *default*
   development CORS origin — so “strong secret accepted” could no longer be
   tested in isolation. Fixed by splitting: `_enforce_secret_policy()` is
   again the secret-only guard (prod ⇒ RuntimeError / other envs ⇒
   warning), and `_enforce_production_policy()` calls it first, then adds
   the DEBUG + CORS-origin checks. All 3 previously failing tests green;
   full suite 395/395.
2. **Readiness suite fragility vs. a flaky npm registry.** `npm audit`
   (human output) hung without bound; the suite now runs
   `npm audit --json --audit-level=high` with a 300 s bound and reports
   structured counts (`high=… critical=… moderate=…`) instead of a bare
   last-line excerpt. Same bound added to pip-audit.
3. **Secret-scan false positives** (self-signed certs, the scanner's own
   pattern literals, `__pycache__`, binary suffixes) — scanner now excludes
   `certs/` / caches / build dirs and binary files, and skips its own file.
4. **Runbook error**: restore flag was documented as `--backup-set`; the
   CLI is `--set`. Fixed in `infrastructure/monitoring/runbook.md`.
5. **Makefile `security-scan` bug**: `pip-audit --no-deps` without a
   requirements file is a usage error; now plain `pip-audit` (audits the
   venv).
6. **Missing `staging.env.example`** (deploy.sh requires it) — created with
   staging-specific values (separate domain/DB/bucket/SMTP identity).
7. **Sandbox venv pip vulnerability** (PYSEC-2026-3721) — upgraded
   `pip 26.1.2 → 26.2`.

## 4. Audit findings closed by this phase

| ID | Sev | Finding | Closed by |
|---|---|---|---|
| F4 | LOW | `/api/docs` + `/api/openapi.json` exposed in all envs | nginx serves the SPA and proxies `/api/` only; `/api/docs` and `/api/openapi.json` return **404 in production** (readiness check). Dev keeps docs via the dev server. |
| F10 | MEDIUM | Rate limit keyed on socket IP only (no X-Forwarded-For) | `TRUST_PROXY` support: nginx forwards `X-Forwarded-For`; the middleware uses the first XFF entry when the proxy is trusted (production sets `TRUST_PROXY=true`). |

Still open (report-only by decision): **F13** (login p95 793.7 ms vs the
500 ms target — Argon2id cost; a product/security decision item, not
“fixed” by weakening auth), **F11** (`PATCH /auth/me` ignores unknown
fields).

## 5. Known caveats / decisions for the real rollout

- **Self-signed certificates** in `infrastructure/nginx/certs/` are the
  sandbox/staging profile only. Production must place CA-issued
  fullchain/privkey in the same location (`CERT_DIR`); the readiness suite
  is run with `--allow-insecure-tls` against self-signed material and
  without the flag against a real chain.
- **S3 object storage**: the production template targets S3 with
  least-privilege credentials and short-lived signed URLs; enable
  **bucket versioning** for the originals bucket (TSD §51
  “versioning where appropriate”) — a provider-side setting, documented in
  the runbook.
- **Docker images** are verified at config level (no Docker daemon in this
  sandbox); the production stack was emulated natively with the identical
  configuration artifacts (same postgres conf, nginx conf sed-substituted
  only for host paths/ports, same env files).
- **npm registry latency** in this sandbox made audits slow (2–5 min);
  the suite bounds them at 300 s and fails loudly instead of hanging.
- **react-router 6.x MEDIUM advisories** — schedule the 6 → 7 major
  upgrade as an explicit change (see §2.3).
