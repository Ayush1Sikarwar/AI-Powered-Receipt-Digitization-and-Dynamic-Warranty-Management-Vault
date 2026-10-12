# WarrantyVault AI

A consumer-facing **digital warranty vault** that converts physical purchase
evidence (receipts/invoices) into structured, warranty-aware records. It
enhances and reads documents, extracts fields with confidence and provenance,
identifies the product, maps the applicable **manufacturer** warranty, tracks
expiries, sends reminders, and helps prepare a claim-ready package.

> **Trust model:** AI output is a *suggestion* until verified. Every displayed
> warranty fact is labeled as **manufacturer** coverage (not legal advice), and
> carries provenance. User corrections never overwrite the original evidence.

The authoritative specifications live in [`docs/`](docs/):

| Doc | Purpose |
| --- | --- |
| [01_SRS_Requirements.md](docs/01_SRS_Requirements.md) | Functional / non-functional / AI / security requirements |
| [02_SDD_System_Design.md](docs/02_SDD_System_Design.md) | High-level architecture, services, data & API design |
| [03_PRD_Project_Roadmap.md](docs/03_PRD_Project_Roadmap.md) | MVP definition, priorities, phases, dataset strategy |
| [04_TSD_Technical_Specification.md](docs/04_TSD_Technical_Specification.md) | Implementation contract (modules, entities, APIs, rules) |

Progress reports per phase are in [`docs/progress/`](docs/progress/).

---

## MVP journey

```
Register → Upload receipt → Secure storage → Processing → Enhancement → OCR
→ Structured extraction → Confidence scoring → Human verification
→ Product/SKU identification → Warranty source retrieval → Warranty mapping
→ Warranty rules calculation → Dashboard → Expiration alerts
→ Claim creation → Claim-ready package
```

## Stack

- **Backend:** Python 3.12+, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, PostgreSQL 17
- **AI/ML:** OpenCV (preprocessing), Tesseract (OCR via pytesseract), rule/schema-constrained extraction, RapidFuzz matching
- **Async:** Background worker process with bounded retries (job table in Postgres)
- **Storage:** PostgreSQL + pluggable object storage (local FS in dev, S3 in prod)
- **Frontend:** React 18, TypeScript, Vite, Tailwind CSS, React Query, React Router
- **Infra:** Docker Compose, Redis (rate limiting), structured JSON logs

## Repository layout

```
warranty-vault/
├── backend/          FastAPI app (api/, core/, models/, services/, ai/, warranty/, workers/)
├── frontend/         React + TS + Vite app (src/pages, components, services, hooks)
├── data/             raw / processed / evaluation / fixtures (not committed)
├── infrastructure/   docker/, nginx/, postgres/, environments/, scripts/, monitoring/
├── docs/             SRS, SDD, PRD, TSD + phase progress reports
├── .env.example
├── docker-compose.yml
└── Makefile
```

## Quick start (local)

Prereqs: Python 3.12+, Node 20+, PostgreSQL 17, Redis, Tesseract (`tesseract-ocr`).

```bash
# 1. Configure environment
cp .env.example backend/.env          # edit secrets

# 2. Install
make install

# 3. Migrations
make migrate

# 4. Run (separate terminals)
make backend    # API on :8000
make worker     # background processing
make frontend   # UI on :5173
```

Open http://localhost:5173, create an account, and start the journey.

### Running via Docker

```bash
docker compose up --build
```

## Testing

```bash
make test        # backend (unit/integration/api/ai/security) + frontend
make lint        # ruff + mypy + tsc
```

The backend test suite rebuilds the schema of a dedicated
`warranty_vault_test` database from Alembic migrations, so **migration
integrity is verified on every run**. Prerequisites (one-time):

```bash
sudo -u postgres createdb -O warranty warranty_vault_test
sudo -u postgres psql -c "ALTER ROLE warranty CREATEDB;"  # needed by the backup/restore tests
```

## Production deployment

Production is a separate stack (`infrastructure/docker/docker-compose.prod.yml`)
behind a TLS reverse proxy (`infrastructure/nginx/`), with its own database,
object storage, Redis, credentials, and secrets. Development credentials are
never used in production and secrets are never committed.

```bash
# 1. Configure (git-ignored; values from the secret manager, NOT the examples)
cp infrastructure/environments/production.env.example .env.production   # fill every value
#    generate secrets: openssl rand -hex 32

# 2. TLS material (self-signed for staging; CA-issued for production)
bash infrastructure/nginx/generate-certs.sh

# 3. Deploy (or rely on .github/workflows/deploy.yml)
ENV=production infrastructure/scripts/deploy.sh
```

CI/CD: `.github/workflows/ci.yml` runs build / lint / type-check / test /
`pip-audit` / `npm audit` on every pull request. `.github/workflows/deploy.yml`
deploys **staging automatically on merge to `main`** (after CI is green);
**production is manual** (`workflow_dispatch` behind a GitHub environment
protection rule) and has a separate rollback dispatch. Environment files are
supplied as base64 secrets (`STAGING_ENV_FILE` / `PRODUCTION_ENV_FILE`).

Operational essentials (full detail in
[`infrastructure/monitoring/runbook.md`](infrastructure/monitoring/runbook.md)):

| Concern | Where |
| --- | --- |
| Health / readiness / metrics | `GET /health/live`, `GET /health/ready`, `GET /metrics` (Prometheus) |
| Backups (pg_dump + storage + sha256 manifest) | `make backup ENV=production` → `infrastructure/scripts/backup.sh` |
| Restore (rehearsed) | `backend/scripts/restore.py` — runbook §1 |
| Rollback to previous image | `infrastructure/scripts/rollback.sh` (state in git-ignored `.deploy-state`) |
| Monitoring & alerts | `infrastructure/monitoring/prometheus.yml`, `alert_rules.yml` |
| PostgreSQL hardening | `infrastructure/postgres/production-postgresql.conf` |

The live production-readiness verification suite is
`backend/scripts/production_readiness_test.py` (TLS, secure headers, CORS,
auth, IDOR, upload abuse, rate limiting, prod boot guards, backup/restore,
migrations, worker heartbeat, metrics, dependency + secret scans). Run it
with `make readiness` after any infrastructure change.

## Audit

The 2026-09-04 full-repository audit (security, AI measurement, performance,
requirements traceability) and its verdict are in
[`docs/audit/`](docs/audit/): `final-audit-report.md` and
`requirements-traceability.md` (126 requirements: 112 PASS / 12 PARTIAL /
0 FAIL / 2 N-A — after the production deployment phase promoted
NFR-01, NFR-07, TSD §51 and TSD §52 to PASS on live evidence).

## Configuration

All configuration is environment-driven (see [.env.example](.env.example)).
Secrets are never committed. Key categories: application, database, Redis,
auth, rate limits, uploads, object storage, email, AI pipeline, warranty, and
retention.

## Key engineering rules (from the TSD)

A feature is **complete** only when it has:
UI + API + Business Logic + Database + Validation + Error Handling +
Authorization + Logging + Tests + Documentation.

AI features additionally require:
Model + Evaluation Dataset + Metrics + Confidence + Versioning + Human Review +
Provenance.

The four documents are the source of truth. No requirement is silently removed;
no feature is downgraded to a mock; and AI results are never fabricated —
unavailable integrations surface an honest *not-configured* state.
