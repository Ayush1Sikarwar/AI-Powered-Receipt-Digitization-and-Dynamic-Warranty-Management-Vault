# infrastructure/migrations

Versioned database migrations live in `backend/alembic/` (Alembic).

- `backend/alembic/env.py` — metadata from `app.models`, URL from app config.
- `backend/alembic/versions/` — migration history, 0001 → current head.

The test suite rebuilds the test database schema **from these migrations on
every run**, so migration integrity is verified continuously (CI included).
Apply locally: `cd backend && alembic upgrade head`.
