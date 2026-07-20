"""Deployed-resource enrichment — matching must be conservative."""

import json

import pytest

from toast.pipeline import resources as R

NOW = "2026-07-19T12:00:00.000Z"
LAT, LON = 40.70, -122.19  # Brook Fire, Shasta County


def _incident(db, name="Brook Fire", lat=LAT, lon=LON, iid="i2"):
    db.execute(
        """INSERT INTO incidents (id, canonical_name, status, lat, lon, county,
                                  created_at, updated_at)
           VALUES (?, ?, 'active', ?, ?, 'Shasta', ?, ?)""",
        (iid, name, lat, lon, NOW, NOW),
    )
    return iid


def _feature(name="BROOK", lat=LAT, lon=LON, personnel=513, irwin="IRWIN-1"):
    return {
        "attributes": {
            "IrwinID": irwin,
            "IncidentName": name,
            "TotalIncidentPersonnel": personnel,
            "IncidentComplexityLevel": "Type 3",
            "EstimatedCostToDate": 250000,
            "ModifiedOnDateTime_dt": 1,
        },
        "geometry": {"x": lon, "y": lat},
    }


def _serve(monkeypatch, features):
    payload = json.dumps({"features": features}).encode()
    monkeypatch.setattr(R, "fetch", lambda url, **kw: (payload, "application/json"))


def test_matches_by_name_and_proximity(monkeypatch):
    _serve(monkeypatch, [_feature()])
    match = R.find_match("Brook Fire", LAT, LON)
    assert match is not None and match["TotalIncidentPersonnel"] == 513


def test_same_name_far_away_is_not_a_match(monkeypatch):
    """California repeats fire names; a name alone must never be enough."""
    _serve(monkeypatch, [_feature(lat=34.05, lon=-118.24)])  # 600+ km away
    assert R.find_match("Brook Fire", LAT, LON) is None


def test_substring_hit_with_a_different_name_is_rejected(monkeypatch):
    _serve(monkeypatch, [_feature(name="BROOKSIDE")])
    assert R.find_match("Brook Fire", LAT, LON) is None


def test_enrichment_writes_ledger_facts(seeded, monkeypatch):
    iid = _incident(seeded)
    _serve(monkeypatch, [_feature()])
    result = R.enrich_incident(seeded, iid)

    assert result["matched"] is True and result["recorded"] == 3
    rows = seeded.execute(
        "SELECT content, structured FROM facts WHERE incident_id = ? AND kind = 'resource'",
        (iid,),
    ).fetchall()
    metrics = {json.loads(r["structured"])["metric"] for r in rows}
    assert metrics == {"personnel", "complexity", "cost_to_date"}
    assert any("513 total personnel" in r["content"] for r in rows)


def test_updated_personnel_supersedes_rather_than_overwrites(seeded, monkeypatch):
    iid = _incident(seeded)
    _serve(monkeypatch, [_feature(personnel=513)])
    R.enrich_incident(seeded, iid)
    _serve(monkeypatch, [_feature(personnel=634, irwin="IRWIN-1")])
    seeded.execute("UPDATE envelopes SET content_hash = 'changed' WHERE id LIKE '%'")
    R.enrich_incident(seeded, iid)

    current = R.current_resources(seeded, iid)
    assert current["metrics"]["personnel"]["value"] == 634
    # the earlier figure is still in the ledger, superseded rather than erased
    total = seeded.execute(
        "SELECT COUNT(*) AS n FROM facts WHERE incident_id = ? AND kind = 'resource'", (iid,)
    ).fetchone()["n"]
    assert total >= 4


def test_no_match_reports_why(seeded, monkeypatch):
    iid = _incident(seeded, name="Nonexistent Fire")
    _serve(monkeypatch, [])
    result = R.enrich_incident(seeded, iid)
    assert result["matched"] is False and "no WFIGS record" in result["reason"]


def test_match_without_figures_is_not_a_failure(seeded, monkeypatch):
    iid = _incident(seeded)
    feature = _feature(personnel=None)
    feature["attributes"]["IncidentComplexityLevel"] = None
    feature["attributes"]["FireMgmtComplexity"] = None
    _serve(monkeypatch, [feature])
    result = R.enrich_incident(seeded, iid)
    assert result["matched"] is True and result["recorded"] == 0


def test_free_text_resource_facts_stay_as_notes(seeded, monkeypatch):
    """Media wording ('23 engines working') is kept, but never parsed into a
    number we would then present as official."""
    iid = _incident(seeded)
    seeded.execute(
        "INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,"
        " confidence, observed_at, ingested_at)"
        " VALUES ('f-txt', ?, 'e1', 's1', 4, '23 engines are working the fire', 'resource',"
        " 0.6, ?, ?)",
        (iid, NOW, NOW),
    )
    current = R.current_resources(seeded, iid)
    assert current["metrics"] == {}
    assert current["notes"][0]["content"].startswith("23 engines")


def test_unknown_incident_raises(seeded):
    with pytest.raises(ValueError):
        R.enrich_incident(seeded, "nope")
