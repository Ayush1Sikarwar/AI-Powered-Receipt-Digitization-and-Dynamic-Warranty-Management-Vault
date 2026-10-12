# WarrantyVault AI — production backend image (API and worker share it;
# the entrypoint differs: uvicorn vs `python -m app.workers.run`).
#
# Hardening (TSD §51):
# - non-root runtime user (least privilege)
# - no dev extras, no source outside the app tree
# - image healthcheck (liveness; readiness is probed at /health/ready)
# - tesseract + glib required by the OCR/enhancement pipeline
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends tesseract-ocr tesseract-ocr-eng libglib2.0-0 curl && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd --system app && useradd --system --gid app --home-dir /srv --shell /usr/sbin/nologin app

WORKDIR /srv

# Install dependencies first (layer cache) — runtime deps only.
COPY backend/pyproject.toml backend/README.md* ./
RUN pip install --no-cache-dir .

# Application code (read-only at runtime via the container filesystem default).
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY backend/scripts ./scripts

# Migrations run as a separate compose step (migrate service) or via
# `docker compose run --rm backend alembic upgrade head`.
ENV PYTHONPATH=/srv
USER app
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8000/health/ready || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
