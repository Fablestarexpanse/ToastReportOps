"""County boundaries overlay: cached, generalized, degrades to stale."""

from toast.pipeline import countylines


def _fc(n=2):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 0], [1, 1]]],
                },
                "properties": {"COUNTY_NAME": f"County {i}"},
            }
            for i in range(n)
        ],
    }


def test_fetch_caches_and_serves(db, monkeypatch):
    monkeypatch.setattr(countylines, "fetch_county_lines", lambda: _fc(58))
    out = countylines.get_county_lines(db)
    assert out["count"] == 58 and not out.get("cached")

    # second call comes from the 30-day cache, no network
    def _boom():
        raise RuntimeError("no network expected")

    monkeypatch.setattr(countylines, "fetch_county_lines", _boom)
    again = countylines.get_county_lines(db)
    assert again["cached"] and again["count"] == 58


def test_failure_degrades_to_stale_then_empty(db, monkeypatch):
    monkeypatch.setattr(countylines, "fetch_county_lines", lambda: _fc(3))
    countylines.get_county_lines(db)
    # expire the cache and make the fetch fail: stale beats nothing
    db.execute(
        "UPDATE map_cache SET fetched_at = '2020-01-01T00:00:00.000Z' WHERE key = ?",
        (countylines.CACHE_KEY,),
    )

    def boom():
        raise RuntimeError("down")

    monkeypatch.setattr(countylines, "fetch_county_lines", boom)
    out = countylines.get_county_lines(db)
    assert out["stale"] and out["count"] == 3
    # no cache at all -> honest empty payload, never an exception
    db.execute("DELETE FROM map_cache WHERE key = ?", (countylines.CACHE_KEY,))
    empty = countylines.get_county_lines(db)
    assert empty["unavailable"] and empty["features"] == []
