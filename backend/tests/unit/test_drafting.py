"""Draft generation: window filtering, fact numbering, evac-verbatim prompt,
citation id translation, and the draft insert."""

import json

from toast.pipeline.drafting import gather_facts, generate_draft

NOW = "2026-07-19T12:00:00.000Z"
LATER = "2026-07-19T18:00:00.000Z"
EVAC_TEXT = "EVACUATION ORDER for zones PAR-E031 and E032 effective immediately"


class FakeLLM:
    def __init__(self, response):
        self.response = response
        self.last_prompt = None

    def chat_json(self, messages, **kw):
        self.last_prompt = messages[0]["content"]
        return self.response


def _w(conn, fid, kind, content, observed, superseded, evac):
    conn.execute(
        "INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,"
        " evac_status, confidence, observed_at, ingested_at, supersedes)"
        " VALUES (?, 'i1', 'e1', 's1', 1, ?, ?, ?, 0.99, ?, ?, ?)",
        (fid, content, kind, evac, observed, NOW, superseded),
    )
    return fid


def test_gather_facts_excludes_superseded_and_respects_window(seeded):
    _w(seeded, "f1", "acreage", "100 acres", NOW, None, None)
    _w(seeded, "f2", "acreage", "350 acres", LATER, "f1", None)  # supersedes f1
    _w(seeded, "f3", "containment", "10% contained", "2026-07-01T00:00:00.000Z", None, None)

    current = gather_facts(seeded, "i1")
    ids = {f["id"] for f in current}
    assert "f1" not in ids and "f2" in ids  # superseded excluded
    assert "f3" in ids

    windowed = gather_facts(seeded, "i1", window_start=NOW)
    ids = {f["id"] for f in windowed}
    assert "f3" not in ids  # before window
    assert "f2" in ids


def test_generate_draft_inserts_and_maps_citations(seeded):
    _w(seeded, "f1", "acreage", "Fire at 1200 acres", NOW, None, None)
    _w(seeded, "f2", "containment", "0% contained", NOW, None, None)
    fake = FakeLLM(
        {
            "title": "FLUME FIRE UPDATE",
            "body_md": "The fire is 1,200 acres [#1] and 0% contained [#2].",
            "cited_fact_ids": ["1", "2"],
        }
    )
    result = generate_draft(seeded, "i1", client=fake)
    assert result["created"] and result["facts_used"] == 2 and result["cited"] == 2

    draft = seeded.execute("SELECT * FROM drafts WHERE id = ?", (result["draft_id"],)).fetchone()
    assert draft["status"] == "draft" and draft["title"] == "FLUME FIRE UPDATE"
    cited = json.loads(draft["cited_fact_ids"])
    assert set(cited) == {"f1", "f2"}  # short [#1],[#2] mapped back to real ids


def test_evac_facts_flagged_verbatim_in_prompt(seeded):
    _w(seeded, "f1", "evac", EVAC_TEXT, NOW, None, "order")
    fake = FakeLLM({"title": "x", "body_md": "y", "cited_fact_ids": []})
    generate_draft(seeded, "i1", client=fake)
    assert "EVACUATION — reproduce verbatim" in fake.last_prompt
    assert EVAC_TEXT in fake.last_prompt  # the official text reaches the model intact


def test_no_facts_in_window_returns_not_created(seeded):
    result = generate_draft(
        seeded, "i1", window_start="2099-01-01T00:00:00.000Z", client=FakeLLM({})
    )
    assert result["created"] is False


def test_body_includes_numbered_sources_section(seeded):
    _w(seeded, "f1", "acreage", "Fire at 1200 acres", NOW, None, None)
    _w(seeded, "f2", "containment", "0% contained", NOW, None, None)
    fake = FakeLLM(
        {
            "title": "T",
            "body_md": "Fire is 1,200 acres [#1], 0% contained [#2].",
            "cited_fact_ids": ["1", "2"],
        }
    )
    result = generate_draft(seeded, "i1", client=fake)
    body = seeded.execute(
        "SELECT body_md FROM drafts WHERE id=?", (result["draft_id"],)
    ).fetchone()["body_md"]
    assert "**Sources**" in body
    # numbered entries match the inline [#n] citations, and name the source
    assert "1. CAL FIRE" in body and "2. CAL FIRE" in body
    assert "Fire at 1200 acres" in body and "0% contained" in body
    # the article body (before the rule) still carries the inline citations
    article, _, sources = body.partition("---")
    assert "[#1]" in article and "[#2]" in article
    assert "**Sources**" in sources


def test_empty_body_gets_no_sources_section(seeded):
    _w(seeded, "f1", "acreage", "1200 acres", NOW, None, None)
    result = generate_draft(
        seeded, "i1", client=FakeLLM({"title": "T", "body_md": "", "cited_fact_ids": []})
    )
    body = seeded.execute(
        "SELECT body_md FROM drafts WHERE id=?", (result["draft_id"],)
    ).fetchone()["body_md"]
    assert body == ""  # nothing drafted → no sources appended


def test_bad_citation_ids_are_dropped(seeded):
    _w(seeded, "f1", "acreage", "1200 acres", NOW, None, None)
    fake = FakeLLM({"title": "t", "body_md": "b [#1] [#9]", "cited_fact_ids": ["1", "9", "bogus"]})
    result = generate_draft(seeded, "i1", client=fake)
    draft = seeded.execute(
        "SELECT cited_fact_ids FROM drafts WHERE id=?", (result["draft_id"],)
    ).fetchone()
    assert json.loads(draft["cited_fact_ids"]) == ["f1"]  # only the real id #1 survives
