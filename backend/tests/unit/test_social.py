"""Social rewrite layer: faithful human-voice second draft, linked to parent."""

import pytest

from toast.core.db import utcnow
from toast.llm.client import LLMError
from toast.pipeline.social import generate_social

BODY = (
    "The Flume Fire has burned 1,240 acres [#1] and is 15% contained [#2].\n\n"
    "---\n\n**Sources**\n\n1. CAL FIRE active incidents (T1) — ..."
)


class FakeLLM:
    def __init__(self, response):
        self.response = response
        self.last_prompt = None

    def chat_json(self, messages, **kw):
        self.last_prompt = messages[0]["content"]
        return self.response


def _draft(conn, did="d1", body=BODY, title="FLUME FIRE UPDATE"):
    conn.execute(
        "INSERT INTO drafts (id, incident_id, title, body_md, status, created_at, updated_at)"
        " VALUES (?, 'i1', ?, ?, 'approved', ?, ?)",
        (did, title, body, utcnow(), utcnow()),
    )


def test_social_draft_created_and_linked(seeded):
    _draft(seeded)
    fake = FakeLLM(
        {
            "post_text": "The Flume Fire near Paradise is now 1,240 acres, 15% contained. "
            "Crews made progress overnight. Sources: CAL FIRE",
            "short_text": "Flume Fire: 1,240 acres, 15% contained. Sources: CAL FIRE",
        }
    )
    result = generate_social(seeded, "d1", client=fake)
    d = seeded.execute("SELECT * FROM drafts WHERE id=?", (result["draft_id"],)).fetchone()
    assert d["kind"] == "social" and d["status"] == "draft"
    assert d["parent_id"] == "d1"  # lineage: facts -> report -> post
    assert d["incident_id"] == "i1"
    assert d["title"].startswith("SOCIAL — FLUME FIRE")
    assert "Short version (X/Twitter" in d["body_md"]
    # the full source report reached the model
    assert "1,240 acres" in fake.last_prompt and "SOURCE REPORT" in fake.last_prompt


def test_social_requires_body_and_output(seeded):
    _draft(seeded, "d2", body="   ")
    with pytest.raises(ValueError):
        generate_social(seeded, "d2", client=FakeLLM({}))
    _draft(seeded, "d3")
    with pytest.raises(LLMError):
        generate_social(seeded, "d3", client=FakeLLM({"post_text": ""}))
