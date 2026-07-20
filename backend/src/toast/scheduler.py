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
          -- CAST('*/5 * * * *' AS INTEGER) is 0, which would make a cron-style
          -- schedule due on every scan. Interval seconds only, until a cron
          -- parser exists to earn that column's documented flexibility.
          AND CAST(s.schedule AS INTEGER) > 0
          AND NOT EXISTS (
            SELECT 1 FROM jobs j
            WHERE j.stage = 'poll' AND j.state IN ('queued', 'running')
              AND j.payload ->> 'source_id' = s.id)
          -- 'dead' counts as an attempt, not as "never polled". It used to be
          -- neither done nor pending, so a source whose poll job exhausted its
          -- retries looked permanently overdue: the scheduler re-enqueued it on
          -- every 10-second scan, each one burning five more HTTP attempts
          -- against a feed that was already failing, forever, with the jobs
          -- table growing without bound. A dead poll now simply waits out the
          -- source's own schedule like any other attempt.
          AND COALESCE((
            SELECT MAX(unixepoch(COALESCE(j.claimed_at, j.created_at))) FROM jobs j
            WHERE j.stage = 'poll' AND j.state IN ('done', 'dead')
              AND j.payload ->> 'source_id' = s.id), 0)
              + CAST(s.schedule AS INTEGER) <= unixepoch(?)
        """,
        (utcnow(),),
    ).fetchall()


DAILY_S = 24 * 3600
# stage -> how often it should run. Re-attribution is frequent on purpose:
# incidents get listed through the day, and each new one can adopt facts that
# arrived before it existed.
DAILY_STAGES = {"backup": DAILY_S, "trust": DAILY_S, "reattribute": 30 * 60}


def stage_due(conn, stage: str, interval_s: int) -> bool:
    """True when this maintenance stage hasn't completed within its interval."""
    if conn.execute(
        "SELECT 1 FROM jobs WHERE stage = ? AND state IN ('queued','running')", (stage,)
    ).fetchone():
        return False
    row = conn.execute(
        """SELECT COALESCE(MAX(unixepoch(COALESCE(claimed_at, created_at))), 0) AS last
           FROM jobs WHERE stage = ? AND state IN ('done', 'dead')""",
        (stage,),
    ).fetchone()
    now = conn.execute("SELECT unixepoch(?) AS n", (utcnow(),)).fetchone()["n"]
    return (row["last"] or 0) + interval_s <= now


def scan_once(conn) -> int:
    log = get_logger("scheduler")
    n = 0
    for stage, interval_s in DAILY_STAGES.items():
        if stage_due(conn, stage, interval_s):
            job_id = jobs.enqueue(conn, stage, {}, priority=8)  # lowest priority
            log.info("maintenance_enqueued", stage=stage, job_id=job_id)
            n += 1
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
