#!/usr/bin/env bash
# ============================================================================
# WarrantyVault AI — rollback (CI/CD "rollback" step).
#
# Usage:  infrastructure/scripts/rollback.sh <env>
#
# Re-deploys the previously recorded release tag (state kept by deploy.sh in
# .deploy-state/). Images are immutable per tag, so rollback is a compose
# cutover to the last known-good build — no rebuild, no new migration
# forward-pass (a rollback to an earlier schema requires an explicit
# `alembic downgrade`, which the operator runs and validates manually;
# see infrastructure/monitoring/runbook.md).
# ============================================================================
set -euo pipefail

ENV="${1:-}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE="$REPO_ROOT/infrastructure/docker/docker-compose.prod.yml"
ENV_FILE="$REPO_ROOT/.env.$ENV"
STATE_DIR="$REPO_ROOT/.deploy-state"

die() { echo "rollback: ERROR: $*" >&2; exit 1; }

[ -n "$ENV" ] || die "usage: rollback.sh <staging|production>"
[ "$ENV" = "staging" ] || [ "$ENV" = "production" ] || die "env must be staging or production"
[ -f "$ENV_FILE" ] || die "missing $ENV_FILE"
[ -f "$STATE_DIR/previous-$ENV" ] || die "no previous release recorded in $STATE_DIR/previous-$ENV"

# Pull FRONTEND_BASE_URL (and any other vars the readiness gate needs).
set -a; source "$ENV_FILE"; set +a

TAG="$(cat "$STATE_DIR/previous-$ENV")"
CUR="$(cat "$STATE_DIR/current-$ENV" 2>/dev/null || echo unknown)"
echo "rollback: $ENV $CUR -> $TAG"

# Bring the previous release's stack back. The compose file tags images by
# service; rollback re-runs the same compose with the previous tag baked in
# by re-pointing the release image and restarting the app services.
docker compose -f "$COMPOSE" --env-file "$ENV_FILE" up -d postgres redis
# Rebuild/point backend+worker at the previous release tag and roll.
docker tag "warranty-vault-backend:release-$ENV-$TAG" \
  "$(docker compose -f "$COMPOSE" --env-file "$ENV_FILE" config --images backend | head -1)" \
  || die "release image release-$ENV-$TAG not present on this host"
docker compose -f "$COMPOSE" --env-file "$ENV_FILE" up -d --force-recreate backend worker frontend

# Readiness gate.
for _ in $(seq 1 60); do
  code="$(curl -fsS -o /dev/null -w '%{http_code}' "$FRONTEND_BASE_URL/health/ready" 2>/dev/null || true)"
  [ "$code" = "200" ] && break
  sleep 2
done
[ "$code" = "200" ] || die "stack not ready after rollback (last HTTP $code)"

# Update state: the rolled-back tag becomes current; the failed one becomes previous.
echo "$TAG" > "$STATE_DIR/current-$ENV"
echo "$CUR" > "$STATE_DIR/previous-$ENV"
echo "rollback: SUCCESS — $ENV is running release $TAG (readiness 200)"
