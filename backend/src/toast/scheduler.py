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
DAILY_STAGES = {
    "backup": DAILY_S,
    "trust": DAILY_S,
    "reattribute": 30 * 60,
    # every 2h: the raw archive grows ~25 MB/day and the first sync has a
    # thousand-blob backlog, so it drains in bounded batches rather than one run
    "raw_sync": 2 * 3600,
    # aircraft move fast — a tanker orbit is only legible if sampled often
    "aircraft": 60,
    # the SDR inbox: cheap directory scan; calls land seconds after capture
    "radio_ingest": 30,
    # scheduled releases: a minute of slack on a scheduled post is fine
    "release_due": 60,
    # is every active fire still burning? official-inactive auto-closes;
    # everything else becomes an operator proposal
    "activity_check": 1800,
    "jobs_prune": 3600,
    # EVACUATIONS ARE TIME-SENSITIVE. The Cal OES zone layer is the authoritative,
    # self-correcting source for orders/warnings, and it is what drives the pin,
    # the counts, the bell and the Telegram alert. It rebuilds every ~5 min at
    # source, so we pull it every 2 min — proactively, not only when a news fact
    # happens to fire a sync or the map tab happens to be open — so a new order or
    # a reduction is reflected within ~2 min of the county publishing it. Runs at
    # ingest priority (see STAGE_PRIORITY), never behind the maintenance backlog.
    "evac_zone_sync": 120,
    # the phone hub: alerts out every 30 s, button presses picked up every 15 s
    "telegram_notify": 30,
    "telegram_poll": 15,
    # silence is the failure mode that looks like good news — check every 5 min
    "watchdog": 300,
    # weather changes slowly and NWS asks for restraint — every 20 min keeps the
    # wind layer fresh enough for fire behaviour without hammering the API
    "weather_refresh": 20 * 60,
}


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


# Most maintenance stages run at the lowest priority (8) — they can wait behind
# live ingest. A few cannot: an evacuation refresh is a life-safety path and must
# jump the queue, so it rides at ingest priority (2), the same as a reactive
# evac sync fired by a fresh order.
# Priority is ASCENDING — 2 runs before 5 runs before 8 (see queue/jobs.claim).
# Everything not listed here lands on 8, BEHIND poll (5), extract (6) and embed
# (7). That is fine for true maintenance, and fatal for a live feed: a backlog
# of LLM extract/embed work starved the 60-second aircraft poller for hours, and
# because stage_due() refuses to enqueue while one is already queued, the single
# stranded job silently killed the stage instead of merely delaying it.
#
# So the cheap, time-critical, fast-cadence stages sit in the ingest tier with
# the evacuation sync. They are seconds of work each; they must never queue
# behind a model.
STAGE_PRIORITY = {
    "evac_zone_sync": 2,  # evacuations outrank everything
    "aircraft": 3,  # 60 s cadence — a tanker orbit is only legible if sampled
    "radio_ingest": 3,  # 30 s — calls land seconds after capture
    "telegram_poll": 3,  # 15 s — operator button presses
    "watchdog": 3,  # it exists to report a clogged queue, so it can't wait in it
    "telegram_notify": 3,  # 30 s — alerts out
    "release_due": 3,  # 60 s — scheduled posts go out on time
    "weather_refresh": 4,  # 20 min, but cheap and feeds the wind layer
}


def scan_once(conn) -> int:
    log = get_logger("scheduler")
    n = 0
    for stage, interval_s in DAILY_STAGES.items():
        if stage_due(conn, stage, interval_s):
            priority = STAGE_PRIORITY.get(stage, 8)
            job_id = jobs.enqueue(conn, stage, {}, priority=priority)
            log.info("maintenance_enqueued", stage=stage, job_id=job_id, priority=priority)
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
