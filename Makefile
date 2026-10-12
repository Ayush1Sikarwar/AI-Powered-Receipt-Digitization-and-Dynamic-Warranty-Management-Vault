.PHONY: help install migrate revision test test-backend test-frontend lint backend worker frontend security-scan backup readiness

help:
	@echo "WarrantyVault AI — common tasks"
	@echo "  make install        Install backend (venv) + frontend deps"
	@echo "  make migrate        Apply Alembic migrations (dev DB)"
	@echo "  make revision MSG=  Create a new Alembic migration"
	@echo "  make test           Run backend + frontend tests"
	@echo "  make lint           Run linters / type checkers"
	@echo "  make security-scan  Dependency + secret scanning (pip-audit, npm audit)"
	@echo "  make backup ENV=    Run a backup set (ENV=production|staging)"
	@echo "  make readiness      Run the production-readiness test suite"
	@echo "  make backend        Run the API on :8000"
	@echo "  make worker         Run the background worker"
	@echo "  make frontend       Run the Vite dev server on :5173"

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
	cd frontend && npm install

migrate:
	cd backend && .venv/bin/alembic upgrade head

revision:
	cd backend && .venv/bin/alembic revision --autogenerate -m "$(MSG)"

test: test-backend test-frontend

test-backend:
	cd backend && .venv/bin/pytest

test-frontend:
	cd frontend && npx vitest run

lint:
	cd backend && .venv/bin/ruff check app tests
	cd backend && .venv/bin/mypy app
	cd frontend && npx tsc -b --noEmit

backend:
	cd backend && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

worker:
	cd backend && .venv/bin/python -m app.workers.run

frontend:
	cd frontend && npm run dev

security-scan:
	cd backend && .venv/bin/pip-audit
	cd frontend && npm audit --audit-level=high
	@echo "security-scan: complete"

backup:
	test -n "$(ENV)" || (echo "usage: make backup ENV=production|staging" && exit 2)
	infrastructure/scripts/backup.sh $(ENV)

readiness:
	cd backend && .venv/bin/python scripts/production_readiness_test.py
