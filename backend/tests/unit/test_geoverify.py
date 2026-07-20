"""Origin verification — zooming to street level must not imply false precision."""

import pytest

from toast.pipeline import geoverify as G

NOW = "2026-07-19T12:00:00.000Z"
# Paradise, Butte County
LAT, LON = 39.7596, -121.6219


def _incident(
    db, *, lat=LAT, lon=LON, county="Butte", calfire_id="cf-1", iid="i2", name="TEST FIRE"
):
    db.execute(
        """INSERT INTO incidents (id, canonical_name, status, lat, lon, county, calfire_id,
                                  created_at, updated_at)
           VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?)""",
        (iid, name, lat, lon, county, calfire_id, NOW, NOW),
    )
    return iid


def _gnis(db, name, county, lat, lon, cls="Populated Place"):
    db.execute(
        """INSERT INTO gnis_features (feature_id, name, name_norm, feature_class, state,
                                      county, lat, lon)
           VALUES (?, ?, ?, ?, 'California', ?, ?, ?)""",
        (abs(hash(name)) % 10**8, name, name.lower(), cls, county, lat, lon),
    )


def _calfire(monkeypatch, records):
    monkeypatch.setattr(G, "haversine_km", G.haversine_km)  # keep real distance math

    def fake_fetch(capture, *, inactive=False, year=None):
        return records if not inactive else []

    import toast.sources.calfire as cf

    monkeypatch.setattr(cf, "fetch_incidents", fake_fetch)


def test_matching_calfire_position_verifies(seeded, monkeypatch):
    iid = _incident(seeded)
    _gnis(seeded, "Paradise", "Butte", LAT, LON)
    _calfire(monkeypatch, [{"UniqueId": "cf-1", "Latitude": LAT, "Longitude": LON, "Name": "TEST"}])

    result = G.verify_origin(seeded, iid)
    assert result["verified"] is True
    assert result["confidence"] == "high"
    assert result["official"]["delta_km"] == 0.0


def test_drifted_calfire_position_is_flagged_not_corrected(seeded, monkeypatch):
    """CAL FIRE refining a point must be surfaced, never silently applied —
    the ledger is append-only and the operator decides."""
    iid = _incident(seeded)
    _gnis(seeded, "Paradise", "Butte", LAT, LON)
    _calfire(
        monkeypatch,
        [{"UniqueId": "cf-1", "Latitude": LAT + 0.2, "Longitude": LON, "Name": "TEST"}],
    )

    result = G.verify_origin(seeded, iid)
    assert result["verified"] is False
    match = next(c for c in result["checks"] if c["check"] == "matches CAL FIRE")
    assert match["ok"] is False and "differs" in match["detail"]
    # our stored coordinate is unchanged
    row = seeded.execute("SELECT lat FROM incidents WHERE id = ?", (iid,)).fetchone()
    assert row["lat"] == LAT


def test_point_outside_california_fails(seeded, monkeypatch):
    iid = _incident(seeded, lat=45.5, lon=-122.6, county="Multnomah")  # Portland
    _calfire(monkeypatch, [])
    result = G.verify_origin(seeded, iid)
    ca = next(c for c in result["checks"] if c["check"] == "in California")
    assert ca["ok"] is False
    assert result["verified"] is False


def test_wrong_county_is_caught(seeded, monkeypatch):
    iid = _incident(seeded, county="Shasta")  # claims Shasta, sits in Butte
    _gnis(seeded, "Paradise", "Butte", LAT, LON)
    _calfire(monkeypatch, [{"UniqueId": "cf-1", "Latitude": LAT, "Longitude": LON}])
    result = G.verify_origin(seeded, iid)
    county = next(c for c in result["checks"] if c["check"] == "county")
    assert county["ok"] is False and "Butte" in county["detail"]


def test_inferred_position_is_not_presented_as_reported(seeded, monkeypatch):
    """A GNIS-inferred point must never look like a surveyed origin."""
    iid = _incident(seeded, calfire_id=None)
    seeded.execute(
        "INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,"
        " confidence, observed_at, ingested_at, lat, lon, geo_method)"
        " VALUES ('f-inf', ?, 'e1', 's1', 4, 'c', 'start', 0.6, ?, ?, ?, ?, 'named_feature')",
        (iid, NOW, NOW, LAT, LON),
    )
    _calfire(monkeypatch, [])
    result = G.verify_origin(seeded, iid)
    reported = next(c for c in result["checks"] if c["check"] == "reported position")
    assert reported["ok"] is False
    assert "approximate" in reported["detail"]
    assert result["verified"] is False


def test_calfire_outage_is_unknown_not_failure(seeded, monkeypatch):
    """An upstream outage must not read as a bad coordinate."""
    iid = _incident(seeded)
    _gnis(seeded, "Paradise", "Butte", LAT, LON)

    import toast.sources.calfire as cf

    def boom(capture, **kw):
        raise OSError("calfire unreachable")

    monkeypatch.setattr(cf, "fetch_incidents", boom)
    result = G.verify_origin(seeded, iid)
    match = next(c for c in result["checks"] if c["check"] == "matches CAL FIRE")
    assert match["ok"] is None
    assert result["confidence"] == "medium"  # unknown, not failed


def test_incident_without_coordinates(seeded, monkeypatch):
    iid = _incident(seeded, lat=None, lon=None)
    _calfire(monkeypatch, [])
    result = G.verify_origin(seeded, iid)
    assert result["verified"] is False and result["confidence"] == "none"


def test_unknown_incident_raises(seeded):
    with pytest.raises(ValueError):
        G.verify_origin(seeded, "nope")
