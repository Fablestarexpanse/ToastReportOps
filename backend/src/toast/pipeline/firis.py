"""FIRIS infrared heat perimeters — CAL FIRE's IR flight footprints.

FIRIS (CalOES / Overwatch Imaging) flies infrared over California incidents and
CAL FIRE publishes the extracted polygons — heat perimeter, intense heat,
isolated heat — through this public ArcGIS view. It is the most authoritative
fire FOOTPRINT available short of ground truth: an aircraft measured where the
heat actually is, hours fresher than a mapped containment line.

The raw thermal imagery itself is commercially restricted and not shared; we
get the vectorised polygons only, which is exactly what a map wants. Tier-1
official. Matched to our incidents by name so the OPS map can show "the IR
footprint of THIS fire", newest flight per incident.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from toast.core.db import utcnow
from toast.core.fetch import FetchError, fetch
from toast.core.logging import get_logger

SERVICE = (
    "https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/arcgis/rest/services/"
    "CA_Perimeters_NIFC_FIRIS_public_view/FeatureServer/0/query"
)
CACHE_KEY = "firis:ca"
CACHE_TTL_S = 15 * 60  # perimeters update through the day as flights land
RECENT_DAYS = 7  # only current fires' footprints, not the whole season's archive


def _norm(name: str | None) -> str:
    """'ELEPHANT' == 'Elephant Fire' — strip case and the noise words so a
    FIRIS mission name matches our canonical incident name."""
    if not name:
        return ""
    out = "".join(c for c in name.lower() if c.isalnum() or c == " ").split()
    return " ".join(w for w in out if w not in ("fire", "incident", "complex"))


def _epoch_iso(ms: Any) -> str | None:
    if not isinstance(ms, int | float) or ms <= 0:
        return None
    return (
        datetime.fromtimestamp(ms / 1000.0, tz=UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def fetch_firis() -> dict[str, Any]:
    query = urlencode(
        {
            "where": (
                f"poly_DateCurrent >= CURRENT_TIMESTAMP - INTERVAL '{RECENT_DAYS}' DAY"
            ),
            "outFields": "incident_name,incident_number,area_acres,poly_DateCurrent,"
            "mission,source,type,displayStatus",
            "returnGeometry": "true",
            "outSR": 4326,
            "geometryPrecision": 4,  # ~11 m, plenty for a map, keeps the payload small
            "f": "geojson",
        }
    )
    body, _ctype = fetch(
        f"{SERVICE}?{query}",
        accept_types=("application/json", "application/geo+json", "text/plain"),
        max_bytes=24 * 1024 * 1024,
    )
    data = json.loads(body)
    if data.get("type") != "FeatureCollection":
        raise RuntimeError(f"FIRIS returned no collection: {str(data)[:120]}")
    return data


def _match_incidents(conn: sqlite3.Connection, features: list[dict[str, Any]]) -> int:
    """Tag each footprint with the active incident whose name it carries."""
    incidents = [
        (r["id"], _norm(r["canonical_name"]))
        for r in conn.execute(
            "SELECT id, canonical_name FROM incidents WHERE status = 'active'"
        )
    ]
    matched = 0
    for feature in features:
        props = feature.setdefault("properties", {})
        props["incident_id"] = None
        target = _norm(props.get("incident_name") or props.get("mission"))
        if not target:
            continue
        for inc_id, norm in incidents:
            if norm and norm == target:
                props["incident_id"] = inc_id
                matched += 1
                break
    return matched


def get_firis(conn: sqlite3.Connection, *, refresh: bool = False) -> dict[str, Any]:
    """Recent FIRIS IR heat perimeters statewide, matched to our incidents."""
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
        data = fetch_firis()
    except (FetchError, ValueError, RuntimeError) as exc:
        get_logger("firis").warning("firis_fetch_failed", error=repr(exc))
        stale = conn.execute(
            "SELECT payload FROM map_cache WHERE key = ?", (CACHE_KEY,)
        ).fetchone()
        if stale:
            out = json.loads(stale["payload"])
            out["cached"] = True
            out["stale"] = True
            return out
        return {"type": "FeatureCollection", "features": [], "count": 0, "unavailable": True}

    features = data.get("features", [])
    for f in features:
        p = f.setdefault("properties", {})
        p["poly_DateCurrent"] = _epoch_iso(p.get("poly_DateCurrent")) or p.get("poly_DateCurrent")
    matched = _match_incidents(conn, features)

    payload: dict[str, Any] = {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "matched": matched,
        "source": "CAL FIRE FIRIS public view — infrared heat perimeters (tier 1)",
        "note": (
            "IR heat perimeters flown over active fires. The most authoritative "
            "footprint short of ground truth; hours-fresh, updated as flights land."
        ),
        "fetched_at": utcnow(),
    }
    conn.execute(
        "INSERT OR REPLACE INTO map_cache (key, payload, fetched_at) VALUES (?, ?, ?)",
        (CACHE_KEY, json.dumps(payload), payload["fetched_at"]),
    )
    conn.commit()
    get_logger("firis").info("firis_fetched", features=len(features), matched=matched)
    return payload
