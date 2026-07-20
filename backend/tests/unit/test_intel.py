import sqlite3

from toast.core.ids import ulid
from toast.pipeline.intel import incident_intel, incident_timeline

NOW = "2026-07-19T00:00:00.000Z"


def _fact(
    db: sqlite3.Connection,
    kind: str,
    content: str = "claim",
    acreage: float | None = None,
    containment_pct: float | None = None,
    supersedes: str | None = None,
    observed_at: str = NOW,
) -> str:
    fid = ulid()
    db.execute(
        "INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,"
        " acreage, containment_pct, confidence, observed_at, ingested_at, supersedes)"
        " VALUES (?, 'i1', 'e1', 's1', 1, ?, ?, ?, ?, 0.9, ?, ?, ?)",
        (fid, content, kind, acreage, containment_pct, observed_at, NOW, supersedes),
    )
    return fid


def test_metric_delta_comes_from_supersession_chain(seeded):
    old = _fact(seeded, "acreage", acreage=100, observed_at="2026-07-18T00:00:00.000Z")
    _fact(seeded, "acreage", acreage=1334, supersedes=old)
    intel = incident_intel(seeded, "i1")
    m = intel["metrics"]["acreage"]
    assert m["value"] == 1334
    assert m["prev"] == 100
    assert m["delta"] == 1234


def test_metric_without_chain_has_no_delta(seeded):
    _fact(seeded, "containment", containment_pct=50)
    m = incident_intel(seeded, "i1")["metrics"]["containment"]
    assert m["value"] == 50
    assert m["delta"] is None


def test_situation_shows_latest_non_superseded_per_kind(seeded):
    old = _fact(seeded, "damage", "two structures damaged", observed_at="2026-07-18T00:00:00.000Z")
    _fact(seeded, "damage", "3 structures lost", supersedes=old)
    _fact(seeded, "other", "crews holding the eastern line")
    intel = incident_intel(seeded, "i1")
    by_kind = {s["kind"]: s["content"] for s in intel["situation"]}
    assert by_kind["damage"] == "3 structures lost"
    assert by_kind["other"] == "crews holding the eastern line"


def test_resources_and_cause_are_not_repeated_as_situation_bullets(seeded):
    """Both have their own block; repeating them makes one fact look like two
    independent reports."""
    _fact(seeded, "resource", "513 total personnel assigned")
    _fact(seeded, "cause", "Under investigation")
    intel = incident_intel(seeded, "i1")
    kinds = {s["kind"] for s in intel["situation"]}
    assert "resource" not in kinds and "cause" not in kinds
    # …but they are still present where they belong
    assert intel["metrics"]["cause"]["content"] == "Under investigation"
    assert intel["resources"]["notes"][0]["content"].startswith("513 total personnel")


def test_evac_facts_returned_verbatim(seeded):
    _fact(seeded, "evac", "EVACUATION ORDER for zone ABC-123. Leave now.")
    intel = incident_intel(seeded, "i1")
    assert intel["evac"][0]["content"] == "EVACUATION ORDER for zone ABC-123. Leave now."
    assert intel["road"] == []


def test_confidence_meter_counts_sources(seeded):
    fid = _fact(seeded, "acreage", acreage=10)
    seeded.execute(
        "INSERT INTO fact_assessments (fact_id, effective_confidence, corroborating_sources,"
        " updated_at) VALUES (?, 0.8, 2, ?)",
        (fid, NOW),
    )
    conf = incident_intel(seeded, "i1")["confidence"]
    assert conf["avg_confidence"] == 0.8
    assert conf["source_count"] == 1
    assert conf["corroborated_facts"] == 1


def test_timeline_merges_events_and_facts_newest_first(seeded):
    seeded.execute(
        "INSERT INTO incident_events (id, incident_id, event, actor, at)"
        " VALUES ('ev1', 'i1', 'created', 'system', '2026-07-17T00:00:00.000Z')"
    )
    _fact(seeded, "acreage", acreage=10, observed_at="2026-07-18T00:00:00.000Z")
    tl = incident_timeline(seeded, "i1")
    assert [e["type"] for e in tl] == ["fact", "event"]
    assert tl[1]["label"] == "CREATED"
