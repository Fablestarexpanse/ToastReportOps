"""Incident upsert + basic geo attribution.

Official WFIGS reports carry an IrwinID — incidents key on it. Hotspots and
alerts have no incident reference; Phase 1 attaches hotspots to the nearest
active incident within a radius, else leaves incident_id NULL (the Phase 2
attribution scorer takes over from there).
"""

from __future__ import annotations

import math
import sqlite3

from toast.core.db import utcnow
from toast.core.ids import ulid

ATTACH_RADIUS_KM = 15.0


def upsert_incident_by_irwin(
    conn: sqlite3.Connection,
    *,
    irwin_id: str,
    name: str,
    lat: float | None,
    lon: float | None,
    county: str | None,
) -> tuple[str, bool]:
    """Returns (incident_id, created)."""
    row = conn.execute("SELECT id FROM incidents WHERE irwin_id = ?", (irwin_id,)).fetchone()
    now = utcnow()
    if row is not None:
        conn.execute(
            """UPDATE incidents SET canonical_name = ?, lat = COALESCE(?, lat),
               lon = COALESCE(?, lon), county = COALESCE(?, county), updated_at = ?
               WHERE id = ?""",
            (name, lat, lon, county, now, row["id"]),
        )
        return row["id"], False
    incident_id = ulid()
    conn.execute(
        """INSERT INTO incidents (id, canonical_name, status, lat, lon, county, irwin_id,
                                  created_at, updated_at)
           VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?)""",
        (incident_id, name, lat, lon, county, irwin_id, now, now),
    )
    conn.execute(
        """INSERT INTO incident_events (id, incident_id, event, detail, actor, at)
           VALUES (?, ?, 'created', '{"via": "wfigs"}', 'system', ?)""",
        (ulid(), incident_id, now),
    )
    return incident_id, True


def set_first_detection(conn: sqlite3.Connection, incident_id: str, fact_id: str) -> None:
    conn.execute(
        "UPDATE incidents SET first_detected_fact_id = ? WHERE id = ? "
        "AND first_detected_fact_id IS NULL",
        (fact_id, incident_id),
    )


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_active_incident(
    conn: sqlite3.Connection, lat: float, lon: float, radius_km: float = ATTACH_RADIUS_KM
) -> str | None:
    best_id, best_d = None, radius_km
    for row in conn.execute(
        "SELECT id, lat, lon FROM incidents WHERE status IN ('candidate','active')"
        " AND lat IS NOT NULL AND lon IS NOT NULL"
    ):
        d = haversine_km(lat, lon, row["lat"], row["lon"])
        if d <= best_d:
            best_id, best_d = row["id"], d
    return best_id
