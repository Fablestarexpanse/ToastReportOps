"""Queue fairness: a live feed must never be starved by a batch backlog.

This is a regression test with a date on it. A standing backlog of LLM extract
(priority 6) and embed (7) work held the 60-second aircraft poller off the CPU
for three hours; because scheduler.stage_due() will not enqueue while one job is
already queued, that single stranded job killed the stage outright rather than
merely delaying it. The map simply stopped showing aircraft.
"""

from toast.core.db import apply_migrations, connect
from toast.queue import jobs


def _db():
    conn = connect(":memory:")
    apply_migrations(conn)
    return conn


def _age(conn, job_id: str, seconds: int) -> None:
    conn.execute(
        "UPDATE jobs SET created_at = datetime('now', ?) WHERE id = ?",
        (f"-{seconds} seconds", job_id),
    )


def test_realtime_feed_runs_before_a_batch_backlog():
    conn = _db()
    for _ in range(10):
        jobs.enqueue(conn, "extract", {}, priority=6)
    jobs.enqueue(conn, "aircraft", {}, priority=3)
    assert jobs.claim(conn, "w1")["stage"] == "aircraft"


def test_evacuations_still_outrank_every_other_feed():
    conn = _db()
    jobs.enqueue(conn, "aircraft", {}, priority=3)
    jobs.enqueue(conn, "evac_zone_sync", {}, priority=2)
    assert jobs.claim(conn, "w1")["stage"] == "evac_zone_sync"


def test_a_starved_job_is_eventually_promoted():
    conn = _db()
    # a low-priority maintenance job that has been waiting a long time
    stuck = jobs.enqueue(conn, "raw_sync", {}, priority=8)
    _age(conn, stuck, jobs.AGING_AFTER_S + 60)
    # ...against a fresh batch backlog that would otherwise always win
    for _ in range(5):
        jobs.enqueue(conn, "extract", {}, priority=6)
    assert jobs.claim(conn, "w1")["stage"] == "raw_sync"


def test_aging_never_lets_a_stale_job_cut_ahead_of_an_evacuation():
    conn = _db()
    stuck = jobs.enqueue(conn, "raw_sync", {}, priority=8)
    _age(conn, stuck, AGING := jobs.AGING_AFTER_S * 10)
    assert AGING > jobs.AGING_AFTER_S
    jobs.enqueue(conn, "evac_zone_sync", {}, priority=2)
    # however long the backlog has waited, an evacuation goes first
    assert jobs.claim(conn, "w1")["stage"] == "evac_zone_sync"


def test_fresh_low_priority_work_still_yields_to_the_pipeline():
    conn = _db()
    jobs.enqueue(conn, "raw_sync", {}, priority=8)
    jobs.enqueue(conn, "extract", {}, priority=6)
    # nothing has aged yet, so normal priority order holds
    assert jobs.claim(conn, "w1")["stage"] == "extract"
