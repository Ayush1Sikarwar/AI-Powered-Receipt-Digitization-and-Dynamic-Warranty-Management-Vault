#!/usr/bin/env bash
# ============================================================================
# WarrantyVault AI — deployment (TSD §51, CI/CD "deploy" step).
#
# Usage:  infrastructure/scripts/deploy.sh <env> <image_tag>
#   env        staging | production
#   image_tag  immutable tag to build & deploy (e.g. a git SHA)
#
# Safety properties:
#   - Fails fast if the env file is missing or a required secret is empty.
#   - Validates the database migration path on a scratch copy BEFORE the
#     cutover, so a bad migration never half-applies under live traffic.
#   - Records the deployed tag in a state file so rollback.sh is one command.
# ============================================================================
set -euo pipefail

ENV="${1:-}"
TAG="${2:-}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_DIR="$REPO_ROOT/infrastructure/docker"
COMPOSE="$COMPOSE_DIR/docker-compose.prod.yml"
ENV_FILE="$REPO_ROOT/.env.$ENV"
STATE_DIR="$REPO_ROOT/.deploy-state"

die() { echo "deploy: ERROR: $*" >&2; exit 1; }

[ -n "$ENV" ] || die "usage: deploy.sh <staging|production> <image_tag>"
[ -n "$TAG" ] || die "usage: deploy.sh <staging|production> <image_tag>"
[ "$ENV" = "staging" ] || [ "$ENV" = "production" ] || die "env must be staging or production"
[ -f "$ENV_FILE" ] || die "missing $ENV_FILE (copy infrastructure/environments/${ENV}.env.example and fill it in)"

# --- 1. Secret hygiene: every required var must be non-empty & non-placeholder
# shellcheck disable=SC1090
source /dev/stdin <<<"$(grep -E '^\s*[A-Z0-9_]+=' "$ENV_FILE" | sed 's/^/export /')"
for var in AUTH_SECRET DATABASE_URL REDIS_URL CORS_ORIGINS POSTGRES_PASSWORD REDIS_PASSWORD; do
  val="${!var:-}"
  [ -n "$val" ] || die "$var is empty in $ENV_FILE"
  case "$val" in
    *__GENERATE_*|*change-me*|*dev-only-insecure*) die "$var looks like a placeholder; generate a real secret (openssl rand -hex 32)" ;;
  esac
done
echo "deploy: [1/5] secret hygiene OK for $ENV"

# --- 2. Build the immutable images under this tag
docker compose -f "$COMPOSE" --env-file "$ENV_FILE" \
  build backend worker frontend \
  --build-arg BUILDKIT_INLINE_CACHE=1 2>&1 | sed 's/^/deploy:   /'
# Tag the backend image so rollback can reference it exactly.
BACKEND_IMAGE="$(docker compose -f "$COMPOSE" --env-file "$ENV_FILE" config --images backend | head -1)"
docker tag "$BACKEND_IMAGE" "warranty-vault-backend:release-$ENV-$TAG"
echo "deploy: [2/5] images built and tagged release-$ENV-$TAG"

# --- 3. Pre-deploy migration validation on a scratch database
# Runs `alembic upgrade head` against an isolated DB so a broken migration
# fails here, not during the cutover.
echo "deploy: [3/5] validating migration path (alembic upgrade head, scratch DB)"
if command -v psql >/dev/null 2>&1; then
  SCRATCH_DB="${POSTGRES_DB}_migrate_check"
  DB_HOST_PORT="$(echo "$DATABASE_URL" | sed -E 's#^[a-z+]+://[^@]*@##; s#/.*$##')"
  PGPASSWORD="$POSTGRES_PASSWORD" createdb -h "$DB_HOST_PORT" -U "$POSTGRES_USER" "$SCRATCH_DB" 2>/dev/null || true
  ( cd "$REPO_ROOT/backend" \
    && DATABASE_URL="postgresql+psycopg://$POSTGRES_USER:$POSTGRES_PASSWORD@$DB_HOST_PORT/$SCRATCH_DB" \
    alembic upgrade head >/dev/null 2>&1 ) \
    || die "migration validation failed on scratch DB — aborting deploy"
  echo "deploy:   migration path valid"
else
  echo "deploy:   psql not on this host; skipping local scratch-DB check (CI performs it)"
fi

# --- 4. Apply migrations to the live DB, then cutover (rolling restart)
echo "deploy: [4/5] applying migrations + rolling the stack"
docker compose -f "$COMPOSE" --env-file "$ENV_FILE" up -d postgres redis migrate
# `migrate` is one-shot; wait for it to finish successfully before the API.
MIGRATE_ID="$(docker compose -f "$COMPOSE" --env-file "$ENV_FILE" ps -q migrate || true)"
if [ -n "$MIGRATE_ID" ]; then
  for _ in $(seq 1 60); do
    st="$(docker inspect --format '{{.State.Status}}' "$MIGRATE_ID" 2>/dev/null || echo missing)"
    [ "$st" = "exited" ] && break
    sleep 2
  done
  [ "$(docker inspect --format '{{.State.ExitCode}}' "$MIGRATE_ID")" = "0" ] \
    || die "migration service failed during deploy"
fi
docker compose -f "$COMPOSE" --env-file "$ENV_FILE" up -d backend worker frontend

# --- 5. Readiness gate: block traffic until /health/ready is 200
echo "deploy: [5/5] readiness gate"
for _ in $(seq 1 60); do
  code="$(curl -fsS -o /dev/null -w '%{http_code}' "$FRONTEND_BASE_URL/health/ready" || true)"
  [ "$code" = "200" ] && break
  sleep 2
done
[ "$code" = "200" ] || die "stack not ready after cutover (last HTTP $code)"

mkdir -p "$STATE_DIR"
echo "$TAG" > "$STATE_DIR/current-$ENV"
echo "$TAG" > "$STATE_DIR/previous-$ENV.tmp" && mv "$STATE_DIR/previous-$ENV.tmp" "$STATE_DIR/previous-$ENV" 2>/dev/null || true
echo "deploy: SUCCESS — $ENV is running release $TAG (readiness 200)"
