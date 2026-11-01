"""Worker loop — claims jobs, dispatches to handlers, records outcomes.

One process, one SQLite connection. Restart-safe: an unfinished 'running' job
from a crashed worker is requeued by requeue_stale() on any worker's startup.
"""

from __future__ import annotations

import os
import socket
import time

import toast.pipeline.handlers  # noqa: F401 - registers poll/normalize handlers
from toast.core.config import Settings
from toast.core.db import connect, utcnow
from toast.core.health import HealthState, serve_health
from toast.core.logging import configure_logging, get_logger
from toast.llm.client import LLMModelMissing
from toast.queue import handlers, jobs

STALE_RUNNING_AFTER_S = 300
STALE_SWEEP_S = 60.0


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


def requeue_own(conn, wid: str) -> int:
    """Requeue jobs claimed under our own id. A restarted container keeps its
    hostname and pid, so these belonged to the process we just replaced."""
    cur = conn.execute(
        "UPDATE jobs SET state='queued', claimed_by=NULL, claimed_at=NULL"
        " WHERE state='running' AND claimed_by=?",
        (wid,),
    )
    return cur.rowcount


def sweep_if_due(conn, *, last_sweep: float, now: float) -> float:
    """Free abandoned jobs while running, not only at startup. A job left 'running'
    blocks its stage: stage_due() refuses to enqueue another. The loop calls this
    between jobs, and compose runs one worker, so nothing live is being swept.
    Returns the time of the last sweep."""
    if now - last_sweep < STALE_SWEEP_S:
        return last_sweep
    requeue_stale(conn)
    return now


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
    except LLMModelMissing as exc:
        jobs.wait_for_model(conn, job, str(exc))
        log.info("job_waiting_for_model", job_id=job["id"], stage=job["stage"], error=str(exc))
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
    requeued = requeue_own(conn, wid) + requeue_stale(conn)
    if requeued:
        log.info("requeued_stale_jobs", count=requeued)

    state = HealthState("worker")
    if settings.health_port:
        serve_health(state, settings.health_port, bind="0.0.0.0")  # noqa: S104 - container-internal

    log.info("worker_started", worker_id=wid, db=str(settings.toast_db_path))
    last_sweep = time.monotonic()
    while True:
        state.beat()
        last_sweep = sweep_if_due(conn, last_sweep=last_sweep, now=time.monotonic())
        if not run_once(conn, wid, settings):
            time.sleep(settings.worker_poll_interval_s)


if __name__ == "__main__":
    main()
