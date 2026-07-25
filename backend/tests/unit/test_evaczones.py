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


def test_split_siblings_stay_on_one_fire(seeded, monkeypatch):
    """A county that splits ICU-123 into A/B/C/D describes ONE place. Matching
    each sibling to its own nearest fire scattered them across two incidents and
    mis-counted both fires' evacuations; the closest sibling decides for all."""
    seeded.execute("UPDATE incidents SET lat = 37.00, lon = -118.00 WHERE id = 'i1'")
    seeded.execute(
        "INSERT INTO incidents (id, canonical_name, status, lat, lon, created_at, updated_at)"
        " VALUES ('i2', 'OTHER FIRE', 'active', 37.30, -118.00,"
        " '2026-07-19T00:00:00.000Z', '2026-07-19T00:00:00.000Z')"
    )
    _fake(
        monkeypatch,
        [
            _zone("US-CA-XIN-ICU-123-A", "Evacuation Order", 37.05, -118.00),  # nearer i1
            _zone("US-CA-XIN-ICU-123-D", "Evacuation Order", 37.20, -118.00),  # nearer i2
        ],
    )
    out = evaczones.get_evac_zones(seeded, refresh=True)
    ids = {f["properties"]["ZONE_ID"]: f["properties"] for f in out["features"]}
    a = ids["US-CA-XIN-ICU-123-A"]
    d = ids["US-CA-XIN-ICU-123-D"]
    # both land on the fire the CLOSEST sibling picked — not one each
    assert a["incident_id"] == d["incident_id"] == "i1"
    assert d["regrouped_with"] == "US-CA-XIN-ICU-123-A"
    # and each keeps its own full public identity
    assert a["zone_label"] == "ICU-123-A"
    assert d["zone_label"] == "ICU-123-D"


def test_hazard_is_tagged_so_a_flood_is_not_shown_as_a_fire(seeded, monkeypatch):
    fire = _zone("US-CA-XIN-ICU-127", "Evacuation Order", 39.75, -121.60)
    fire["properties"]["NOTES"] = "Evacuation ORDER in place for an active wildfire."
    flood = _zone("US-CA-XTU-PVL-E044", "Evacuation Order", 36.06, -118.93)
    flood["properties"]["NOTES"] = "Flooding"
    _fake(monkeypatch, [fire, flood])
    out = evaczones.get_evac_zones(seeded, refresh=True)
    by = {f["properties"]["ZONE_ID"]: f["properties"] for f in out["features"]}
    assert by["US-CA-XIN-ICU-127"]["hazard"] == "fire"
    assert by["US-CA-XTU-PVL-E044"]["hazard"] == "flood"
    assert out["hazards"] == {"fire": 1, "flood": 1}


def test_cache_respects_the_five_minute_upstream_cadence(seeded, monkeypatch):
    calls = _fake(monkeypatch, [])
    evaczones.get_evac_zones(seeded, refresh=True)
    evaczones.get_evac_zones(seeded)  # within TTL: served from cache
    assert len(calls) == 1


def test_edit_date_becomes_iso_for_the_age_chip(seeded, monkeypatch):
    _fake(monkeypatch, [_zone("Z1", "Evacuation Order", 39.75, -121.60)])
    out = evaczones.get_evac_zones(seeded, refresh=True)
    assert out["features"][0]["properties"]["EDIT_DATE"].endswith("Z")
