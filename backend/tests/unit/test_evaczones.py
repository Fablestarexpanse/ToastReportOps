"""Cal OES evacuation zones — the layer where doctrine matters most.

Statuses and official text pass through VERBATIM; zones match to the nearest
active fire; the 5-minute upstream cadence is respected by the cache.
"""

import json

from toast.pipeline import evaczones


def _zone(zone_id, status, lat, lon, info=None):
    d = 0.02
    return {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d], [lon - d, lat - d]]
            ],
        },
        "properties": {
            "COUNTY": "BUTTE",
            "ZONE_ID": zone_id,
            "STATUS": status,
            "CRITICAL_INFO": info,
            "EDIT_DATE": 1752900000000,
        },
    }


def _fake(monkeypatch, features):
    calls = []

    def fake_fetch(url, **kw):
        calls.append(url)
        body = json.dumps({"type": "FeatureCollection", "features": features})
        return body.encode(), "application/json"

    monkeypatch.setattr(evaczones, "fetch", fake_fetch)
    return calls


def test_statuses_pass_through_verbatim_and_zones_match_fires(seeded, monkeypatch):
    seeded.execute("UPDATE incidents SET lat = 39.76, lon = -121.62 WHERE id = 'i1'")
    _fake(
        monkeypatch,
        [
            _zone("US-CA-XBU-PDSE-014", "Evacuation Order", 39.75, -121.60,
                  "Leave now via Skyway southbound."),
            _zone("US-CA-XTU-PVL-E044", "Evacuation Warning", 36.06, -118.93),
        ],
    )
    out = evaczones.get_evac_zones(seeded, refresh=True)
    assert out["count"] == 2
    near = next(f for f in out["features"] if f["properties"]["ZONE_ID"].startswith("US-CA-XBU"))
    # verbatim: the exact county wording, untouched
    assert near["properties"]["STATUS"] == "Evacuation Order"
    assert near["properties"]["CRITICAL_INFO"] == "Leave now via Skyway southbound."
    # matched to the fire it surrounds; the far zone is not
    assert near["properties"]["incident_id"] == "i1"
    far = next(f for f in out["features"] if f["properties"]["ZONE_ID"].startswith("US-CA-XTU"))
    assert far["properties"]["incident_id"] is None
    assert out["statuses"] == {"Evacuation Order": 1, "Evacuation Warning": 1}
    assert "verbatim" in out["note"]


def test_cache_respects_the_five_minute_upstream_cadence(seeded, monkeypatch):
    calls = _fake(monkeypatch, [])
    evaczones.get_evac_zones(seeded, refresh=True)
    evaczones.get_evac_zones(seeded)  # within TTL: served from cache
    assert len(calls) == 1


def test_edit_date_becomes_iso_for_the_age_chip(seeded, monkeypatch):
    _fake(monkeypatch, [_zone("Z1", "Evacuation Order", 39.75, -121.60)])
    out = evaczones.get_evac_zones(seeded, refresh=True)
    assert out["features"][0]["properties"]["EDIT_DATE"].endswith("Z")
