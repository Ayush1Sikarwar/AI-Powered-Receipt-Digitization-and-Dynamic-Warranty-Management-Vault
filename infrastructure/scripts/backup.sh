#!/usr/bin/env bash
# ============================================================================
# WarrantyVault AI — scheduled backup (TSD §50 reliability / data backups).
#
# Wraps backend/scripts/backup.py (pg_dump -Fc + storage tar + manifest with
# sha256 + retention via --keep). Runs as a compose profile (`backup`) or as
# a cron job. Output goes to $BACKUP_DIR (a volume in compose).
#
# Usage:  infrastructure/scripts/backup.sh <env> [KEEP_DAYS]
# ============================================================================
set -euo pipefail

ENV="${1:-production}"
KEEP="${2:-14}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$REPO_ROOT/.env.$ENV"
BACKUP_DIR="${BACKUP_DIR:-$REPO_ROOT/data/backups/$ENV}"

die() { echo "backup: ERROR: $*" >&2; exit 1; }
[ -f "$ENV_FILE" ] || die "missing $ENV_FILE"
set -a; source "$ENV_FILE"; set +a
mkdir -p "$BACKUP_DIR"

# Reuse the app's own credentials (already least-privilege). The backup needs
# a DB that can pg_dump; use DATABASE_URL's host/user via psql env vars.
export PGUSER="$POSTGRES_USER"
export PGPASSWORD="$POSTGRES_PASSWORD"
DB_REF="$(echo "$DATABASE_URL" | sed -E 's#^[a-z+]+://[^@]*@##')"
HOST="$(echo "$DB_REF" | cut -d/ -f1)"
DBNAME="$(echo "$DB_REF" | sed -E 's#^.*/##')"

STORAGE_ROOT="${STORAGE_LOCAL_ROOT:-$REPO_ROOT/backend/data/storage}"

echo "backup: $ENV -> $BACKUP_DIR (keep $KEEP sets) at $(date -u +%FT%TZ)"
( cd "$REPO_ROOT/backend" \
  && python scripts/backup.py \
       --db-url "postgresql://$PGUSER@$HOST/$DBNAME" \
       --storage "$STORAGE_ROOT" \
       --out "$BACKUP_DIR" \
       --keep "$KEEP" )

echo "backup: SUCCESS"
