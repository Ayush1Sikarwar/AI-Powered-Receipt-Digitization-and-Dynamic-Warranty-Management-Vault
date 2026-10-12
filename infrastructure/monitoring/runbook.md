# WarrantyVault AI — Operations Runbook

Companion to `prometheus.yml` / `alert_rules.yml` (TSD §40/§50/§51).

## 1. Backup & restore (TSD §50)

**Schedule:** nightly via the compose `backup` profile
(`docker compose -f infrastructure/docker/docker-compose.prod.yml --env-file .env.production --profile backup up -d`)
or `infrastructure/scripts/backup.sh production` from cron. Retention: 14 sets
(configurable, `--keep`).

**A backup set** (under `data/backups/<env>/backup_<ts>/`):
- `database.dump` — `pg_dump -Fc` custom format
- `storage.tar.gz` — object-storage tree (originals + derivatives)
- `manifest.json` — db name, per-table counts, storage stats, sha256 of both files

**Restore procedure** (rehearsed — see `backend/scripts/restore.py`):

```bash
# 1. Stop writers (API + worker) so the DB is quiescent.
docker compose -f infrastructure/docker/docker-compose.prod.yml \
  --env-file .env.production stop backend worker

# 2. Restore the database (refuses a non-empty target without --force).
cd backend
python scripts/restore.py \
  --set ../data/backups/production/backup_<ts> \
  --db-url "$DATABASE_URL" --force

# 3. Restore the storage tree (explicit target, never in-place).
python scripts/restore.py \
  --set ../data/backups/production/backup_<ts> \
  --storage-target /srv/data/storage-restore

# 4. Verify the manifest sha256s, start the stack, hit /health/ready.
docker compose ... start backend worker
```

**Point-in-time caveat:** backups are nightly snapshots, not continuous
WAL archiving. For lower RPO enable `archive_command` to object storage.

## 2. Deploy & rollback

- **Deploy:** `infrastructure/scripts/deploy.sh <env> <git-sha>` — secret
  hygiene → immutable image build → scratch-DB migration validation → live
  `alembic upgrade head` → rolling `up -d` → readiness gate (blocks traffic
  until `/health/ready` is 200).
- **Rollback:** `infrastructure/scripts/rollback.sh <env>` — re-cuts over to
  the previously recorded release tag (images are immutable per tag).
- **Schema rollback:** if the failed release applied a migration, run
  `alembic downgrade -1` on the live DB (validate on a copy first), then
  roll back the image. Every migration ships with a `downgrade()`.
- **Environments are separate:** staging and production have distinct env
  files, databases, Redis passwords, S3 buckets, and CORS origins. Deploy to
  staging on every `main` push; production is manual (`workflow_dispatch`)
  and requires the staging smoke pass.

## 3. TLS

- Staging/local: `infrastructure/nginx/generate-certs.sh` (self-signed, 365d).
- Production: CA-issued `fullchain.pem` + `privkey.pem` (0600) mounted at
  `/etc/nginx/certs`. Rotate before expiry (Let's Encrypt: ~60d before);
  after replacing certs, `docker exec <frontend> nginx -s reload`.

## 4. Alerts

| Alert | First response |
|---|---|
| WorkerHeartbeatMissing | `docker compose ps worker`; restart worker. If it crash-loops, check worker logs (structured JSON) and the jobs table for a poison job (`/admin/failed-processing`). |
| ReadinessDegraded | `/health/ready` JSON names the failing check (database/worker/queue). Follow that check. |
| JobQueueBacklog | Scale worker replicas (compose `--scale worker=N`); check for handler errors in logs. |
| DeadJobsIncreasing | Open `/admin/failed-processing`, fix the cause, `POST /admin/failed-processing/documents/{id}/retry`. |
| High5xxRate | Triage by path via `/metrics` request counters; correlate with structured error logs (request_id). |
| ApiP95High | Check `log_min_duration_statement` slow queries in postgres logs; check Redis latency; load-test to reproduce. |
| BackupNotFresh | Run `infrastructure/scripts/backup.sh production` manually; inspect the backup log; verify the `backups` volume disk space. |

## 5. Log & metric sources

- **API/worker:** JSON structured logs (NFR-07) with `request_id`; API
  latency per request; error events with context.
- **Edge:** nginx JSON access logs.
- **Metrics:** `/metrics` (Prometheus text format) — request counters by
  path/status, latency histogram, uptime, jobs by status, document counts,
  worker heartbeat age. Scraped by `prometheus.yml`.
- **Queue monitoring:** the job queue is the `background_jobs` table; depth
  and DEAD count are gauges in `/metrics` and in `/health/ready`.
