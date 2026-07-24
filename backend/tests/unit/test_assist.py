"""Operator-authored drafts + editor AI assists.

The assists are STYLE tools: whitelisted server-side instructions, suggestion-
only responses, and the human save/review/approve gates unchanged."""

import pytest
from fastapi import HTTPException

from toast.api import routes
from toast.pipeline import assist as assist_mod


class Req:
    def __init__(self, conn):
        self.app = type("A", (), {"state": type("S", (), {"conn": conn})()})()


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.seen = None

    def chat_json(self, messages, **kw):
        self.seen = messages[0]["content"]
        return self.payload


def test_create_blank_draft_and_lifecycle(seeded):
    r = routes.create_manual_draft(Req(seeded), {"title": "My field report"})
    did = r["draft_id"]
    row = seeded.execute("SELECT * FROM drafts WHERE id = ?", (did,)).fetchone()
    assert row["status"] == "draft" and row["kind"] == "update"
    assert row["body_md"] == "" and row["cited_fact_ids"] == "[]"
    assert row["title"] == "My field report"
    # the operator writes, then the normal state machine applies
    seeded.execute("UPDATE drafts SET body_md = 'words' WHERE id = ?", (did,))
    seeded.execute("UPDATE drafts SET status = 'needs_review' WHERE id = ?", (did,))


def test_create_with_unknown_incident_404s(seeded):
    with pytest.raises(HTTPException):
        routes.create_manual_draft(Req(seeded), {"incident_id": "nope"})


def test_assist_edit_returns_suggestion_only(seeded):
    fake = FakeLLM({"text": "Tighter words."})
    out = assist_mod.assist_text(seeded, "tighten", "Some loose words here.", client=fake)
    assert out["text"] == "Tighter words." and out["op"] == "tighten"
    # the instruction came from the server-side whitelist, not the client
    assert "Tighten the text" in fake.seen
    # and the operator text rode inside the prompt as data
    assert "Some loose words here." in fake.seen


def test_assist_headlines_shape(seeded):
    fake = FakeLLM({"headlines": ["One", "Two", "Three", "Four", "Five", "Six"]})
    out = assist_mod.assist_text(seeded, "headlines", "A fire report.", client=fake)
    assert out["headlines"] == ["One", "Two", "Three", "Four", "Five"]  # capped at 5


def test_assist_rejects_unknown_op_and_empty_text(seeded):
    with pytest.raises(ValueError):
        assist_mod.assist_text(seeded, "make_stuff_up", "text", client=FakeLLM({}))
    with pytest.raises(ValueError):
        assist_mod.assist_text(seeded, "tighten", "   ", client=FakeLLM({}))


def test_assist_route_refuses_published(seeded):
    r = routes.create_manual_draft(Req(seeded), {"title": "t", "body_md": "b"})
    did = r["draft_id"]
    seeded.execute("UPDATE drafts SET status='needs_review' WHERE id=?", (did,))
    seeded.execute(
        "UPDATE drafts SET status='approved', approved_by='op',"
        " approved_at='2026-07-19T00:00:00Z' WHERE id=?",
        (did,),
    )
    seeded.execute("UPDATE drafts SET status='published' WHERE id=?", (did,))
    with pytest.raises(HTTPException) as exc:
        routes.assist_draft_route(Req(seeded), did, {"op": "tighten", "text": "x"})
    assert exc.value.status_code == 409


def test_assist_prompts_load_and_carry_guardrails(db):
    from toast.llm.prompts import load_prompt

    edit = load_prompt("assist_edit")
    assert "{instruction}" in edit.template and "{text}" in edit.template
    assert "EVACUATIONS ARE VERBATIM" in edit.template
    assert "SECURITY CONTRACT" in edit.template
    heads = load_prompt("assist_headlines")
    assert "{text}" in heads.template and "SECURITY CONTRACT" in heads.template
