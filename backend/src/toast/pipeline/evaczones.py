"""Evacuation zone polygons, from Cal OES — the compliant path.

The zones people know ("US-CA-XBU-PARADISE-A") live in Genasys Protect, whose
terms prohibit programmatic use. It does not matter: the California Office of
Emergency Services aggregates every county's zone service into one public
feature layer on the state open-data portal — same polygons, same Zonehaven
zone ids, same statuses, refreshed every 5 minutes, published by the state's
own emergency authority. Tier-1 official, no vendor terms in the way.

Doctrine holds here harder than anywhere: STATUS and the official text fields
(CRITICAL_INFO / PUBLIC_INFO) are rendered VERBATIM — never summarized, never
rewritten. The layer only ever contains zones a county has elevated, so every
polygon on the map is an active instruction to someone.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from toast.core.db import utcnow
from toast.core.fetch import fetch
from toast.core.logging import get_logger
from toast.pipeline.incidents import haversine_km
from toast.pipeline.perimeters import _centroid

SERVICE = (
    "https://services.arcgis.com/BLN4oKB0N1YSgvY8/arcgis/rest/services/"
    "CA_EVACUATIONS_CalOESHosted_view/FeatureServer/0/query"
)
CACHE_KEY = "evaczones:ca"
CACHE_TTL_S = 4 * 60  # the layer rebuilds every 5 minutes; never poll faster
MATCH_RADIUS_KM = 60.0
ACTIVE_STATUSES = ("order", "warning", "shelter in place", "shelter-in-place", "advisory")


def _epoch_iso(ms: Any) -> str | None:
    if not isinstance(ms, int | float) or ms <= 0:
        return None
    return (
        datetime.fromtimestamp(ms / 1000.0, tz=UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def fetch_zones() -> dict[str, Any]:
    query = urlencode(
        {
            "where": "1=1",  # the layer carries only elevated zones
            "outFields": "COUNTY,CITY,ZONE_ID,ZONE_NAME,STATUS,EVENT_TYPE,"
            "CRITICAL_INFO,PUBLIC_INFO,EDIT_DATE,STATEWIDE_LAST_UPDATED",
            "returnGeometry": "true",
            "outSR": 4326,
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
        raise RuntimeError(f"Cal OES evac layer returned no collection: {str(data)[:120]}")
    return data


def _match_incidents(conn: sqlite3.Connection, features: list[dict[str, Any]]) -> int:
    """Tag each zone with the nearest active fire within range, so the intel
    panel can say which zones belong to which incident."""
    incidents = [
        dict(r)
        for r in conn.execute(
            """SELECT id, canonical_name, lat, lon FROM incidents
               WHERE status = 'active' AND lat IS NOT NULL"""
        )
    ]
    matched = 0
    for feature in features:
        props = feature.setdefault("properties", {})
        props["incident_id"] = None
        centre = _centroid(feature.get("geometry") or {})
        if not centre:
            continue
        best = None
        for inc in incidents:
            km = haversine_km(centre[0], centre[1], inc["lat"], inc["lon"])
            if km <= MATCH_RADIUS_KM and (best is None or km < best[1]):
                best = (inc, km)
        if best:
            props["incident_id"] = best[0]["id"]
            props["incident_name"] = best[0]["canonical_name"]
            props["distance_km"] = round(best[1], 1)
            matched += 1
    return matched


def get_evac_zones(conn: sqlite3.Connection, *, refresh: bool = False) -> dict[str, Any]:
    """Elevated evacuation zones statewide, statuses verbatim."""
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

    data = fetch_zones()
    features = data.get("features", [])
    for f in features:
        p = f.setdefault("properties", {})
        p["EDIT_DATE"] = _epoch_iso(p.get("EDIT_DATE")) or p.get("EDIT_DATE")
    matched = _match_incidents(conn, features)
    statuses: dict[str, int] = {}
    for f in features:
        s = (f["properties"].get("STATUS") or "unknown").strip()
        statuses[s] = statuses.get(s, 0) + 1

    payload: dict[str, Any] = {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "matched": matched,
        "statuses": statuses,
        "source": "Cal OES California Evacuation Aggregation Layer (state open data)",
        "note": (
            "Official zone statuses, rendered verbatim. The layer holds only "
            "zones a county has elevated — every polygon is an active "
            "instruction. Rebuilds every 5 minutes from county sources."
        ),
        "fetched_at": utcnow(),
    }
    conn.execute(
        "INSERT OR REPLACE INTO map_cache (key, payload, fetched_at) VALUES (?, ?, ?)",
        (CACHE_KEY, json.dumps(payload), payload["fetched_at"]),
    )
    conn.commit()
    get_logger("evaczones").info("evac_zones_fetched", zones=len(features), matched=matched)
    return payload
