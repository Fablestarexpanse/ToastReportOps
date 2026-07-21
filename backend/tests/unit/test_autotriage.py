"""AI triage proposals.

The two guardrails that make this safe: a model-invented incident id is
discarded (downgraded to 'unsure'), and a proposal is only ever advice — the
fact row is untouched until the operator accepts through the normal endpoints.
"""

from toast.core.ids import ulid
from toast.pipeline import autotriage

NOW = "2026-07-19T00:00:00.000Z"


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def chat_json(self, messages, **kw):
        self.prompts.append(messages[0]["content"])
        return self.replies.pop(0)


def _claim(db, content="Fire near Paradise grows.", name=None):
    fid = ulid()
    import json

    db.execute(
        "INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,"
        " structured, confidence, observed_at, ingested_at)"
        " VALUES (?, NULL, 'e1', 's1', 4, ?, 'start', ?, 0.6, ?, ?)",
        (fid, content, json.dumps({"incident_name": name} if name else {}), NOW, NOW),
    )
    return fid


def test_attach_proposal_stores_with_candidate_validation(seeded):
    seeded.execute(
        "UPDATE incidents SET lat = 39.76, lon = -121.62, canonical_name = 'Sawmill Fire'"
        " WHERE id = 'i1'"
    )
    fid = _claim(seeded, "The Sawmill Fire burned 400 acres near Paradise.", name="Sawmill Fire")
    llm = FakeLLM([
        {"action": "attach", "incident_id": "i1", "confidence": 0.9,
         "reasoning": "Name matches the tracked Sawmill Fire."},
    ])
    out = autotriage.propose_batch(seeded, client=llm)
    assert out["proposed"] == 1
    prop = seeded.execute("SELECT * FROM triage_proposals WHERE fact_id = ?", (fid,)).fetchone()
    assert prop["action"] == "attach" and prop["incident_id"] == "i1"
    assert prop["incident_name"] == "Sawmill Fire"
    # advice only: the fact itself is untouched
    assert (
        seeded.execute("SELECT incident_id FROM facts WHERE id = ?", (fid,)).fetchone()[0] is None
    )
    # the hostile claim went in fenced
    assert "=====" in llm.prompts[0] and "Sawmill Fire burned" in llm.prompts[0]


def test_a_model_invented_id_is_never_trusted(seeded):
    fid = _claim(seeded)
    llm = FakeLLM([
        {"action": "attach", "incident_id": "totally-made-up", "confidence": 0.99,
         "reasoning": "trust me"},
    ])
    autotriage.propose_batch(seeded, client=llm)
    prop = seeded.execute("SELECT * FROM triage_proposals WHERE fact_id = ?", (fid,)).fetchone()
    assert prop["action"] == "unsure" and prop["incident_id"] is None


def test_proposed_items_are_not_reproposed(seeded):
    _claim(seeded)
    llm = FakeLLM([
        {"action": "not_incident", "confidence": 0.8, "reasoning": "old fire retrospective"},
        {"action": "not_incident", "confidence": 0.8, "reasoning": "should never be asked"},
    ])
    assert autotriage.propose_batch(seeded, client=llm)["proposed"] == 1
    assert autotriage.propose_batch(seeded, client=llm)["proposed"] == 0
    assert len(llm.prompts) == 1


def test_llm_outage_skips_quietly(seeded):
    from toast.llm.client import LLMError

    _claim(seeded)

    class Down:
        def chat_json(self, *a, **kw):
            raise LLMError("connection refused")

    out = autotriage.propose_batch(seeded, client=Down())
    assert out["failed"] == 1 and out["proposed"] == 0
