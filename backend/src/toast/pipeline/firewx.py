"""Fire-weather (red flag) zones for the map.

NWS CAP alerts already reach the ledger, but their features carry
`geometry: null` — an alert references affectedZones by URL, and the shape
lives one hop away at /zones/fire/{id}. This module makes that hop so a Red
Flag Warning can be SEEN as the area it covers.

Two honesty rules baked in:
- A Red Flag Warning is a FORECAST of dangerous burning conditions, not a
  fire. Every polygon carries its event name, headline and expiry, and the
  layer label says "forecast" — never a bare shaded area.
- A zone that fails to resolve degrades the layer, never fails it: the alert
  still shows in the list with `unresolved: true` so nothing is silently lost.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from toast.core.db import utcnow
from toast.core.fetch import fetch
from toast.core.logging import get_logger
from toast.sources.nws_cap import FIRE_EVENTS

ALERTS_URL = "https://api.weather.gov/alerts/active?area=CA"
CACHE_KEY = "firewx:ca"
CACHE_TTL_S = 10 * 60
# zone shapes are static geography — cache them practically forever
ZONE_TTL_S = 30 * 24 * 3600


def _get_json(url: str, max_bytes: int = 4 * 1024 * 1024) -> dict[str, Any]:
    body, _ctype = fetch(
        url,
        accept_types=("application/json", "application/geo+json", "text/plain"),
        max_bytes=max_bytes,
    )
    return json.loads(body)


def _zone_geometry(conn: sqlite3.Connection, zone_url: str) -> dict[str, Any] | None:
    key = f"firewx:zone:{zone_url.rsplit('/', 1)[-1]}"
    row = conn.execute(
        """SELECT payload FROM map_cache
           WHERE key = ? AND unixepoch('now') - unixepoch(fetched_at) < ?""",
        (key, ZONE_TTL_S),
    ).fetchone()
    if row:
        cached = json.loads(row["payload"])
        return cached or None  # a cached miss ('looked, none') is also an answer
    try:
        geom = _get_json(zone_url).get("geometry")
    except Exception:
        geom = None
    conn.execute(
        "INSERT OR REPLACE INTO map_cache (key, payload, fetched_at) VALUES (?, ?, ?)",
        (key, json.dumps(geom), utcnow()),
    )
    conn.commit()
    return geom


def get_fire_weather(conn: sqlite3.Connection, *, refresh: bool = False) -> dict[str, Any]:
    """Active CA fire-weather alerts as GeoJSON polygons, zone shapes resolved."""
    if not refresh:
        row = conn.execute(
            """SELECT payload, fetched_at FROM map_cache
               WHERE key = ? AND unixepoch('now') - unixepoch(fetched_at) < ?""",
            (CACHE_KEY, CACHE_TTL_S),
        ).fetchone()
        if row:
            cached = json.loads(row["payload"])
            cached["cached"] = True
            cached["fetched_at"] = row["fetched_at"]
            return cached

    data = _get_json(ALERTS_URL, max_bytes=8 * 1024 * 1024)
    features: list[dict[str, Any]] = []
    unresolved = 0
    for f in data.get("features", []):
        props = f.get("properties") or {}
        if props.get("event") not in FIRE_EVENTS:
            continue
        geom = f.get("geometry")
        zones = props.get("affectedZones") or []
        # an alert usually has no inline geometry; take every zone it names
        shapes = [geom] if geom else [_zone_geometry(conn, z) for z in zones]
        shapes = [s for s in shapes if s]
        if not shapes:
            unresolved += 1
        base = {
            "event": props.get("event"),
            "severity": props.get("severity"),
            "headline": (props.get("headline") or "")[:250],
            "onset": props.get("onset"),
            "ends": props.get("ends") or props.get("expires"),
            "area": (props.get("areaDesc") or "")[:250],
        }
        if shapes:
            for s in shapes:
                features.append({"type": "Feature", "geometry": s, "properties": base})
        else:
            features.append(
                {"type": "Feature", "geometry": None, "properties": {**base, "unresolved": True}}
            )

    payload: dict[str, Any] = {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "unresolved": unresolved,
        "note": "NWS fire-weather alerts — a FORECAST of dangerous burning "
        "conditions, not a fire. Polygons are the warned zones.",
        "fetched_at": utcnow(),
    }
    conn.execute(
        "INSERT OR REPLACE INTO map_cache (key, payload, fetched_at) VALUES (?, ?, ?)",
        (CACHE_KEY, json.dumps(payload), payload["fetched_at"]),
    )
    conn.commit()
    get_logger("firewx").info("firewx_refreshed", alerts=len(features), unresolved=unresolved)
    return payload
