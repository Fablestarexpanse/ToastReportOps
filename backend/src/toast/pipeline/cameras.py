"""ALERTCalifornia camera positions, for the map.

The ~2,100-camera PTZ network run by UC San Diego is how smoke gets visually
confirmed before anything official exists. This module handles METADATA ONLY:
the same all_cameras JSON their own map hands every visitor, cached here so we
can place camera markers near a fire and say which way each lens is pointing.

The imagery boundary, stated once and enforced by architecture: frames are
loaded by the OPERATOR'S BROWSER directly from ALERTCalifornia when a camera is
clicked — exactly what visiting their site does. This backend never fetches,
stores, or relays a frame; there is no polling, no archive, no redistribution.
Their TOS question on automated sampling (open since the kickoff plan) stays a
question about something we do not do.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from toast.core.db import utcnow
from toast.core.fetch import fetch
from toast.core.logging import get_logger

CAMERA_INDEX = "https://cameras.alertcalifornia.org/public-camera-data/all_cameras-v3.json"
# the id slots into this template CLIENT-SIDE; kept here as the single record
# of the format we verified (1920x1080 JPEG, discovered empirically 2026-07-21)
FRAME_URL_TEMPLATE = "https://cameras.alertcalifornia.org/public-camera-data/{id}/latest-frame.jpg"
CACHE_KEY = "cameras:alertca"
CACHE_TTL_S = 60 * 60  # positions are static; az/tilt drift is tolerable at 1 h
NEAR_KM = 40.0  # a ridge camera 40 km out can still see a smoke column
MAX_RETURNED = 14


def _index(conn: sqlite3.Connection, *, refresh: bool = False) -> dict[str, Any]:
    if not refresh:
        row = conn.execute(
            """SELECT payload, fetched_at FROM map_cache
               WHERE key = ? AND unixepoch('now') - unixepoch(fetched_at) < ?""",
            (CACHE_KEY, CACHE_TTL_S),
        ).fetchone()
        if row:
            cached = json.loads(row["payload"])
            cached["fetched_at"] = row["fetched_at"]
            return cached

    body, _ctype = fetch(
        CAMERA_INDEX,
        accept_types=("application/json", "text/plain", "application/octet-stream"),
        max_bytes=16 * 1024 * 1024,
    )
    data = json.loads(body)
    cameras = []
    for feature in data.get("features", []):
        props = feature.get("properties") or {}
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2 or not props.get("id"):
            continue
        if not isinstance(coords[0], int | float) or not isinstance(coords[1], int | float):
            continue  # a camera the index cannot place cannot go on a map
        cameras.append(
            {
                "id": props["id"],
                "name": props.get("name") or props["id"],
                "lat": coords[1],
                "lon": coords[0],
                "elev_m": coords[2] if len(coords) > 2 else None,
                "county": props.get("county"),
                "az": props.get("az_current"),  # where the lens points right now
                "fov": props.get("fov"),
                "last_frame_ts": props.get("last_frame_ts"),
            }
        )
    payload = {"cameras": cameras, "fetched_at": utcnow()}
    conn.execute(
        "INSERT OR REPLACE INTO map_cache (key, payload, fetched_at) VALUES (?, ?, ?)",
        (CACHE_KEY, json.dumps(payload), payload["fetched_at"]),
    )
    conn.commit()
    get_logger("cameras").info("camera_index_refreshed", cameras=len(cameras))
    return payload


def cameras_near(
    conn: sqlite3.Connection,
    lat: float,
    lon: float,
    *,
    km: float = NEAR_KM,
    limit: int = MAX_RETURNED,
    refresh: bool = False,
) -> dict[str, Any]:
    """The cameras that might actually see this point, nearest first."""
    from toast.pipeline.incidents import haversine_km

    index = _index(conn, refresh=refresh)
    hits = []
    for cam in index["cameras"]:
        d = haversine_km(lat, lon, cam["lat"], cam["lon"])
        if d <= km:
            hits.append({**cam, "distance_km": round(d, 1)})
    hits.sort(key=lambda c: c["distance_km"])
    return {
        "cameras": hits[:limit],
        "count": len(hits[:limit]),
        "total_in_range": len(hits),
        "fetched_at": index["fetched_at"],
        "frame_url_template": FRAME_URL_TEMPLATE,
        "attribution": "ALERTCalifornia / UC San Diego",
    }
