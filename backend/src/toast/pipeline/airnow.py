"""Smoke / AQI near a fire, from AirNow (EPA).

The fire story that reaches the most readers is the smoke. AirNow's free API
returns current observations from the nearest reporting sites; we surface the
WORST current AQI across parameters (smoke is PM2.5, but ozone rides along)
with its category, and we name the reporting area — the honest caveat, since
the monitor can be a town over from the fire.

The key is the operator's (OPTIONS -> credentials). It travels in the query
string, so it is declared to fetch() as a secret and never survives into an
error message, a log line, or the nightly snapshot.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any
from urllib.parse import urlencode

from toast.core.credentials import get_credential
from toast.core.db import utcnow
from toast.core.fetch import fetch
from toast.core.logging import get_logger

URL = "https://www.airnowapi.org/aq/observation/latLong/current/"
CACHE_TTL_S = 45 * 60  # AirNow publishes hourly
DISTANCE_MILES = 75

CATEGORY_ORDER = [
    "Good",
    "Moderate",
    "Unhealthy for Sensitive Groups",
    "Unhealthy",
    "Very Unhealthy",
    "Hazardous",
]


def aqi_for(conn: sqlite3.Connection, incident_id: str, *, refresh: bool = False) -> dict[str, Any]:
    inc = conn.execute("SELECT lat, lon FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    if inc is None:
        raise ValueError("incident not found")
    if inc["lat"] is None:
        return {"available": False, "reason": "incident has no coordinates"}

    key = get_credential(conn, "AIRNOW_API_KEY")
    if not key:
        # a missing key is a documented state, not an error
        return {"available": False, "reason": "AirNow key not configured (OPTIONS -> credentials)"}

    cache_key = f"airnow:{incident_id}"
    if not refresh:
        row = conn.execute(
            """SELECT payload, fetched_at FROM map_cache
               WHERE key = ? AND unixepoch('now') - unixepoch(fetched_at) < ?""",
            (cache_key, CACHE_TTL_S),
        ).fetchone()
        if row:
            cached = json.loads(row["payload"])
            cached["cached"] = True
            cached["fetched_at"] = row["fetched_at"]
            return cached

    query = urlencode(
        {
            "format": "application/json",
            "latitude": f"{inc['lat']:.4f}",
            "longitude": f"{inc['lon']:.4f}",
            "distance": DISTANCE_MILES,
            "API_KEY": key,
        }
    )
    body, _ctype = fetch(
        f"{URL}?{query}",
        accept_types=("application/json", "text/plain"),
        max_bytes=1 * 1024 * 1024,
        secrets=(key,),
    )
    observations = json.loads(body)
    readings = []
    for o in observations if isinstance(observations, list) else []:
        aqi = o.get("AQI")
        if not isinstance(aqi, int) or aqi < 0:
            continue
        readings.append(
            {
                "parameter": o.get("ParameterName"),
                "aqi": aqi,
                "category": (o.get("Category") or {}).get("Name"),
                "area": o.get("ReportingArea"),
                "hour": o.get("HourObserved"),
            }
        )
    worst = max(readings, key=lambda r: r["aqi"], default=None)
    payload: dict[str, Any] = {
        "available": bool(readings),
        "readings": readings,
        "worst": worst,
        "note": (
            f"AirNow (EPA) — nearest reporting area '{worst['area']}', which can "
            "be a town over from the fire; smoke at the fire itself may be far worse"
            if worst
            else f"no AirNow site reporting within {DISTANCE_MILES} miles"
        ),
        "fetched_at": utcnow(),
    }
    conn.execute(
        "INSERT OR REPLACE INTO map_cache (key, payload, fetched_at) VALUES (?, ?, ?)",
        (cache_key, json.dumps(payload), payload["fetched_at"]),
    )
    conn.commit()
    get_logger("airnow").info(
        "airnow_fetched",
        incident=incident_id,
        readings=len(readings),
        worst=worst["aqi"] if worst else None,
    )
    return payload
