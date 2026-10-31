"""Article dates: an undated claim takes its article's date, and old articles stay out.

Undated claims used to take the time of ingest, so a story published days ago read
as happening now and passed the New Starts board's recency check.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from toast.core.envelope import Envelope, insert_envelope
from toast.pipeline import handlers
from toast.pipeline.analyze import extract_article

TEXT = "The Sawmill Fire has burned 400 acres near Paradise."
PUBLISHED = "2026-10-28T09:00:00.000Z"


class FakeLLM:
    def chat_json(self, messages, **kw):
        return {
            "california_relevant": True,
            "primary_location": "Butte County, California",
            "claims": [
                {
                    "content": TEXT,
                    "kind": "acreage",
                    "acreage": 400,
                    "incident_name": "Sawmill Fire",
                    "confidence": 0.8,
                }
            ],
        }


def _extract(conn, **kw):
    env_id = insert_envelope(
        conn, Envelope(source_id="s1", external_id="a1", kind="article", payload={"url": "u"})
    )
    extract_article(
        conn,
        url="u",
        source_id="s1",
        tier=4,
        envelope_id=env_id,
        raw_ref=None,
        text=TEXT,
        client=FakeLLM(),
        **kw,
    )
    return conn.execute("SELECT observed_at, structured FROM facts").fetchone()


def test_undated_claim_takes_the_articles_date(seeded):
    fact = _extract(seeded, published_at=PUBLISHED)
    assert fact["observed_at"] == PUBLISHED
    assert json.loads(fact["structured"])["observed_at_estimated"] is True


def test_undated_claim_with_no_article_date_still_falls_back_to_now(seeded):
    fact = _extract(seeded)
    assert fact["observed_at"][:4] == str(datetime.now(UTC).year)
    assert json.loads(fact["structured"])["observed_at_estimated"] is True


def _envelope(conn, published_at):
    return insert_envelope(
        conn,
        Envelope(
            source_id="s1",
            external_id="old",
            kind="article",
            payload={
                "url": "https://example.com/a",
                "title": "Sawmill Fire reported near Paradise",
                "published_at": published_at,
            },
        ),
    )


def _iso_days_ago(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def test_old_article_costs_no_fetch_and_no_model(seeded, monkeypatch):
    def explode(*a, **kw):  # pragma: no cover - must never run
        raise AssertionError("an old article must not reach the fetch or the model")

    monkeypatch.setattr("toast.pipeline.analyze.fetch_article_text", explode)
    monkeypatch.setattr("toast.pipeline.analyze.extract_article", explode)
    monkeypatch.setattr("toast.llm.client.LLMClient.ensure_chat_model", explode)

    env_id = _envelope(seeded, _iso_days_ago(handlers.MAX_ARTICLE_AGE_DAYS + 1))
    handlers.extract_handler(seeded, {"envelope_id": env_id})
    assert seeded.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
    assert seeded.execute("SELECT COUNT(*) FROM envelopes WHERE id = ?", (env_id,)).fetchone()[0]


@pytest.mark.parametrize("published_at", [None, "not a date"])
def test_article_with_no_usable_date_is_still_read(seeded, monkeypatch, published_at):
    """No date proves nothing about age, and dropping it could lose a real report."""
    captured = {}

    def fake_extract(conn, **kw):
        captured.update(kw)
        return {"facts": []}

    monkeypatch.setattr("toast.pipeline.analyze.extract_article", fake_extract)
    monkeypatch.setattr("toast.llm.client.LLMClient.ensure_chat_model", lambda self: None)
    monkeypatch.setattr("toast.core.fetch.robots_allows", lambda url: False)

    env_id = _envelope(seeded, published_at)
    handlers.extract_handler(seeded, {"envelope_id": env_id})
    assert "text" in captured
    assert captured["published_at"] is None  # never written to the ledger as a date


def test_recent_article_passes_its_date_to_the_extractor(seeded, monkeypatch):
    captured = {}

    def fake_extract(conn, **kw):
        captured.update(kw)
        return {"facts": []}

    monkeypatch.setattr("toast.pipeline.analyze.extract_article", fake_extract)
    monkeypatch.setattr("toast.llm.client.LLMClient.ensure_chat_model", lambda self: None)
    monkeypatch.setattr("toast.core.fetch.robots_allows", lambda url: False)

    recent = _iso_days_ago(1)
    env_id = _envelope(seeded, recent)
    handlers.extract_handler(seeded, {"envelope_id": env_id})
    assert captured["published_at"] == recent
