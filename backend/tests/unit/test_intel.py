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


def _tiered_fact(seeded, kind, tier, *, acreage=None, containment_pct=None, source="s1"):
    """A fact from a specific trust tier (T1-2 official, T3+ press/scraped)."""
    if source != "s1":
        seeded.execute(
            "INSERT OR IGNORE INTO sources (id, name, type, adapter, tier, created_at, updated_at)"
            " VALUES (?, ?, 'scrape', 'rss', ?, ?, ?)",
            (source, f"Outlet {source}", tier, NOW, NOW),
        )
        seeded.execute(
            "INSERT OR IGNORE INTO envelopes (id, source_id, external_id, kind, payload,"
            " content_hash, fetched_at) VALUES (?, ?, ?, 'article', '{}', ?, ?)",
            (f"e-{source}", source, source, f"h-{source}", NOW),
        )
    fid = ulid()
    seeded.execute(
        "INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,"
        " acreage, containment_pct, confidence, observed_at, ingested_at)"
        " VALUES (?, 'i1', ?, ?, ?, 'claim', ?, ?, ?, 0.9, ?, ?)",
        (
            fid,
            "e1" if source == "s1" else f"e-{source}",
            source,
            tier,
            kind,
            acreage,
            containment_pct,
            NOW,
            NOW,
        ),
    )
    return fid


def test_official_and_reported_are_kept_apart(seeded):
    """Averaging an agency figure with a newsroom figure would hide both."""
    _tiered_fact(seeded, "acreage", 1, acreage=12000)  # CAL FIRE
    _tiered_fact(seeded, "acreage", 4, acreage=30000, source="s-press")

    m = incident_intel(seeded, "i1")["metrics"]["acreage"]
    assert m["official"]["value"] == 12000
    assert m["reported"]["value"] == 30000
    # the headline number is the official one — a tile must not show press figures
    assert m["value"] == 12000


def test_material_disagreement_is_flagged(seeded):
    _tiered_fact(seeded, "acreage", 1, acreage=12000)
    _tiered_fact(seeded, "acreage", 4, acreage=30000, source="s-press")
    d = incident_intel(seeded, "i1")["metrics"]["acreage"]["divergence"]
    assert d is not None and d["reported_higher"] is True
    assert d["gap"] == 18000


def test_small_differences_are_not_flagged(seeded):
    _tiered_fact(seeded, "acreage", 1, acreage=12000)
    _tiered_fact(seeded, "acreage", 4, acreage=12500, source="s-press")
    assert incident_intel(seeded, "i1")["metrics"]["acreage"]["divergence"] is None


def test_reported_only_still_shows_but_is_labelled(seeded):
    """With no agency figure the press number is all we have — show it, and be
    clear that is what it is."""
    _tiered_fact(seeded, "acreage", 4, acreage=800, source="s-press")
    m = incident_intel(seeded, "i1")["metrics"]["acreage"]
    assert m["official"] is None
    assert m["reported"]["value"] == 800 and m["value"] == 800
    assert m["tier"] == 4


def test_evac_facts_are_marked_official_or_not(seeded):
    """Whether an evacuation instruction is the agency's or a paraphrase is the
    most important thing about it."""
    _tiered_fact(seeded, "evac", 1)
    _tiered_fact(seeded, "evac", 4, source="s-press")
    evac = incident_intel(seeded, "i1")["evac"]
    assert {e["official"] for e in evac} == {True, False}


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


def _evac_fact(seeded, evac_status, url="https://county.example/evac/BUT-1043"):
    """An evacuation fact whose envelope carries the issuing page."""
    fid, eid = ulid(), ulid()
    seeded.execute(
        "INSERT INTO envelopes (id, source_id, external_id, kind, payload, content_hash,"
        " fetched_at) VALUES (?, 's1', ?, 'article', ?, ?, ?)",
        (eid, eid, f'{{"url": "{url}"}}', f"h-{eid}", NOW),
    )
    seeded.execute(
        "INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,"
        " evac_status, confidence, observed_at, ingested_at)"
        " VALUES (?, 'i1', ?, 's1', 1, 'EVACUATION ORDER: Zone BUT-1043 GO NOW.', 'evac',"
        " ?, 0.9, ?, ?)",
        (fid, eid, evac_status, NOW, NOW),
    )
    return fid


def test_evac_text_travels_with_its_official_deep_link(seeded):
    """The mission rule is verbatim text + the deep-link + the notice. The link
    was never plumbed, so an operator could not open the issuing agency's page
    to confirm an order before repeating it to the public."""
    _evac_fact(seeded, "order")
    evac = incident_intel(seeded, "i1")["evac"]
    assert len(evac) == 1
    assert evac[0]["source_url"] == "https://county.example/evac/BUT-1043"
    assert evac[0]["content"].startswith("EVACUATION ORDER:")  # verbatim, unsummarized


def _zone_cache(db, statuses):
    """Seed the Cal OES zone cache with zones already matched to incident i1."""
    import json

    from toast.core.db import utcnow

    features = [
        {
            "geometry": {"type": "Point", "coordinates": [-121.6, 39.76]},
            "properties": {
                "ZONE_ID": f"US-CA-XBU-Z{i}",
                "ZONE_NAME": f"Zone {i}",
                "STATUS": s,
                "issued_at": NOW,
                "incident_id": "i1",
            },
        }
        for i, s in enumerate(statuses)
    ]
    db.execute(
        "INSERT OR REPLACE INTO map_cache (key, payload, fetched_at) VALUES (?, ?, ?)",
        ("evaczones:ca", json.dumps({"features": features}), utcnow()),
    )


def test_every_evacuation_instruction_is_counted(seeded):
    """Counts are zone-authoritative now: a fire whose only instruction is
    'shelter in place' must still surface the badge, and each status is tallied
    from the zones a county has actually elevated (not from free-text facts,
    which over-count dupes and never drop a reduced zone)."""
    _zone_cache(seeded, ["Evacuation Order", "Shelter In Place", "Evacuation Warning"])
    counts = incident_intel(seeded, "i1")["impact_counts"]
    assert counts["orders"] == 1
    assert counts["shelter_in_place"] == 1
    assert counts["warnings"] == 1
    assert counts["advisory"] == 0
