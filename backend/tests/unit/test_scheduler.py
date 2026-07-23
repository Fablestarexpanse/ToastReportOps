"""The scheduler must treat an evacuation refresh as time-sensitive: scheduled
on a tight cadence and enqueued ahead of the maintenance backlog."""

from toast import scheduler


def test_evac_zone_sync_is_scheduled_and_frequent():
    assert scheduler.DAILY_STAGES["evac_zone_sync"] == 120  # ~2 min, life-safety


def test_evac_zone_sync_runs_at_ingest_priority():
    # lower number = higher priority; maintenance defaults to 8, evac jumps it
    assert scheduler.STAGE_PRIORITY["evac_zone_sync"] == 2
    assert scheduler.STAGE_PRIORITY["evac_zone_sync"] < 8


def test_scan_enqueues_evac_sync_ahead_of_maintenance(db):
    # a fresh DB has never run any stage, so every one is due
    scheduler.scan_once(db)
    row = db.execute(
        "SELECT priority FROM jobs WHERE stage = 'evac_zone_sync'"
    ).fetchone()
    assert row is not None, "evac_zone_sync was not enqueued"
    assert row["priority"] == 2
    # a plain maintenance stage stays at the low priority, proving evac jumped it
    backup = db.execute("SELECT priority FROM jobs WHERE stage = 'backup'").fetchone()
    assert backup is not None and backup["priority"] == 8
