"""Caltrans road closures near active fires.

The LCS feed is free and keyless: 12 district files, ~1,000 lane records each.
Almost all of it is routine lane work — noise, not signal. A closure earns the
map only when it is a FULL closure AND either sits near an active fire or its
own text says fire/evacuation. Everything dropped is counted and reported, so
the layer never silently hides.

The honesty label this layer must always carry: Caltrans LCS covers STATE
HIGHWAYS ONLY. County roads and local streets — how people actually leave a
neighborhood — are not in it. Unlabelled, it implies a completeness it does
not have.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from toast.core.db import utcnow
from toast.core.fetch import fetch
from toast.core.logging import get_logger
from toast.pipeline.incidents import haversine_km

DISTRICTS = list(range(1, 13))
FEED = "https://cwwp2.dot.ca.gov/data/d{d}/lcs/lcsStatusD{d:02d}.json"
CACHE_KEY = "closures:ca"
CACHE_TTL_S = 10 * 60
NEAR_KM = 15.0  # 50 km caught rest-area work half a county away; 15 km is fire-relevant
FIRE_WORDS = ("fire", "evacuation", "evac")


def _records(district: int) -> list[dict[str, Any]]:
    body, _ctype = fetch(
        FEED.format(d=district),
        accept_types=("application/json", "text/plain"),
        max_bytes=24 * 1024 * 1024,  # District 7 (LA) alone is ~14 MB
    )
    data = json.loads(body)
    return data.get("data") or []


def _flat(rec: dict[str, Any]) -> dict[str, Any] | None:
    """The LCS record is deeply nested; pull out the fields the map needs."""
    lcs = rec.get("lcs") or rec
    closure = lcs.get("closure") or {}
    loc = (lcs.get("location") or {}).get("begin") or {}
    try:
        lat = float(loc["beginLatitude"])
        lon = float(loc["beginLongitude"])
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "lat": lat,
        "lon": lon,
        "route": loc.get("beginRoute"),
        "county": loc.get("beginCounty"),
        "place": loc.get("beginNearbyPlace"),
        "type_of_closure": closure.get("typeOfClosure"),
        "type_of_work": closure.get("typeOfWork"),
        "started": closure.get("closureTimestamp", {}).get("closureStartDate")
        if isinstance(closure.get("closureTimestamp"), dict)
        else None,
    }


def get_closures(conn: sqlite3.Connection, *, refresh: bool = False) -> dict[str, Any]:
    """Full closures near active fires (or fire-worded), statewide."""
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

    incidents = [
        dict(r)
        for r in conn.execute(
            """SELECT id, canonical_name, lat, lon FROM incidents
               WHERE status = 'active' AND lat IS NOT NULL"""
        )
    ]
    kept: list[dict[str, Any]] = []
    seen_total = 0
    failed_districts = []
    for d in DISTRICTS:
        try:
            records = _records(d)
        except Exception:
            failed_districts.append(d)
            continue
        seen_total += len(records)
        for rec in records:
            flat = _flat(rec)
            if flat is None:
                continue
            if (flat["type_of_closure"] or "").strip().lower() != "full":
                continue
            text = f"{flat['type_of_work'] or ''}".lower()
            fire_worded = any(w in text for w in FIRE_WORDS)
            near = None
            for inc in incidents:
                km = haversine_km(flat["lat"], flat["lon"], inc["lat"], inc["lon"])
                if km <= NEAR_KM and (near is None or km < near["km"]):
                    near = {
                        "incident_id": inc["id"],
                        "name": inc["canonical_name"],
                        "km": round(km, 1),
                    }
            if not (near or fire_worded):
                continue
            kept.append({**flat, "district": d, "fire_worded": fire_worded, "near": near})

    # directional/duplicate records for the same closure point collapse to one
    deduped: dict[tuple, dict[str, Any]] = {}
    for c in kept:
        key = (c["route"], c["place"], c["type_of_work"], round(c["lat"], 2), round(c["lon"], 2))
        deduped.setdefault(key, c)
    kept = list(deduped.values())

    payload: dict[str, Any] = {
        "closures": kept,
        "count": len(kept),
        "dropped": seen_total - len(kept),
        "failed_districts": failed_districts,
        "note": "Caltrans LCS full closures near active fires — STATE HIGHWAYS "
        "ONLY. County and local roads are not in this feed.",
        "fetched_at": utcnow(),
    }
    conn.execute(
        "INSERT OR REPLACE INTO map_cache (key, payload, fetched_at) VALUES (?, ?, ?)",
        (CACHE_KEY, json.dumps(payload), payload["fetched_at"]),
    )
    conn.commit()
    get_logger("closures").info(
        "closures_refreshed",
        kept=len(kept),
        dropped=seen_total - len(kept),
        failed=len(failed_districts),
    )
    return payload
