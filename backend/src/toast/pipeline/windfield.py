"""A coarse ambient wind field over California, from Open-Meteo (free, keyless).

The per-incident NWS winds (weather.py) are the AUTHORITATIVE arrows — station
-attributed, official, the ones an operator reports from. This is the opposite
and complementary: a low-authority BACKGROUND field on a fixed grid, so the map
shows the regional flow pushing every fire, not a lone arrow per incident.

Honesty: drawn dim and unlabeled, and NEVER a source for a published number.
Open-Meteo is a modelled forecast, not a station observation — useful as
context for where the air is going, not as a fact about any one fire.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from toast.core.db import utcnow
from toast.core.fetch import FetchError, fetch
from toast.core.logging import get_logger

SERVICE = "https://api.open-meteo.com/v1/forecast"
CACHE_KEY = "windfield:ca"
CACHE_TTL_S = 30 * 60  # the model updates hourly; Open-Meteo asks for restraint

# A grid over California + just offshore/inland margins. ~0.7 deg spacing keeps
# it to ~200 points — one Open-Meteo request, readable arrow density on the map.
LAT_MIN, LAT_MAX = 32.5, 42.1
LON_MIN, LON_MAX = -124.5, -114.3
STEP = 0.7


def _grid() -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    lat = LAT_MIN
    while lat <= LAT_MAX + 1e-9:
        lon = LON_MIN
        while lon <= LON_MAX + 1e-9:
            pts.append((round(lat, 2), round(lon, 2)))
            lon += STEP
        lat += STEP
    return pts


def get_wind_field(conn: sqlite3.Connection, *, refresh: bool = False) -> dict[str, Any]:
    """Gridded ambient wind across CA. Cached; a fetch failure degrades to the
    last good field (or empty) — the map must never break on a weather outage."""
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

    grid = _grid()
    lats = ",".join(f"{la:.2f}" for la, _ in grid)
    lons = ",".join(f"{lo:.2f}" for _, lo in grid)
    url = (
        f"{SERVICE}?latitude={lats}&longitude={lons}"
        "&current=wind_speed_10m,wind_direction_10m,wind_gusts_10m"
        "&wind_speed_unit=mph&timezone=UTC"
    )
    try:
        body, _ct = fetch(
            url, accept_types=("application/json", "text/plain"), max_bytes=4 * 1024 * 1024
        )
        data = json.loads(body)
    except (FetchError, ValueError) as exc:
        get_logger("windfield").warning("wind_field_fetch_failed", error=repr(exc))
        stale = conn.execute("SELECT payload FROM map_cache WHERE key = ?", (CACHE_KEY,)).fetchone()
        if stale:
            out = json.loads(stale["payload"])
            out["cached"] = True
            out["stale"] = True
            return out
        return {"arrows": [], "count": 0, "fetched_at": utcnow(), "unavailable": True}

    arrows = []
    for e in data if isinstance(data, list) else []:
        cur = e.get("current") or {}
        deg = cur.get("wind_direction_10m")
        mph = cur.get("wind_speed_10m")
        if deg is None or mph is None:
            continue
        arrows.append(
            {
                "lat": e.get("latitude"),
                "lon": e.get("longitude"),
                "deg": deg,  # direction the wind blows FROM (met convention)
                "mph": round(mph, 1),
                "gust": cur.get("wind_gusts_10m"),
            }
        )

    payload: dict[str, Any] = {
        "arrows": arrows,
        "count": len(arrows),
        "fetched_at": utcnow(),
        "source": "Open-Meteo modelled 10 m wind — ambient field, not an official observation",
    }
    conn.execute(
        "INSERT OR REPLACE INTO map_cache (key, payload, fetched_at) VALUES (?, ?, ?)",
        (CACHE_KEY, json.dumps(payload), payload["fetched_at"]),
    )
    conn.commit()
    get_logger("windfield").info("wind_field_fetched", arrows=len(arrows))
    return payload
