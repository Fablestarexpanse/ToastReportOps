"""Fire-weather zones and Caltrans closures — the two honesty-labelled layers.

Under test: the closure filter keeps a full closure near an active fire and
drops routine lane work; a dead NWS zone degrades the layer instead of failing
it; and both payloads carry the labels the UI is required to show.
"""


from toast.pipeline import closures, firewx


class TestClosures:
    def _rec(self, type_of_closure, lat, lon, work="Emergency Work"):
        return {
            "lcs": {
                "location": {
                    "begin": {
                        "beginLatitude": str(lat),
                        "beginLongitude": str(lon),
                        "beginRoute": "SR-70",
                        "beginCounty": "Butte",
                        "beginNearbyPlace": "Paradise",
                    }
                },
                "closure": {
                    "typeOfClosure": type_of_closure,
                    "typeOfWork": work,
                    "closureTimestamp": {"closureStartDate": "2026-07-19"},
                },
            }
        }

    def test_full_near_fire_kept_lane_work_dropped(self, seeded, monkeypatch):
        seeded.execute("UPDATE incidents SET lat = 39.76, lon = -121.62 WHERE id = 'i1'")
        records = [
            self._rec("Full", 39.75, -121.60),          # full + near the fire -> kept
            self._rec("Lane", 39.75, -121.60),          # near, but only a lane -> dropped
            self._rec("Full", 34.0, -118.2),            # full, but 500 km away -> dropped
            # far from any fire but fire-worded -> kept
            self._rec("Full", 41.9, -124.1, "Fire equipment staging"),
        ]
        monkeypatch.setattr(closures, "_records", lambda d: records if d == 3 else [])
        out = closures.get_closures(seeded, refresh=True)
        assert out["count"] == 2
        near = [c for c in out["closures"] if c["near"]]
        assert near and near[0]["near"]["name"]  # tagged with the fire it serves
        # only district 3 returned records: 4 seen, 2 kept -> 2 dropped, counted
        assert out["dropped"] == 2
        assert "STATE HIGHWAYS ONLY" in out["note"]

    def test_a_failed_district_degrades_not_fails(self, seeded, monkeypatch):
        def boom(d):
            raise RuntimeError("district feed down")

        monkeypatch.setattr(closures, "_records", boom)
        out = closures.get_closures(seeded, refresh=True)
        assert out["count"] == 0
        assert len(out["failed_districts"]) == 12  # reported, not hidden


class TestFireWeather:
    def test_zone_resolution_and_dead_zone_degrades(self, seeded, monkeypatch):
        alerts = {
            "features": [
                {
                    "geometry": None,
                    "properties": {
                        "event": "Red Flag Warning",
                        "severity": "Severe",
                        "headline": "Red Flag Warning until 8 PM",
                        "affectedZones": [
                            "https://api.weather.gov/zones/fire/CAZ515",
                            "https://api.weather.gov/zones/fire/CAZ999",  # dead
                        ],
                    },
                },
                {
                    "geometry": None,
                    "properties": {"event": "Heat Advisory", "affectedZones": []},
                },
            ]
        }
        zone_geom = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}

        def fake_get(url, max_bytes=0):
            if "alerts" in url:
                return alerts
            if "CAZ515" in url:
                return {"geometry": zone_geom}
            raise RuntimeError("404")

        monkeypatch.setattr(firewx, "_get_json", fake_get)
        out = firewx.get_fire_weather(seeded, refresh=True)
        # the heat advisory is not fire weather and does not appear
        assert out["count"] == 1
        assert out["features"][0]["geometry"] == zone_geom
        assert out["features"][0]["properties"]["event"] == "Red Flag Warning"
        assert "FORECAST" in out["note"]

    def test_alert_with_no_resolvable_zone_is_listed_not_lost(self, seeded, monkeypatch):
        alerts = {
            "features": [
                {
                    "geometry": None,
                    "properties": {
                        "event": "Fire Weather Watch",
                        "headline": "Watch for wind",
                        "affectedZones": ["https://api.weather.gov/zones/fire/CAZ000"],
                    },
                }
            ]
        }

        def fake_get(url, max_bytes=0):
            if "alerts" in url:
                return alerts
            raise RuntimeError("404")

        monkeypatch.setattr(firewx, "_get_json", fake_get)
        out = firewx.get_fire_weather(seeded, refresh=True)
        assert out["count"] == 1
        assert out["features"][0]["properties"]["unresolved"] is True
        assert out["unresolved"] == 1

    def test_zone_shapes_cache_hard(self, seeded, monkeypatch):
        calls = []

        def fake_get(url, max_bytes=0):
            calls.append(url)
            if "alerts" in url:
                return {
                    "features": [
                        {
                            "geometry": None,
                            "properties": {
                                "event": "Red Flag Warning",
                                "affectedZones": ["https://api.weather.gov/zones/fire/CAZ515"],
                            },
                        }
                    ]
                }
            return {"geometry": {"type": "Polygon", "coordinates": []}}

        monkeypatch.setattr(firewx, "_get_json", fake_get)
        firewx.get_fire_weather(seeded, refresh=True)
        firewx.get_fire_weather(seeded, refresh=True)
        zone_calls = [c for c in calls if "zones" in c]
        assert len(zone_calls) == 1  # geography does not change between polls
