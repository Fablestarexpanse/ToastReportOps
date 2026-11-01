"""A missing model is a state to wait out, not a job failure.

168 extract jobs once burned all five retries into the dead queue because no
model was installed yet, re-fetching every article on each attempt. Nothing was
wrong with those jobs; they only needed to wait.
"""

from __future__ import annotations

import json

import httpx
import pytest

from toast.core.config import Settings
from toast.core.ids import ulid
from toast.llm.client import LLMClient, LLMModelMissing
from toast.pipeline import handlers, watchdog
from toast.queue import handlers as registry
from toast.queue import jobs
from toast.queue.worker import run_once

SETTINGS = Settings(job_backoff_base_s=60.0, worker_poll_interval_s=0.01)
NOT_FOUND = '{"error":{"message":"model \'m\' not found","type":"not_found_error"}}'


def _client() -> LLMClient:
    return LLMClient(Settings(llm_base_url="http://llm:11434/v1"), model="m", embed_model="e")


def _post_returns(monkeypatch, status: int, text: str) -> None:
    def fake_post(url, **kw):
        return httpx.Response(status, text=text, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)


def _get_returns(monkeypatch, status: int, payload: dict) -> None:
    def fake_get(url, **kw):
        return httpx.Response(status, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)


def test_chat_404_for_a_model_is_model_missing(monkeypatch):
    _post_returns(monkeypatch, 404, NOT_FOUND)
    with pytest.raises(LLMModelMissing):
        _client().chat_json([{"role": "system", "content": "x"}])


def test_embed_404_for_a_model_is_model_missing(monkeypatch):
    _post_returns(monkeypatch, 404, NOT_FOUND)
    with pytest.raises(LLMModelMissing):
        _client().embed(["x"])


def test_a_404_that_is_not_about_a_model_stays_an_ordinary_error(monkeypatch):
    from toast.llm.client import LLMError

    _post_returns(monkeypatch, 404, "page not found")
    with pytest.raises(LLMError) as err:
        _client().chat_json([{"role": "system", "content": "x"}])
    assert not isinstance(err.value, LLMModelMissing)


def test_list_models_survives_an_endpoint_with_no_models(monkeypatch):
    """Ollama answers data:null with nothing pulled. That crashed the watchdog
    in the very situation it exists to report."""
    _get_returns(monkeypatch, 200, {"object": "list", "data": None})
    assert _client().list_models() == []


@pytest.mark.parametrize(
    ("ids", "missing"),
    [
        ([], True),
        (["other:latest"], True),
        (["m"], False),
        (["m:latest"], False),
    ],
)
def test_ensure_chat_model(monkeypatch, ids, missing):
    _get_returns(monkeypatch, 200, {"data": [{"id": i} for i in ids]})
    if missing:
        with pytest.raises(LLMModelMissing):
            _client().ensure_chat_model()
    else:
        _client().ensure_chat_model()


def test_ensure_chat_model_does_not_guess_when_the_endpoint_is_down(monkeypatch):
    def down(url, **kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", down)
    _client().ensure_chat_model()  # the real call will report the real failure


def _register_failing(monkeypatch, exc: Exception) -> None:
    def handler(conn, payload):
        raise exc

    monkeypatch.setitem(registry._REGISTRY, "demo_wait", handler)


def test_worker_waits_without_spending_an_attempt(db, monkeypatch):
    _register_failing(monkeypatch, LLMModelMissing("no model installed: m"))
    job_id = jobs.enqueue(db, "demo_wait", {}, max_attempts=1)

    assert run_once(db, "w1", SETTINGS) is True

    row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["state"] == "queued"
    assert row["attempts"] == 0
    assert row["run_after"] is not None
    assert row["last_error"].startswith(jobs.WAITING_FOR_MODEL)
    assert jobs.claim(db, "w1") is None  # not runnable until the wait is over


def test_waiting_jobs_are_counted_for_the_top_bar(db, monkeypatch):
    _register_failing(monkeypatch, LLMModelMissing("no model installed: m"))
    jobs.enqueue(db, "demo_wait", {})
    run_once(db, "w1", SETTINGS)
    assert jobs.counts_by_state(db)["waiting_model"] == 1


def test_watchdog_names_the_cause_instead_of_a_stall(db, monkeypatch):
    _register_failing(monkeypatch, LLMModelMissing("no model installed: m"))
    job_id = jobs.enqueue(db, "demo_wait", {})
    run_once(db, "w1", SETTINGS)
    # waiting a long time, and runnable this instant: still not a stalled worker
    db.execute(
        "UPDATE jobs SET created_at = '2020-01-01T00:00:00.000Z', run_after = NULL WHERE id = ?",
        (job_id,),
    )
    problems = watchdog.find_problems(db, llm_models=["other"])
    assert "queue_stalled" not in problems
    assert "no model installed" in problems["llm_model_missing"]


def test_extract_checks_for_the_model_before_fetching_the_article(seeded, monkeypatch):
    def explode(*a, **kw):  # pragma: no cover - must never run
        raise AssertionError("must not fetch an article while no model can read it")

    monkeypatch.setattr("toast.pipeline.analyze.fetch_article_text", explode)
    _get_returns(monkeypatch, 200, {"data": []})

    env_id = ulid()
    seeded.execute(
        "INSERT INTO envelopes (id, source_id, external_id, kind, payload, content_hash,"
        " fetched_at) VALUES (?, 's1', 'x', 'article', ?, 'h1', '2026-07-19T18:30:00.000Z')",
        (env_id, json.dumps({"url": "https://example.com/a", "title": "t", "summary": "s"})),
    )
    with pytest.raises(LLMModelMissing):
        handlers.extract_handler(seeded, {"envelope_id": env_id})
