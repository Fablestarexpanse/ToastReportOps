"""AIR mode backend.

The invariant under test everywhere: airplanes.live allows ~1 request/second
for the WHOLE application, so every fast path must throttle upstream and
degrade to store reads — never to API abuse. And borrowed live data stays out
of the ledger: the activity log is a view over the position store.
"""

import json

from toast.pipeline import air, aircraft

NOW = "2026-07-19T00:00:00.000Z"


def _incident(db, iid="i1", lat=40.9, lon=-121.3):
    db.execute("UPDATE incidents SET lat = ?, lon = ? WHERE id = ?", (lat, lon, iid))
    return db.execute("SELECT * FROM incidents WHERE id = ?", (iid,)).fetchone()


def _fake_upstream(monkeypatch, returns, calls):
    def fake(lat, lon):
        calls.append((lat, lon))
        return returns

    monkeypatch.setattr(aircraft, "_fetch_near", fake)


class TestFocusThrottle:
    def test_rapid_polls_hit_upstream_once(self, seeded, monkeypatch):
        """A 2-second UI poll must not become a 2-second upstream poll."""
        _incident(seeded)
        calls = []
        _fake_upstream(
            monkeypatch,
            [{"hex": "t1", "lat": 40.9, "lon": -121.3, "alt_baro": 5000, "gs": 120, "t": "S2"}],
            calls,
        )
        first = air.focus_poll(seeded, "i1")
        second = air.focus_poll(seeded, "i1")  # immediately after
        assert len(calls) == 1, "second poll inside the window hit upstream"
        assert first["polled_upstream"] is True
        assert second["polled_upstream"] is False
        assert second["count"] == 1  # the read-through still serves the picture

    def test_unknown_incident_raises(self, seeded):
        import pytest

        with pytest.raises(ValueError):
            air.focus_poll(seeded, "nope")

    def test_trail_carries_the_orbit(self, seeded, monkeypatch):
        inc = _incident(seeded)
        for lon in (-121.30, -121.31, -121.32):
            _fake_upstream(
                monkeypatch,
                [{"hex": "t1", "lat": 40.9, "lon": lon, "alt_baro": 5000, "gs": 120, "t": "S2"}],
                [],
            )
            aircraft.poll_incident(seeded, inc)
        out = air.focus_poll(seeded, "i1")
        assert out["count"] == 1
        # three seeded orbit points plus focus_poll's own upstream hit
        assert len(out["aircraft"][0]["trail"]) == 4


class TestActivityLog:
    def _pos(self, db, hex_, seen_at, flight=None):
        db.execute(
            """INSERT INTO aircraft_positions
               (hex, incident_id, flight, type, descr, lat, lon, alt_ft, gs, track,
                fire_ops, role, military, seen_at)
               VALUES (?, 'i1', ?, 'S2', 'Grumman S-2T', 40.9, -121.3, 5000, 120, 90,
                       1, 'tanker', 0, ?)""",
            (hex_, flight, seen_at),
        )

    def test_a_gap_splits_two_sorties(self, seeded):
        """Load-and-return is exactly this pattern for a tanker."""
        self._pos(seeded, "t1", "2026-07-19T00:00:00.000Z", "TKR82")
        self._pos(seeded, "t1", "2026-07-19T00:05:00.000Z")
        # 40 minutes silent — flew back to base, reloaded
        self._pos(seeded, "t1", "2026-07-19T00:45:00.000Z")
        self._pos(seeded, "t1", "2026-07-19T00:50:00.000Z")
        log = air.activity_log(seeded, "i1")
        assert log["count"] == 2
        assert all(s["flight"] == "TKR82" or s["hex"] == "t1" for s in log["sessions"])
        newest = log["sessions"][0]
        assert newest["arrived"] == "2026-07-19T00:45:00.000Z"
        assert newest["on_station_s"] == 300

    def test_two_aircraft_do_not_merge(self, seeded):
        self._pos(seeded, "t1", "2026-07-19T00:00:00.000Z")
        self._pos(seeded, "h1", "2026-07-19T00:01:00.000Z")
        assert air.activity_log(seeded, "i1")["count"] == 2


class TestMil:
    def test_mil_is_clipped_to_california_and_cached(self, seeded, monkeypatch):
        calls = []

        def fake_fetch(url, **kw):
            calls.append(url)
            body = json.dumps(
                {
                    "ac": [
                        {
                            "hex": "ae01",
                            "lat": 39.5,
                            "lon": -121.0,
                            "t": "C130",
                            "alt_baro": 8000,
                            "gs": 200,
                            "flight": "MAFFS3",
                        },
                        {
                            "hex": "ae02",
                            "lat": 48.0,
                            "lon": -100.0,
                            "t": "F16",
                            "alt_baro": 30000,
                            "gs": 400,
                        },
                    ]
                }
            ).encode()
            return body, "application/json"

        monkeypatch.setattr(air, "fetch", fake_fetch)
        out = air.mil_snapshot(seeded)
        assert out["count"] == 1  # North Dakota does not make the cut
        assert out["aircraft"][0]["flight"] == "MAFFS3"
        assert "/mil" in calls[0]

        again = air.mil_snapshot(seeded)  # inside the 30 s window
        assert len(calls) == 1, "cached window hit upstream again"
        assert again["count"] == 1


class TestBaseWatch:
    def test_rotation_scans_one_base_per_window_and_flags_departure(self, seeded, monkeypatch):
        scanned = []

        def fake_fetch(url, **kw):
            scanned.append(url)
            body = json.dumps(
                {
                    "ac": [
                        {
                            "hex": "a1",
                            "lat": 39.79,
                            "lon": -121.85,
                            "t": "S2",
                            "alt_baro": "ground",
                            "flight": "TKR91",
                        }
                    ]
                }
            ).encode()
            return body, "application/json"

        monkeypatch.setattr(air, "fetch", fake_fetch)
        out = air.base_watch(seeded)
        assert len(scanned) == 1  # exactly one base per allowed window
        assert out["last_scan"]["base"] == air.BASES[0]["id"]
        assert out["sightings"][0]["flight"] == "TKR91"
        assert out["sightings"][0]["airborne"] is False

        # inside the window: no upstream, same picture
        again = air.base_watch(seeded)
        assert len(scanned) == 1
        assert again["sightings"][0]["hex"] == "a1"

        # next window scans the NEXT base; the hull absent from its base scan
        # would be flagged departed when that base's turn comes round again
        seeded.execute("DELETE FROM map_cache WHERE key = 'air:throttle:bases'")
        seeded.commit()
        air.base_watch(seeded)
        assert len(scanned) == 2
