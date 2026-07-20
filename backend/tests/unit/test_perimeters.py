"""Perimeter matching — a polygon must only carry an incident id we can defend.

The whole value of the layer is that the outline on screen belongs to the fire
named in the panel. A wrong match is worse than no match, so the rule is
conservative: same normalized name AND nearby.
"""

from toast.pipeline.perimeters import _mapped_latest, _match_incidents

NOW = "2026-07-19T00:00:00.000Z"


def _feature(name, lat, lon, *, acres=100.0, date_ms=None):
    props = {"poly_IncidentName": name, "poly_GISAcres": acres}
    if date_ms is not None:
        props["poly_DateCurrent"] = date_ms
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [lon - 0.01, lat - 0.01],
                    [lon + 0.01, lat - 0.01],
                    [lon + 0.01, lat + 0.01],
                    [lon - 0.01, lat + 0.01],
                    [lon - 0.01, lat - 0.01],
                ]
            ],
        },
    }


def _incident(db, iid, name, lat, lon):
    db.execute(
        "INSERT INTO incidents (id, canonical_name, status, lat, lon, created_at, updated_at)"
        " VALUES (?, ?, 'active', ?, ?, ?, ?)",
        (iid, name, lat, lon, NOW, NOW),
    )


def test_name_and_location_match_tags_the_incident(seeded):
    _incident(seeded, "i-brook", "Brook Fire", 40.0, -122.0)
    # WFIGS writes bare names in caps — normalization is what makes this match
    features = [_feature("BROOK", 40.0, -122.0)]
    assert _match_incidents(seeded, features) == 1
    assert features[0]["properties"]["incident_id"] == "i-brook"
    assert features[0]["properties"]["incident_name"] == "Brook Fire"


def test_same_name_far_away_is_not_matched(seeded):
    """Fire names repeat across California every season."""
    _incident(seeded, "i-brook", "Brook Fire", 40.0, -122.0)
    features = [_feature("BROOK", 34.0, -118.0)]  # ~700 km south
    assert _match_incidents(seeded, features) == 0
    assert features[0]["properties"]["incident_id"] is None


def test_unmatched_perimeter_still_carries_an_explicit_null(seeded):
    features = [_feature("SOMEWHERE ELSE", 39.0, -121.0)]
    assert _match_incidents(seeded, features) == 0
    assert features[0]["properties"]["incident_id"] is None


def test_incident_without_coordinates_matches_on_name_alone(seeded):
    seeded.execute(
        "INSERT INTO incidents (id, canonical_name, status, created_at, updated_at)"
        " VALUES ('i-nogeo', 'Loomis Fire', 'active', ?, ?)",
        (NOW, NOW),
    )
    features = [_feature("LOOMIS", 39.0, -121.0)]
    assert _match_incidents(seeded, features) == 1
    assert features[0]["properties"]["incident_id"] == "i-nogeo"


def test_frozen_incidents_are_not_matched(seeded):
    _incident(seeded, "i-old", "Wagon Fire", 40.0, -122.0)
    seeded.execute("UPDATE incidents SET status = 'frozen' WHERE id = 'i-old'")
    features = [_feature("WAGON", 40.0, -122.0)]
    assert _match_incidents(seeded, features) == 0


def test_mapped_latest_reports_the_newest_flight_not_our_fetch_time():
    features = [
        _feature("A", 40.0, -122.0, date_ms=1_752_000_000_000),
        _feature("B", 40.0, -122.0, date_ms=1_753_000_000_000),
    ]
    assert _mapped_latest(features) == "2025-07-20T08:26:40.000Z"


def test_mapped_latest_is_null_when_the_feed_omits_dates():
    assert _mapped_latest([_feature("A", 40.0, -122.0)]) is None
