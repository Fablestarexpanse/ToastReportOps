"""Research layer: LDR report → analysis draft (the ledger-boundary landing)."""

from toast.pipeline.research import land_analysis_draft

REPORT = {
    "content": "The Biscar Fire has burned roughly 30,000 acres in Lassen County...",
    "sources": [
        {"title": "CAL FIRE incident page", "url": "https://www.fire.ca.gov/incidents/x"},
        {"title": "Local news report", "url": "https://example.com/biscar"},
        {"url": "https://example.com/untitled"},
    ],
}


def test_report_lands_as_analysis_draft(seeded):
    draft_id = land_analysis_draft(
        seeded, query="Biscar Fire status", report=REPORT, incident_id="i1"
    )
    d = seeded.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    assert d["kind"] == "analysis" and d["status"] == "draft"  # review required
    assert d["incident_id"] == "i1"
    assert d["title"].startswith("RESEARCH — Biscar Fire")
    assert "30,000 acres" in d["body_md"]
    # cited sources listed, with the verify warning
    assert "Research sources" in d["body_md"] and "verify before use" in d["body_md"]
    assert "1. CAL FIRE incident page — https://www.fire.ca.gov/incidents/x" in d["body_md"]
    assert "3. https://example.com/untitled" in d["body_md"]
    # ledger boundary: no facts were written
    assert seeded.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"] == 0


def test_active_research_surfaces_in_drafts_listing(seeded):
    from toast.api.routes import _active_research
    from toast.queue import jobs

    # a queued start job, an in-progress poll, a parked poll, and a dead run
    jobs.enqueue(seeded, "deep_research", {"query": "Ridge Fire status", "incident_id": "i1"})
    jobs.enqueue(
        seeded, "deep_research_poll", {"query": "Dove Fire status", "research_id": "r2", "hops": 3}
    )
    jobs.enqueue(
        seeded,
        "deep_research_poll",
        {"query": "Oak Fire status", "research_id": "r3", "rl_hops": 1},
        run_after="2999-01-01T00:00:00.000Z",
    )
    dead_id = jobs.enqueue(seeded, "deep_research", {"query": "Old Fire status"})
    seeded.execute("UPDATE jobs SET state='dead', last_error='boom' WHERE id = ?", (dead_id,))

    items = {i["query"]: i for i in _active_research(seeded)}
    assert items["Ridge Fire status"]["state"] == "starting"
    assert items["Ridge Fire status"]["incident_id"] == "i1"
    assert items["Ridge Fire status"]["incident_name"] == "FLUME FIRE"
    assert items["Dove Fire status"]["state"] == "researching"
    assert items["Oak Fire status"]["state"] == "parked"
    assert items["Old Fire status"]["state"] == "failed"
    assert items["Old Fire status"]["error"] == "boom"


def test_summary_fallback_when_no_content(seeded):
    draft_id = land_analysis_draft(
        seeded, query="q longer", report={"summary": "Brief take.", "sources": []}, incident_id=None
    )
    d = seeded.execute("SELECT body_md FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    assert d["body_md"].startswith("Brief take.")
