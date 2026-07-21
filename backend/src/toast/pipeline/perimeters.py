"""Fire perimeters from the interagency (WFIGS/NIFC) feed.

A dot sized by acreage says how big a fire is; a perimeter says which way it
has actually run and what it is about to reach. Free, no key, public data.

Two honesty points baked in:

- Perimeters are MAPPED, usually by an overnight IR flight, so they are hours
  old and coarser than they look. The layer always travels with the age of the
  data so nobody reads a crisp polygon as a live fire edge.
- Geometry is simplified server-side (~55 m). That is far finer than the map
  needs and cuts the payload from 476 KB to 13 KB, but it means the outline is
  an approximation of an approximation — which is exactly what a perimeter is.
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
from toast.pipeline.attribution import _norm_name
from toast.pipeline.incidents import haversine_km

SERVICE = (
    "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
    "WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query"
)
CACHE_KEY = "perimeters:ca"
CACHE_TTL_S = 30 * 60
SIMPLIFY_DEG = 0.0005  # ~55 m; 476 KB -> 13 KB with every feature kept
MATCH_RADIUS_KM = 50.0


def _centroid(geometry: dict[str, Any]) -> tuple[float, float] | None:
    """Rough centre of a polygon ring — enough to match against an incident."""
    coords = geometry.get("coordinates") or []
    ring: Any = coords
    while ring and isinstance(ring[0], list) and not isinstance(ring[0][0], float):
        ring = ring[0]
    if not ring or not isinstance(ring[0], list):
        return None
    lons = [p[0] for p in ring if isinstance(p, list) and len(p) >= 2]
    lats = [p[1] for p in ring if isinstance(p, list) and len(p) >= 2]
    if not lons:
        return None
    return sum(lats) / len(lats), sum(lons) / len(lons)


def fetch_perimeters() -> dict[str, Any]:
    query = urlencode(
        {
            "where": "attr_POOState='US-CA'",
            "outFields": "poly_IncidentName,attr_IrwinID,poly_GISAcres,poly_DateCurrent",
            "returnGeometry": "true",
            "outSR": 4326,
            "f": "geojson",
            "maxAllowableOffset": SIMPLIFY_DEG,
        }
    )
    body, _ctype = fetch(
        f"{SERVICE}?{query}",
        accept_types=("application/json", "application/geo+json", "text/plain"),
        max_bytes=8 * 1024 * 1024,
    )
    data = json.loads(body)
    if "features" not in data:
        raise RuntimeError("perimeter feed returned no features")
    return data


def _mapped_latest(features: list[dict[str, Any]]) -> str | None:
    """Newest poly_DateCurrent across the layer, as ISO UTC.

    This — not our fetch time — is the honest age of a perimeter: the feed can
    be seconds old and still be carrying an outline flown two nights ago.
    """
    stamps = []
    for feature in features:
        raw = (feature.get("properties") or {}).get("poly_DateCurrent")
        if isinstance(raw, int | float) and raw > 0:
            stamps.append(float(raw) / 1000.0)  # ArcGIS gives epoch milliseconds
    if not stamps:
        return None
    return (
        datetime.fromtimestamp(max(stamps), tz=UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _match_incidents(conn: sqlite3.Connection, features: list[dict[str, Any]]) -> int:
    """Tag each perimeter with our incident id where we can be confident.

    Same conservative rule as the resource enrichment: the normalized name must
    match AND the polygon must sit near where we believe the fire is.
    """
    incidents = [
        dict(r)
        for r in conn.execute(
            "SELECT id, canonical_name, lat, lon FROM incidents WHERE status != 'frozen'"
        )
    ]
    matched = 0
    for feature in features:
        props = feature.setdefault("properties", {})
        props["incident_id"] = None
        name = _norm_name(str(props.get("poly_IncidentName") or ""))
        if not name or not feature.get("geometry"):
            continue
        centre = _centroid(feature["geometry"])
        for inc in incidents:
            if _norm_name(inc["canonical_name"]) != name:
                continue
            if (
                centre
                and inc["lat"] is not None
                and haversine_km(centre[0], centre[1], inc["lat"], inc["lon"]) > MATCH_RADIUS_KM
            ):
                continue  # same name, different fire
            props["incident_id"] = inc["id"]
            props["incident_name"] = inc["canonical_name"]
            matched += 1
            break
    return matched


def _archive_snapshots(conn: sqlite3.Connection, features: list[dict[str, Any]]) -> int:
    """Keep each NEW mapping of a matched fire's outline.

    Which way a fire has run — last night's edge against tonight's — is the
    story, and history only exists if we keep it. UNIQUE(incident, mapped_at)
    makes this idempotent: a hundred re-fetches store nothing until the IR
    flight actually flies again."""
    stored = 0
    for feature in features:
        props = feature.get("properties") or {}
        iid = props.get("incident_id")
        if not iid or not feature.get("geometry"):
            continue
        raw = props.get("poly_DateCurrent")
        mapped_at = None
        if isinstance(raw, int | float) and raw > 0:
            mapped_at = (
                datetime.fromtimestamp(raw / 1000.0, tz=UTC)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )
        cur = conn.execute(
            """INSERT OR IGNORE INTO perimeter_history
               (incident_id, mapped_at, acres, geometry, captured_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                iid,
                mapped_at,
                props.get("poly_GISAcres"),
                json.dumps(feature["geometry"]),
                utcnow(),
            ),
        )
        stored += cur.rowcount
    if stored:
        conn.commit()
        get_logger("perimeters").info("perimeter_snapshots_stored", new=stored)
    return stored


def get_perimeters(conn: sqlite3.Connection, *, refresh: bool = False) -> dict[str, Any]:
    """Cached CA perimeter GeoJSON, tagged with our incident ids."""
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

    data = fetch_perimeters()
    matched = _match_incidents(conn, data.get("features", []))
    _archive_snapshots(conn, data.get("features", []))
    now = utcnow()
    data.update(
        {
            "fetched_at": now,
            "cached": False,
            "matched": matched,
            "mapped_latest": _mapped_latest(data.get("features", [])),
            "count": len(data.get("features", [])),
            "source": "WFIGS/NIFC interagency perimeters (public domain)",
            "note": (
                "Mapped perimeters, usually from an overnight IR flight — "
                "hours old and simplified to ~55 m."
            ),
        }
    )
    conn.execute(
        """INSERT INTO map_cache (key, payload, fetched_at) VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET payload = excluded.payload,
                                          fetched_at = excluded.fetched_at""",
        (CACHE_KEY, json.dumps(data), now),
    )
    get_logger("perimeters").info("perimeters_fetched", count=data["count"], matched=matched)
    return data
