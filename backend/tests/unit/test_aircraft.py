"""Aircraft over active fires.

Two things matter: an airliner passing at cruise is not air operations and must
not bury the tankers, and "fire ops" is a heuristic surfaced for the operator —
never asserted as fact and never written to the ledger.
"""

from toast.pipeline.aircraft import (
    _is_fire_ops,
    current_aircraft,
    observed_over_incident,
    poll_incident,
)

NOW = "2026-07-19T00:00:00.000Z"


class TestFireOpsHeuristic:
    def test_a_firefighting_type_is_air_ops(self):
        assert _is_fire_ops(alt_ft=4000, type_code="S2", category="A3") is True

    def test_a_firefighting_type_counts_even_climbing_out(self):
        # a CAL FIRE S-2 tanker climbing after a drop is still fire ops
        assert _is_fire_ops(alt_ft=11000, type_code="S2", category="A3") is True

    def test_a_low_rotorcraft_is_air_ops(self):
        # ADS-B category A7 = rotorcraft; low near a fire = plausibly working it
        assert _is_fire_ops(alt_ft=4000, type_code="EC35", category="A7") is True

    def test_an_airliner_on_approach_is_not_air_ops(self):
        # the exact false positive the loose 'low + slow' rule produced
        assert _is_fire_ops(alt_ft=4000, type_code="A388", category="A5") is False
        assert _is_fire_ops(alt_ft=400, type_code="BCS1", category="A3") is False


class FakeAircraft:
    """Stand-in for airplanes.live so the poller is tested without the network."""

    def __init__(self, monkeypatch, returns):
        import toast.pipeline.aircraft as mod

        monkeypatch.setattr(mod, "_fetch_near", lambda lat, lon: returns)


def _incident(db, iid="i1", lat=40.9, lon=-121.3):
    # the seeded fixture already provides active incident 'i1'; give it coordinates
    db.execute("UPDATE incidents SET lat = ?, lon = ? WHERE id = ?", (lat, lon, iid))
    return db.execute("SELECT * FROM incidents WHERE id = ?", (iid,)).fetchone()


def test_transit_aircraft_are_not_stored(seeded, monkeypatch):
    """A jet at 35,000 ft over the fire is noise; only the low ones are kept."""
    inc = _incident(seeded)
    FakeAircraft(
        monkeypatch,
        [
            {"hex": "a1", "lat": 40.9, "lon": -121.3, "alt_baro": 35000, "gs": 450, "t": "B738"},
            {
                "hex": "a2",
                "lat": 40.91,
                "lon": -121.31,
                "alt_baro": 4200,
                "gs": 90,
                "t": "H60",
                "flight": "COPTER1",
                "desc": "Sikorsky UH-60",
            },
        ],
    )
    stored = poll_incident(seeded, inc)
    assert stored == 1  # the airliner is dropped
    row = seeded.execute("SELECT hex, fire_ops, descr FROM aircraft_positions").fetchone()
    assert row["hex"] == "a2" and row["fire_ops"] == 1


def test_current_aircraft_carries_a_trail(seeded, monkeypatch):
    inc = _incident(seeded)
    for lon in (-121.30, -121.31, -121.30):  # a little orbit
        FakeAircraft(
            monkeypatch,
            [{"hex": "h1", "lat": 40.9, "lon": lon, "alt_baro": 5000, "gs": 110, "t": "S2"}],
        )
        poll_incident(seeded, inc)
    result = current_aircraft(seeded, minutes=60)
    assert result["count"] == 1
    assert result["fire_ops"] == 1
    assert len(result["aircraft"][0]["trail"]) == 3  # every position on the orbit


def test_observed_over_incident_counts_distinct_fire_ops(seeded, monkeypatch):
    inc = _incident(seeded)
    FakeAircraft(
        monkeypatch,
        [
            {"hex": "t1", "lat": 40.9, "lon": -121.3, "alt_baro": 5000, "gs": 100, "t": "S2"},
            {"hex": "t2", "lat": 40.9, "lon": -121.3, "alt_baro": 4000, "gs": 90, "t": "H60"},
            {"hex": "x1", "lat": 40.9, "lon": -121.3, "alt_baro": 14000, "gs": 300, "t": "C172"},
        ],
    )
    poll_incident(seeded, inc)
    poll_incident(seeded, inc)  # same aircraft again — must not double-count
    assert observed_over_incident(seeded, "i1") == 2  # t1 and t2, not the non-ops x1
