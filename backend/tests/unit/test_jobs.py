from toast.core.config import Settings
from toast.queue import jobs
from toast.queue.worker import requeue_stale, run_once

# Long backoff so a just-failed job is deterministically not-yet-runnable;
# tests that need re-runs clear run_after explicitly.
SETTINGS = Settings(job_backoff_base_s=60.0, worker_poll_interval_s=0.01)


def test_enqueue_claim_complete(db):
    job_id = jobs.enqueue(db, "demo_noop", {"n": 1})
    job = jobs.claim(db, "w1")
    assert job["id"] == job_id and job["state"] == "running" and job["attempts"] == 1
    jobs.complete(db, job_id)
    assert jobs.counts_by_state(db) == {"done": 1}


def test_claim_respects_priority_then_fifo(db):
    low = jobs.enqueue(db, "demo_noop", {}, priority=9)
    urgent = jobs.enqueue(db, "demo_noop", {}, priority=1)
    urgent2 = jobs.enqueue(db, "demo_noop", {}, priority=1)
    assert jobs.claim(db, "w1")["id"] == urgent
    assert jobs.claim(db, "w1")["id"] == urgent2
    assert jobs.claim(db, "w1")["id"] == low
    assert jobs.claim(db, "w1") is None


def test_claim_skips_future_run_after(db):
    jobs.enqueue(db, "demo_noop", {}, run_after="2999-01-01T00:00:00.000Z")
    assert jobs.claim(db, "w1") is None


def test_fail_requeues_with_backoff_then_dead_letters(db):
    job_id = jobs.enqueue(db, "demo_noop", {}, max_attempts=2)

    job = jobs.claim(db, "w1")
    assert jobs.fail(db, job, "boom", backoff_base_s=0.001) == "queued"
    row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["state"] == "queued" and row["last_error"] == "boom"
    assert row["run_after"] is not None

    db.execute("UPDATE jobs SET run_after=NULL WHERE id=?", (job_id,))
    job = jobs.claim(db, "w1")
    assert job["attempts"] == 2
    assert jobs.fail(db, job, "boom2", backoff_base_s=0.001) == "dead"
    row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["state"] == "dead" and row["last_error"] == "boom2"  # preserved for inspection


def test_run_once_success_and_unknown_stage(db):
    jobs.enqueue(db, "demo_noop", {})
    assert run_once(db, "w1", SETTINGS) is True
    assert jobs.counts_by_state(db)["done"] == 1

    jobs.enqueue(db, "no_such_stage", {})
    assert run_once(db, "w1", SETTINGS) is True
    row = db.execute("SELECT * FROM jobs WHERE stage='no_such_stage'").fetchone()
    assert row["state"] == "queued" and "no handler" in row["last_error"]

    assert run_once(db, "w1", SETTINGS) is False  # nothing runnable (backoff in future)


def test_flaky_handler_retries_to_success(db):
    job_id = jobs.enqueue(
        db, "demo_flaky", {"job_id": "self", "succeed_on_attempt": 3}, max_attempts=5
    )
    db.execute("UPDATE jobs SET payload=json_set(payload,'$.job_id',id) WHERE id=?", (job_id,))
    for _ in range(5):
        db.execute("UPDATE jobs SET run_after=NULL WHERE id=?", (job_id,))
        run_once(db, "w1", SETTINGS)
    row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["state"] == "done" and row["attempts"] == 3


def test_requeue_stale_running_jobs(db):
    job_id = jobs.enqueue(db, "demo_noop", {})
    jobs.claim(db, "w-crashed")
    db.execute("UPDATE jobs SET claimed_at='2020-01-01T00:00:00.000Z' WHERE id=?", (job_id,))
    assert requeue_stale(db) == 1
    row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["state"] == "queued" and row["claimed_by"] is None
