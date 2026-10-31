"""Aircraft over active fires.

Two things matter: an airliner passing at cruise is not air operations and must
not bury the tankers, and "fire ops" is a heuristic surfaced for the operator —
never asserted as fact and never written to the ledger.
"""

from toast.pipeline.aircraft import (
    _is_fire_ops,
    classify_role,
    current_aircraft,
    incident_roster,
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


class TestRoleClassification:
    def test_a_unique_fire_airframe_is_a_tanker(self):
        # only fire flies an S-2T or an Air Tractor — type alone is enough
        assert classify_role("S2", "A3", 6000) == "tanker"
        assert classify_role("AT802", "A2", 3000) == "tanker"
        assert classify_role("OV10", "A2", 7000) == "air_attack"

    def test_a_fire_callsign_beats_the_airframe(self):
        # a DC-10 leased to anyone, flying as TANKER 914, is a tanker
        assert classify_role("DC10", "A5", 8000, "TKR914", "BC AIRCRAFT LEASING") == "tanker"
        assert classify_role("BE20", "A2", 6000, "ATGS7", None) == "air_attack"

    def test_a_shared_airframe_needs_a_fire_operator(self):
        # a FedEx DC-10 (shared airframe, no fire callsign, cargo operator) is NOT
        # a tanker — this is the exact false positive that started this
        assert classify_role("DC10", "A5", 8000, "FDX1234", "FEDERAL EXPRESS") == "other"
        assert classify_role("DC10", "A5", 8000, None, "10 TANKER AIR CARRIER") == "tanker"

    def test_a_cargo_caravan_is_not_air_attack(self):
        # the C208 feeders (PCM/BXR/CFS callsigns) that flooded the roster
        assert classify_role("C208", "A1", 6000, "PCM7654", "WEST AIR") == "other"

    def test_a_low_rotorcraft_is_a_helicopter(self):
        assert classify_role("H60", "A7", 3000) == "helicopter"
        assert classify_role("ZZZZ", "A7", 2000) == "helicopter"  # transponder says rotor

    def test_an_airliner_is_other(self):
        assert classify_role("B738", "A3", 6000, "SWA22", "SOUTHWEST") == "other"

    def test_military_flag_reads_dbflags_bit_zero(self):
        from toast.pipeline.aircraft import is_military

        assert is_military(1) is True and is_military(3) is True
        assert is_military(2) is False and is_military(None) is False and is_military(0) is False


class TestRoster:
    def test_roster_lists_aircraft_with_their_role(self, seeded, monkeypatch):
        inc = _incident(seeded)
        FakeAircraft(
            monkeypatch,
            [
                {
                    "hex": "t1",
                    "lat": 40.9,
                    "lon": -121.3,
                    "alt_baro": 6000,
                    "gs": 150,
                    "t": "S2",
                    "flight": "TANKER 82",
                    "desc": "Grumman S-2T",
                },
                {
                    "hex": "h1",
                    "lat": 40.9,
                    "lon": -121.3,
                    "alt_baro": 2000,
                    "gs": 60,
                    "t": "H60",
                    "category": "A7",
                    "desc": "Sikorsky UH-60",
                },
                {
                    "hex": "x1",
                    "lat": 40.9,
                    "lon": -121.3,
                    "alt_baro": 9000,
                    "gs": 200,
                    "t": "C172",
                    "desc": "Cessna 172",
                },
            ],
        )
        poll_incident(seeded, inc)
        roster = incident_roster(seeded, "i1")
        assert len(roster) == 3
        by_hex = {r["hex"]: r for r in roster}
        assert by_hex["t1"]["role"] == "tanker" and by_hex["t1"]["flight"] == "TANKER 82"
        assert by_hex["h1"]["role"] == "helicopter"
        assert by_hex["x1"]["role"] == "other" and by_hex["x1"]["fire_ops"] is False
        # firefighting aircraft sort ahead of the unclassified ones
        assert roster[0]["fire_ops"] is True


class TestPhoto:
    def test_photo_is_fetched_cached_and_credited(self, seeded, monkeypatch, tmp_path):
        monkeypatch.setenv("TOAST_DATA_DIR", str(tmp_path))  # the CAS write must not land in ./data
        from toast.pipeline import aircraft_photo

        calls = []

        def fake_fetch(url, **kw):
            calls.append(url)
            if url.endswith(".jpg"):  # the CDN image download
                return bytes.fromhex("ffd8ff") + b"-fake-jpeg", "image/jpeg"
            body = (
                b'{"photos":[{"thumbnail":{"src":"https://t/thumb.jpg"},'
                b'"thumbnail_large":{"src":"https://t/large.jpg"},'
                b'"photographer":"Jane Doe","link":"https://planespotters.net/photo/1"}]}'
            )
            return body, "application/json"

        monkeypatch.setattr(aircraft_photo, "fetch", fake_fetch)
        first = aircraft_photo.get_photo(seeded, "a1b2c3")
        assert first["found"] is True
        assert first["photographer"] == "Jane Doe"  # credit rides with the image
        assert first["image_sha"]  # the picture was saved to this hull's ID

        # and the saved bytes are readable back from our own store
        img = aircraft_photo.image_bytes(seeded, "a1b2c3")
        assert img is not None and img[0].startswith(bytes.fromhex("ffd8ff"))

        second = aircraft_photo.get_photo(seeded, "A1B2C3")  # same hull, any case
        assert second["cached"] is True
        assert len([c for c in calls if not c.endswith(".jpg")]) == 1  # planespotters hit once

    def test_no_photo_is_remembered_so_it_is_not_re_requested(self, seeded, monkeypatch):
        from toast.pipeline import aircraft_photo

        calls = []

        def empty(url, **kw):
            calls.append(url)
            return b'{"photos":[]}', "application/json"

        monkeypatch.setattr(aircraft_photo, "fetch", empty)
        assert aircraft_photo.get_photo(seeded, "ffffff")["found"] is False
        aircraft_photo.get_photo(seeded, "ffffff")  # asks again
        assert len(calls) == 1  # the 'none exists' answer was cached too
