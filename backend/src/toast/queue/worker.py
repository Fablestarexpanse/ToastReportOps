"""Worker loop — claims jobs, dispatches to handlers, records outcomes.

One process, one SQLite connection. Restart-safe: an unfinished 'running' job
from a crashed worker is requeued by requeue_stale() on any worker's startup.
"""

from __future__ import annotations

import os
import socket
import time

from toast.core.config import Settings
from toast.core.db import connect, utcnow
from toast.core.health import HealthState, serve_health
from toast.core.logging import configure_logging, get_logger
from toast.queue import handlers, jobs

STALE_RUNNING_AFTER_S = 300


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def requeue_stale(conn, older_than_s: int = STALE_RUNNING_AFTER_S) -> int:
    """Requeue 'running' jobs whose claim is older than the threshold (crashed worker)."""
    cutoff = utcnow()
    cur = conn.execute(
        """UPDATE jobs SET state='queued', claimed_by=NULL, claimed_at=NULL
           WHERE state='running'
             AND unixepoch(?) - unixepoch(claimed_at) > ?""",
        (cutoff, older_than_s),
    )
    return cur.rowcount


def run_once(conn, wid: str, settings: Settings) -> bool:
    """Claim and process one job. Returns True if a job was processed."""
    log = get_logger("worker")
    job = jobs.claim(conn, wid)
    if job is None:
        return False
    handler = handlers.get(job["stage"])
    if handler is None:
        jobs.fail(
            conn,
            job,
            f"no handler for stage {job['stage']!r}",
            backoff_base_s=settings.job_backoff_base_s,
        )
        log.warning("job_no_handler", job_id=job["id"], stage=job["stage"])
        return True
    try:
        handler(conn, handlers.payload_of(job))
    except Exception as exc:
        state = jobs.fail(conn, job, repr(exc), backoff_base_s=settings.job_backoff_base_s)
        log.warning(
            "job_failed",
            job_id=job["id"],
            stage=job["stage"],
            attempts=job["attempts"],
            new_state=state,
            error=repr(exc),
        )
    else:
        jobs.complete(conn, job["id"])
        log.info("job_done", job_id=job["id"], stage=job["stage"], attempts=job["attempts"])
    return True


def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level, service="worker")
    log = get_logger("worker")
    wid = worker_id()
    conn = connect(settings.toast_db_path)

    from toast.core.db import apply_migrations

    apply_migrations(conn)
    requeued = requeue_stale(conn)
    if requeued:
        log.info("requeued_stale_jobs", count=requeued)

    state = HealthState("worker")
    if settings.health_port:
        serve_health(state, settings.health_port, bind="0.0.0.0")  # noqa: S104 - container-internal

    log.info("worker_started", worker_id=wid, db=str(settings.toast_db_path))
    while True:
        state.beat()
        if not run_once(conn, wid, settings):
            time.sleep(settings.worker_poll_interval_s)


if __name__ == "__main__":
    main()
