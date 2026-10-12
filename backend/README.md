# WarrantyVault AI — Backend

FastAPI backend for WarrantyVault AI.

## Layout

```
app/
├── api/            HTTP layer (v1/ route modules, deps, envelope) — NO business logic
├── core/           config, security, logging, exceptions, rate limit, audit
├── db/             SQLAlchemy engine/session, declarative base
├── models/         ORM models
├── schemas/        Pydantic request/response schemas
├── repositories/   data-access helpers
├── services/       business logic (auth, documents, purchases, ...)
├── ai/             preprocessing / classification / ocr / extraction / matching
├── warranty/       sources / retrieval / rules
├── notifications/  email provider, notification service
├── claims/         claim + package generation
├── workers/        background job runner
└── main.py         app factory (middleware, error handlers, health)
```

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/alembic upgrade head           # apply migrations
.venv/bin/uvicorn app.main:app --port 8000
.venv/bin/python -m app.workers.run      # background worker
.venv/bin/pytest                         # tests (rebuilds warranty_vault_test from migrations)
.venv/bin/ruff check app tests           # lint
.venv/bin/mypy app                       # types
```
