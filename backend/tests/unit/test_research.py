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


def test_summary_fallback_when_no_content(seeded):
    draft_id = land_analysis_draft(
        seeded, query="q longer", report={"summary": "Brief take.", "sources": []}, incident_id=None
    )
    d = seeded.execute("SELECT body_md FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    assert d["body_md"].startswith("Brief take.")
