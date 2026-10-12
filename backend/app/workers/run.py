"""Worker entrypoint: `python -m app.workers.run`.

A separate long-running process polls the job table (NFR-05: processing is
separable from the web/API layer and scales independently). Each job type has
a handler; failures are retried with bounded attempts (TSD §31, §50).
"""

import logging
import signal
import time

from app.core.config import settings
from app.core.logging import setup_logging
from app.db.session import SessionLocal
from app.models.job import JobStatus
from app.workers import handlers
from app.workers.jobs import claim_next_job, complete_job, fail_job

logger = logging.getLogger(__name__)

_running = True


def _handle_signal(signum, _frame) -> None:
    global _running
    logger.info("worker_stop_signal sig=%s", signum)
    _running = False


def _beat() -> None:
    """Write a Redis heartbeat (worker monitoring, TSD §40/§51).

    ``/health/ready`` (with health_check_worker enabled) and /metrics read
    the age of this key to detect a dead worker. Best-effort: a Redis outage
    must not take the worker down.
    """
    from app.core.rate_limit import get_redis_client

    redis = get_redis_client()
    if redis is None:
        return
    try:
        redis.set(settings.worker_heartbeat_key, str(time.time()), ex=300)
    except Exception:  # noqa: BLE001
        logger.warning("worker_heartbeat_failed")


def main() -> None:
    setup_logging(settings.log_level)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    logger.info("worker_started")

    sweep_counter = 0
    alert_interval = max(1, settings.notification_sweep_seconds)
    while _running:
        _beat()
        db = SessionLocal()
        try:
            job = claim_next_job(db)
            if job is None:
                # Periodic retention sweep (SRS NFR-02): enqueue purges for
                # closed accounts whose retention window has elapsed.
                sweep_counter += 1
                if sweep_counter % 15 == 0:  # ~every 15s of idle time
                    from app.services.data_retention import retention_sweep

                    try:
                        retention_sweep(db)
                    except Exception:  # noqa: BLE001 - sweep must not kill the worker
                        logger.exception("retention_sweep_failed")
                # Warranty expiry alerts (SRS FR-15): schedule reminders,
                # cancel stale ones, send due notifications via the
                # email provider.
                if sweep_counter % alert_interval == 0:
                    from app.services.notification_service import notification_sweep

                    try:
                        result = notification_sweep(db)
                        if any(result.values()):
                            logger.info("notification_sweep %s", result)
                    except Exception:  # noqa: BLE001 - sweep must not kill the worker
                        logger.exception("notification_sweep_failed")
                time.sleep(1.0)
                continue
            handler = handlers.HANDLERS.get(job.type)
            if handler is None:
                fail_job(db, job, error=f"no handler for {job.type}", error_code="PROCESSING_ERROR")
                continue
            try:
                handler(db, job)
                # Handlers may re-queue the job (retention hold): it is no
                # longer RUNNING, so completing it would corrupt the schedule.
                if job.status == JobStatus.RUNNING:
                    complete_job(db, job)
                    logger.info("job_completed job_id=%s type=%s", job.id, job.type)
                else:
                    logger.info(
                        "job_rescheduled job_id=%s type=%s next_at=%s",
                        job.id, job.type, job.scheduled_at,
                    )
            except Exception as exc:  # noqa: BLE001
                from app.core.exceptions import AppError

                code = exc.error_code if isinstance(exc, AppError) else "PROCESSING_ERROR"
                fail_job(db, job, error=str(exc)[:2000], error_code=code)
        except Exception:  # noqa: BLE001 - keep the worker alive
            logger.exception("worker_loop_error")
        finally:
            db.close()

    logger.info("worker_stopped")


if __name__ == "__main__":
    main()
