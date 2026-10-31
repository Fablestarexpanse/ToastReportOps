"""California county boundaries — the reference overlay.

Evacuations are ordered by counties, fires are dispatched by county units, and
the operator thinks in counties — so the map needs the lines. Source is the
CAL FIRE org's public ArcGIS layer (same host as FIRIS), pulled generalized
(~500 m tolerance — county lines at state scale, not survey data) so the whole
state is ~115 KB. Boundaries do not move; the cache lives for 30 days.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any
from urllib.parse import urlencode

from toast.core.db import utcnow
from toast.core.fetch import FetchError, fetch
from toast.core.logging import get_logger

SERVICE = (
    "https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/arcgis/rest/services/"
    "California_County_Boundaries/FeatureServer/0/query"
)
CACHE_KEY = "countylines:ca"
CACHE_TTL_S = 30 * 24 * 3600  # county lines do not move


def fetch_county_lines() -> dict[str, Any]:
    query = urlencode(
        {
            "where": "1=1",
            "outFields": "COUNTY_NAME",
            "returnGeometry": "true",
            "outSR": 4326,
            "geometryPrecision": 3,
            # generalize: an overlay wants legible lines, not survey geometry —
            # this is what turns an 11 MB payload into ~115 KB
            "maxAllowableOffset": 0.004,
            "f": "geojson",
        }
    )
    body, _ctype = fetch(
        f"{SERVICE}?{query}",
        accept_types=("application/json", "application/geo+json", "text/plain"),
        max_bytes=8 * 1024 * 1024,
    )
    data = json.loads(body)
    if data.get("type") != "FeatureCollection":
        raise RuntimeError(f"county layer returned no collection: {str(data)[:120]}")
    return data


def get_county_lines(conn: sqlite3.Connection, *, refresh: bool = False) -> dict[str, Any]:
    """All 58 county boundaries, generalized for overlay use."""
    if not refresh:
        row = conn.execute(
            """SELECT payload, fetched_at FROM map_cache
               WHERE key = ? AND unixepoch('now') - unixepoch(fetched_at) < ?""",
            (CACHE_KEY, CACHE_TTL_S),
        ).fetchone()
        if row:
            cached = json.loads(row["payload"])
            cached["cached"] = True
            return cached

    try:
        data = fetch_county_lines()
    except (FetchError, ValueError, RuntimeError) as exc:
        get_logger("countylines").warning("county_lines_fetch_failed", error=repr(exc))
        stale = conn.execute("SELECT payload FROM map_cache WHERE key = ?", (CACHE_KEY,)).fetchone()
        if stale:
            out = json.loads(stale["payload"])
            out["cached"] = True
            out["stale"] = True
            return out
        return {"type": "FeatureCollection", "features": [], "count": 0, "unavailable": True}

    features = data.get("features", [])
    payload: dict[str, Any] = {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "source": "California County Boundaries (CAL FIRE public ArcGIS)",
        "fetched_at": utcnow(),
    }
    conn.execute(
        "INSERT OR REPLACE INTO map_cache (key, payload, fetched_at) VALUES (?, ?, ?)",
        (CACHE_KEY, json.dumps(payload), payload["fetched_at"]),
    )
    conn.commit()
    get_logger("countylines").info("county_lines_fetched", features=len(features))
    return payload
