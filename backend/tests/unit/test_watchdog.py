"""The watchdog must notice silence, speak once, and say when it's over."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx

from toast.core.config import Settings
from toast.core.ids import ulid
from toast.pipeline import watchdog

SET = Settings(telegram_bot_token="123:abc", telegram_ops_chat_id="777")
MODELS = ["some-model"]


def ago(seconds: int) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _job(conn, stage, state, created, payload=None, error=None, run_after=None):
    conn.execute(
        "INSERT INTO jobs (id, stage, payload, state, created_at, claimed_at,"
        " last_error, run_after) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ulid(), stage, json.dumps(payload or {}), state, created, created, error, run_after),
    )


def _polling_source(conn, every=60, born=None):
    conn.execute(
        "UPDATE sources SET schedule = ?, created_at = ? WHERE id = 's1'",
        (every, born or ago(86400)),
    )


def _recorder():
    sent: list[str] = []

    def handler(request):
        sent.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True})

    return sent, httpx.Client(transport=httpx.MockTransport(handler))


def test_healthy_system_reports_nothing(seeded):
    _polling_source(seeded)
    _job(seeded, "poll", "done", ago(30), {"source_id": "s1"})
    assert watchdog.find_problems(seeded, llm_models=MODELS) == {}


def test_silent_official_source_is_flagged(seeded):
    _polling_source(seeded, every=60)
    _job(seeded, "poll", "done", ago(3 * 3600), {"source_id": "s1"})
    assert "silent:s1" in watchdog.find_problems(seeded, llm_models=MODELS)


def test_a_dead_poll_does_not_count_as_a_success(seeded):
    _polling_source(seeded, every=60)
    _job(seeded, "poll", "dead", ago(60), {"source_id": "s1"})
    assert "silent:s1" in watchdog.find_problems(seeded, llm_models=MODELS)


def test_new_source_gets_a_grace_period(seeded):
    _polling_source(seeded, every=60, born=ago(60))
    assert watchdog.find_problems(seeded, llm_models=MODELS) == {}


def test_cluster_of_dead_jobs_is_flagged_but_one_is_noise(seeded):
    _job(seeded, "extract", "dead", ago(600), error="boom")
    assert watchdog.find_problems(seeded, llm_models=MODELS) == {}
    for _ in range(2):
        _job(seeded, "extract", "dead", ago(600), error="boom")
    assert "dead:extract" in watchdog.find_problems(seeded, llm_models=MODELS)


def test_stalled_queue_is_flagged(seeded):
    _job(seeded, "extract", "queued", ago(3600))
    assert "queue_stalled" in watchdog.find_problems(seeded, llm_models=MODELS)


def test_a_job_deferred_to_the_future_is_not_a_stall(seeded):
    future = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    _job(seeded, "extract", "queued", ago(3600), run_after=future)
    assert "queue_stalled" not in watchdog.find_problems(seeded, llm_models=MODELS)


def test_unreachable_llm_is_flagged(seeded):
    assert "llm" in watchdog.find_problems(seeded, llm_models=[])


def test_notifies_once_then_stays_quiet_then_announces_recovery(seeded):
    _job(seeded, "extract", "queued", ago(3600))
    sent, client = _recorder()

    first = watchdog.run_watchdog(seeded, SET, client, llm_models=MODELS)
    assert first["notified"] and len(sent) == 1 and "worker" in sent[0]

    again = watchdog.run_watchdog(seeded, SET, client, llm_models=MODELS)
    assert not again["notified"] and len(sent) == 1  # same problem, no spam

    seeded.execute("DELETE FROM jobs")
    done = watchdog.run_watchdog(seeded, SET, client, llm_models=MODELS)
    assert done["notified"] and "recovered" in sent[-1].lower()

    assert not watchdog.run_watchdog(seeded, SET, client, llm_models=MODELS)["notified"]


def test_failed_delivery_is_retried_next_pass(seeded):
    _job(seeded, "extract", "queued", ago(3600))
    down = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert not watchdog.run_watchdog(seeded, SET, down, llm_models=MODELS)["notified"]
    sent, client = _recorder()
    assert watchdog.run_watchdog(seeded, SET, client, llm_models=MODELS)["notified"]
    assert len(sent) == 1


def test_unconfigured_telegram_is_a_silent_noop(seeded):
    _job(seeded, "extract", "queued", ago(3600))
    out = watchdog.run_watchdog(seeded, Settings(), llm_models=MODELS)
    assert out["problems"] == ["queue_stalled"] and not out["notified"]
