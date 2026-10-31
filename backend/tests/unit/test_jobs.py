from toast.core.config import Settings
from toast.queue import jobs
from toast.queue.worker import STALE_SWEEP_S, requeue_own, requeue_stale, run_once, sweep_if_due

# Long backoff so a just-failed job is deterministically not-yet-runnable;
# tests that need re-runs clear run_after explicitly.
SETTINGS = Settings(job_backoff_base_s=60.0, worker_poll_interval_s=0.01)


def test_restart_frees_this_workers_own_job_however_young(db):
    """A restarted container keeps its hostname and pid, so a claim under our own
    id cannot be running: this process has only just started."""
    job_id = jobs.enqueue(db, "demo_noop", {})
    jobs.claim(db, "host:1")
    other = jobs.enqueue(db, "demo_noop", {})
    jobs.claim(db, "other:1")

    assert requeue_own(db, "host:1") == 1
    state = {r["id"]: r["state"] for r in db.execute("SELECT id, state FROM jobs")}
    assert state == {job_id: "queued", other: "running"}


def test_sweep_runs_only_when_due_and_frees_abandoned_jobs(db):
    job_id = jobs.enqueue(db, "demo_noop", {})
    jobs.claim(db, "gone:1")
    db.execute("UPDATE jobs SET claimed_at = '2020-01-01T00:00:00.000Z' WHERE id = ?", (job_id,))

    assert sweep_if_due(db, last_sweep=100.0, now=100.0 + STALE_SWEEP_S - 1) == 100.0
    assert db.execute("SELECT state FROM jobs").fetchone()["state"] == "running"

    assert sweep_if_due(db, last_sweep=100.0, now=100.0 + STALE_SWEEP_S) == 100.0 + STALE_SWEEP_S
    assert db.execute("SELECT state FROM jobs").fetchone()["state"] == "queued"


def test_enqueue_claim_complete(db):
    job_id = jobs.enqueue(db, "demo_noop", {"n": 1})
    job = jobs.claim(db, "w1")
    assert job["id"] == job_id and job["state"] == "running" and job["attempts"] == 1
    jobs.complete(db, job_id)
    assert jobs.counts_by_state(db) == {"done": 1, "dead_recent": 0}


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


class TestDeadLetterRecovery:
    """A dead job is preserved 'for inspection' — until this, nothing inspected
    it. A transient LLM outage silently cost 20 articles' claims, including two
    new-fire reports, with no signal and no way to retry."""

    def _dead(self, db, stage="extract", error="ConnectError('Network is unreachable')"):
        job_id = jobs.enqueue(db, stage, {"envelope_id": "e1"})
        job = jobs.claim(db, "w1")
        db.execute("UPDATE jobs SET attempts = max_attempts WHERE id = ?", (job_id,))
        job = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert jobs.fail(db, job, error) == "dead"
        return job_id

    def test_dead_jobs_are_visible_with_their_reason(self, db):
        self._dead(db)
        summary = jobs.dead_summary(db)
        assert summary["by_stage"][0]["stage"] == "extract"
        assert summary["by_stage"][0]["n"] == 1
        assert "unreachable" in summary["jobs"][0]["last_error"]

    def test_requeue_revives_with_a_fresh_attempt_budget(self, db):
        self._dead(db)
        assert jobs.requeue_dead(db, stage="extract") == 1
        revived = db.execute("SELECT state, attempts FROM jobs").fetchone()
        assert (revived["state"], revived["attempts"]) == ("queued", 0)
        assert jobs.claim(db, "w2") is not None  # actually runnable again

    def test_publish_is_never_bulk_revived(self, db):
        """A dead publish job may already have posted; re-running it is the
        duplicate the publications state machine exists to prevent."""
        self._dead(db, stage="publish")
        assert jobs.requeue_dead(db) == 0
        assert jobs.requeue_dead(db, stage="publish") == 0
        assert db.execute("SELECT state FROM jobs").fetchone()["state"] == "dead"

    def test_dead_recent_is_what_a_health_light_should_use(self, db):
        """The lifetime count only ever grows, so a light driven by it goes red
        once and stays red — an alarm that cannot clear is one you stop reading."""
        self._dead(db)
        counts = jobs.counts_by_state(db)
        assert counts["dead"] == 1 and counts["dead_recent"] == 1
        db.execute("UPDATE jobs SET created_at = '2020-01-01T00:00:00.000Z' WHERE state = 'dead'")
        counts = jobs.counts_by_state(db)
        assert counts["dead"] == 1 and counts["dead_recent"] == 0  # old news, light clears
