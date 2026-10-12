# Phase 0 — Foundation (complete)

**Status:** ✅ Complete & verified

## Deliverables (PRD Phase 0)

| Deliverable | Status | Notes |
| --- | --- | --- |
| Repository | ✅ | PRD §11 / TSD §4 layout, docs/ holds the 4 specs |
| Backend skeleton | ✅ | FastAPI app factory, JSON logging, request-id middleware, standard error envelopes, /health + /health/ready |
| Frontend skeleton | ✅ | Vite + React 18 + TS + Tailwind + React Query + Router; Landing/Login/Register/Dashboard; API client with token refresh |
| Database | ✅ | PostgreSQL 17; SQLAlchemy 2.0 + Alembic; full domain foundation, migrations 0001–0006, **25 tables** (see "Database foundation" below) |
| Docker environment | ✅ | docker-compose.yml (postgres, redis, minio, backend, worker, frontend) + Dockerfiles |
| Environment configuration | ✅ | .env.example (names/placeholders only), pydantic-settings, no secrets in source |
| CI pipeline | ✅ | .github/workflows/ci.yml — backend: install OCR, lint, mypy, pytest (with postgres service); frontend: lint, typecheck, test, build |
| Basic authentication | ✅ | Register, login, logout, refresh (rotating, reuse detection), password reset (request/confirm), /me, profile update |

## Security controls implemented

- Argon2id password hashing (modern, memory-hard); timing-equalized unknown-user login
- JWT access tokens (15 min TTL) + hashed, individually revocable refresh tokens (30 d, rotating)
- Logout revokes the refresh token; password reset revokes the whole session chain
- Refresh-token **reuse detection** → full family revocation
- Rate limiting (Redis fixed-window, in-memory fallback): login 10/min, password-reset 5/hour, API/upload limits defined
- Consistent error envelopes with `request_id` (correlation) on every response; no stack traces/secrets leak
- Structured JSON logging with request_id
- Audit trail for LOGIN/LOGOUT/REGISTER/PASSWORD_RESET
- Admin endpoints separately authorized (role checked from DB, not token claim)

## Database foundation (TSD §34, SDD §5) — completed 2026-08-31

Full domain model implemented and migrated (no placeholder tables):

| Migration | Contents |
| --- | --- |
| 0001_auth_core | users, refresh_tokens, password_reset_tokens, audit_logs, email_outbox |
| 0002_digital_vault | documents, document_pages, document_derivatives, manufacturers, retailers, products, product_aliases, purchases, purchase_items, background_jobs, claims |
| 0003_extraction | extraction_jobs, extracted_fields |
| 0004_field_unique | unique (document_id, field_name) on extracted_fields |
| 0005_warranty_domain | **warranty_sources, warranty_policies, warranty_coverages, warranty_mappings, warranty_timelines** (TSD §13–§15) with check constraints (trust bounds, duration validity), unique (policy, component), unique (item, policy), unique (item, coverage), expiry+status index |
| 0006_notifications_referential | **notifications** (TSD §22/§32: idempotency `notification_key`, scheduled_at/user/scheduled indexes) + user-ownership FKs (documents/purchases/claims/notifications → users, ON DELETE CASCADE) + cascade/SET NULL normalization of the whole document/purchase/product FK chain so SRS FR-20 user deletion cannot be blocked |

All TSD §34 "important indexes" present: users.email, documents.user_id,
documents.status, purchases.user_id, purchases.purchase_date, products.sku,
products.model_number, warranty_policies.manufacturer_id,
warranty_policies.region, warranty_timelines (expiry_date, status),
notifications.user_id, notifications.scheduled_at.

## Verification performed

- ✅ **91 backend tests pass** (auth flows, documents, purchases, uploads/security, file validation, region resolution, storage providers, pipeline, AI: OCR/enhancement/extraction, **warranty+notification schema integrity & cascade tests**)
- ✅ **9 frontend tests pass** + `tsc` clean + production build succeeds
- ✅ `ruff` clean, `mypy` clean (all app files)
- ✅ Migrations applied to dev DB (head `c7d4e9f01a5b`, 25 tables) and **downgrade/upgrade round-trip verified**; test suite rebuilds the test schema from Alembic on every run
- ✅ Live HTTP exercise: register → /me → login → refresh rotation → reuse 401 → password reset token in outbox
- ✅ Frontend dev server proxies /api → backend; register through the proxy verified
- ✅ Authorization verified: 401 without token; forged ADMIN role in a signed token is rejected by admin endpoints

## Known limitations (honest)

- The `unit_price` positional heuristic can pick a numeric fragment of a
  model number (e.g. "55" in "55in 4K") instead of the trailing money on
  some receipts. It emits low confidence (≤0.68) so the document still
  routes to REVIEW_REQUIRED — Phase 2 tuning item, not a data-integrity
  risk (value is never auto-accepted below 0.90).
- Password reset emails use the **log provider** (recorded in `email_outbox`) — no real SMTP in this environment. The `SmtpEmailProvider` is implemented and activates via env config.
- Access-token revocation is best-effort: access tokens live 15 min; revocation is guaranteed for refresh tokens. (Short access TTL is the standard mitigation.)
- Admin user creation is not yet exposed via API (seeding/ops task; role is data-driven).
- Throughput/load numbers intentionally TBD per SDD §10a / TSD §48 (Phase 6).
