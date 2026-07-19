"""Scheduler — enqueues poll jobs for due sources.

Phase 0: the loop, health endpoint, and the sources scan are real; there are
no source adapters yet, so an enabled source with a schedule simply produces a
poll job that dead-letters against the (not yet registered) 'poll' handler.
Phase 1 registers real adapters and this file gains schedule parsing beyond
plain interval seconds.
"""

from __future__ import annotations

import time

from toast.core.config import Settings
from toast.core.db import apply_migrations, connect, utcnow
from toast.core.health import HealthState, serve_health
from toast.core.logging import configure_logging, get_logger
from toast.queue import jobs

SCAN_INTERVAL_S = 10.0


def due_sources(conn) -> list:
    """Enabled sources with an interval schedule (integer seconds) that are due.

    A source is due when it has no queued/running poll job and its last poll
    job finished longer ago than its interval.
    """
    return conn.execute(
        """
        SELECT s.id, s.name, s.adapter, CAST(s.schedule AS INTEGER) AS interval_s
        FROM sources s
        WHERE s.enabled = 1 AND s.schedule IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM jobs j
            WHERE j.stage = 'poll' AND j.state IN ('queued', 'running')
              AND j.payload ->> 'source_id' = s.id)
          AND COALESCE((
            SELECT MAX(unixepoch(j.claimed_at)) FROM jobs j
            WHERE j.stage = 'poll' AND j.state = 'done'
              AND j.payload ->> 'source_id' = s.id), 0)
              + CAST(s.schedule AS INTEGER) <= unixepoch(?)
        """,
        (utcnow(),),
    ).fetchall()


def scan_once(conn) -> int:
    log = get_logger("scheduler")
    n = 0
    for src in due_sources(conn):
        job_id = jobs.enqueue(conn, "poll", {"source_id": src["id"], "adapter": src["adapter"]})
        log.info("poll_enqueued", source=src["name"], source_id=src["id"], job_id=job_id)
        n += 1
    return n


def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level, service="scheduler")
    log = get_logger("scheduler")
    conn = connect(settings.toast_db_path)
    apply_migrations(conn)

    state = HealthState("scheduler")
    if settings.health_port:
        serve_health(state, settings.health_port, bind="0.0.0.0")  # noqa: S104 - container-internal

    log.info("scheduler_started", db=str(settings.toast_db_path))
    while True:
        state.beat()
        scan_once(conn)
        time.sleep(SCAN_INTERVAL_S)


if __name__ == "__main__":
    main()
