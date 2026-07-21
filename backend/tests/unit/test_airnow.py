"""AirNow smoke/AQI.

Three things matter: a missing key is a documented state (never an error that
dead-letters or 500s), the key never survives into an error path (declared as
a fetch secret), and the surfaced number is the WORST current reading with its
reporting area named — the monitor can be a town over from the fire.
"""

import json

from toast.pipeline import airnow

KEY = "aaaabbbb-cccc-dddd-eeee-ffff00001111"


def _with_key(db):
    db.execute(
        "INSERT OR REPLACE INTO credentials (name, value, updated_at)"
        " VALUES ('AIRNOW_API_KEY', ?, '2026-07-19T00:00:00.000Z')",
        (KEY,),
    )
    db.execute("UPDATE incidents SET lat = 39.76, lon = -121.62 WHERE id = 'i1'")


def test_no_key_is_a_state_not_an_error(seeded):
    seeded.execute("UPDATE incidents SET lat = 39.76, lon = -121.62 WHERE id = 'i1'")
    out = airnow.aqi_for(seeded, "i1")
    assert out["available"] is False
    assert "not configured" in out["reason"]


def test_worst_reading_wins_and_area_is_named(seeded, monkeypatch):
    _with_key(seeded)
    body = json.dumps(
        [
            {"ParameterName": "O3", "AQI": 42, "Category": {"Name": "Good"},
             "ReportingArea": "Chico", "HourObserved": 14},
            {"ParameterName": "PM2.5", "AQI": 158, "Category": {"Name": "Unhealthy"},
             "ReportingArea": "Chico", "HourObserved": 14},
        ]
    ).encode()
    seen = {}

    def fake_fetch(url, **kw):
        seen["url"] = url
        seen["secrets"] = kw.get("secrets")
        return body, "application/json"

    monkeypatch.setattr(airnow, "fetch", fake_fetch)
    out = airnow.aqi_for(seeded, "i1")
    assert out["worst"]["aqi"] == 158 and out["worst"]["parameter"] == "PM2.5"
    assert "Chico" in out["note"]
    # the key is declared a secret so a 401 can never carry it into a log
    assert KEY in seen["url"] and KEY in (seen["secrets"] or ())


def test_hourly_cache_spares_the_api(seeded, monkeypatch):
    _with_key(seeded)
    calls = []

    def fake_fetch(url, **kw):
        calls.append(url)
        return b"[]", "application/json"

    monkeypatch.setattr(airnow, "fetch", fake_fetch)
    airnow.aqi_for(seeded, "i1")
    airnow.aqi_for(seeded, "i1")
    assert len(calls) == 1
