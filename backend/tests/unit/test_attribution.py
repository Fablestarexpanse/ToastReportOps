"""Attribution scorer: name/geo/time signals, auto vs review thresholds."""

from toast.pipeline.attribution import (
    attribute_fact,
    geo_score,
    name_similarity,
    score_candidates,
)
from toast.pipeline.ledger import write_fact

NOW = "2026-07-19T12:00:00.000Z"


def _incident(conn, iid, name, lat, lon, when=NOW):
    conn.execute(
        "INSERT INTO incidents (id, canonical_name, status, lat, lon, created_at, updated_at)"
        " VALUES (?, ?, 'active', ?, ?, ?, ?)",
        (iid, name, lat, lon, when, when),
    )


def test_name_similarity():
    assert name_similarity("Flume Fire", "FLUME FIRE", []) == 1.0
    assert name_similarity("Flume", "FLUME FIRE", []) == 1.0  # "fire" normalized out
    assert name_similarity(None, "FLUME FIRE", []) == 0.0
    assert name_similarity("Camp Fire", "FLUME FIRE", []) < 0.5
    assert name_similarity("Dixie", "FLUME FIRE", ["Dixie Complex"]) == 1.0  # alias match


def test_geo_score_falloff():
    assert geo_score(39.76, -121.6, 39.76, -121.6) == 1.0
    assert geo_score(39.76, -121.6, 41.9, -114.2) == 0.0  # far
    mid = geo_score(39.76, -121.6, 39.96, -121.6)  # ~22 km
    assert 0.0 < mid < 1.0
    assert geo_score(None, None, 39.76, -121.6) == 0.0


def test_score_candidates_ranks_by_combined_signal(seeded):
    _incident(seeded, "i-flume", "FLUME FIRE", 39.76, -121.6)
    _incident(seeded, "i-far", "CAMP FIRE", 34.0, -118.2)
    cands = score_candidates(
        seeded, observed_at=NOW, incident_name="Flume Fire", lat=39.77, lon=-121.61
    )
    assert cands[0].incident_id == "i-flume"
    assert cands[0].score > cands[1].score


def test_auto_attribute_when_clear_winner(seeded):
    _incident(seeded, "i-flume", "FLUME FIRE", 39.76, -121.6)
    fid = write_fact(
        seeded,
        incident_id=None,
        envelope_id="e1",
        source_id="s1",
        tier=4,
        content="Flume Fire now 500 acres",
        kind="acreage",
        observed_at=NOW,
        confidence=0.7,
        acreage=500.0,
        lat=39.76,
        lon=-121.6,
        structured={"incident_name": "Flume Fire"},
    )
    outcome, _ = attribute_fact(seeded, fid)
    assert outcome == "attributed"
    row = seeded.execute("SELECT incident_id FROM facts WHERE id=?", (fid,)).fetchone()
    assert row["incident_id"] == "i-flume"


def test_ambiguous_goes_to_review_not_auto(seeded):
    # two same-named nearby incidents -> genuine tie, no confident winner
    _incident(seeded, "i-a", "MILL FIRE", 39.50, -121.50)
    _incident(seeded, "i-b", "MILL FIRE", 39.55, -121.55)
    fid = write_fact(
        seeded,
        incident_id=None,
        envelope_id="e1",
        source_id="s1",
        tier=5,
        content="Mill fire grows",
        kind="acreage",
        observed_at=NOW,
        confidence=0.6,
        lat=39.52,
        lon=-121.52,
        structured={"incident_name": "Mill Fire"},
    )
    outcome, cands = attribute_fact(seeded, fid)
    assert outcome == "review"  # top two scores too close to auto-attribute
    assert (
        seeded.execute("SELECT incident_id FROM facts WHERE id=?", (fid,)).fetchone()["incident_id"]
        is None
    )
    assert len(cands) >= 2


def test_no_candidate_leaves_unattributed(seeded):
    fid = write_fact(
        seeded,
        incident_id=None,
        envelope_id="e1",
        source_id="s1",
        tier=5,
        content="something",
        kind="other",
        observed_at=NOW,
        confidence=0.5,
        structured={"incident_name": "Nonexistent Fire"},
    )
    outcome, _ = attribute_fact(seeded, fid)
    assert outcome == "none"
