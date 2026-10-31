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


# ref columns an adapter may key an incident on — allowlisted (never user input)
# so they are safe to interpolate into SQL.
REF_COLUMNS = {"irwin_id", "calfire_id"}
# a fire that has gone inactive should not be flipped back to active by a stale
# poll; only these forward transitions are applied on update.
STATUS_RANK = {"candidate": 0, "active": 1, "contained": 2, "frozen": 3}


def _norm_name(name: str) -> str:
    """'PARK Fire' == 'Park fire' == 'Park' — the noise words and case out."""
    out = "".join(c for c in name.lower() if c.isalnum() or c == " ").split()
    return " ".join(w for w in out if w not in ("fire", "incident", "complex")) or name.lower()


ADOPT_RADIUS_KM = 30.0  # CAL FIRE origin vs IRWIN point of origin can differ by miles


def _adopt_cross_ref(
    conn: sqlite3.Connection, *, ref_col: str, name: str, lat: float | None, lon: float | None
) -> str | None:
    """An existing incident that is the same fire under another identity, or None."""
    target = _norm_name(name)
    for row in conn.execute(
        f"SELECT id, canonical_name, lat, lon FROM incidents WHERE {ref_col} IS NULL"
    ):
        if _norm_name(row["canonical_name"]) != target:
            continue
        if (
            lat is not None
            and lon is not None
            and row["lat"] is not None
            and haversine_km(lat, lon, row["lat"], row["lon"]) > ADOPT_RADIUS_KM
        ):
            continue  # same name, different fire — Creek Fires abound
        return row["id"]
    return None


def upsert_incident(
    conn: sqlite3.Connection,
    *,
    ref_col: str,
    ref_id: str,
    name: str,
    lat: float | None,
    lon: float | None,
    county: str | None,
    incident_type: str | None = None,
    status: str = "active",
    via: str = "unknown",
) -> tuple[str, bool]:
    """Upsert an incident keyed on an official reference column.

    Returns (incident_id, created). On update, fields are refreshed and status
    only advances (active→contained→frozen), never regresses on a stale poll.
    """
    if ref_col not in REF_COLUMNS:
        raise ValueError(f"unknown ref_col {ref_col!r}")
    now = utcnow()
    row = conn.execute(
        f"SELECT id, status FROM incidents WHERE {ref_col} = ?", (ref_id,)
    ).fetchone()
    if row is not None:
        new_status = status if STATUS_RANK[status] > STATUS_RANK[row["status"]] else row["status"]
        if new_status != row["status"]:
            conn.execute(
                """INSERT INTO incident_events (id, incident_id, event, detail, actor, at)
                   VALUES (?, ?, 'status_change', ?, 'system', ?)""",
                (ulid(), row["id"], f'{{"to": "{new_status}", "via": "{via}"}}', now),
            )
        conn.execute(
            """UPDATE incidents SET canonical_name = ?, lat = COALESCE(?, lat),
               lon = COALESCE(?, lon), county = COALESCE(?, county),
               incident_type = COALESCE(?, incident_type), status = ?, updated_at = ?
               WHERE id = ?""",
            (name, lat, lon, county, incident_type, new_status, now, row["id"]),
        )
        return row["id"], False
    # Cross-source adoption: the same fire arrives from BOTH CAL FIRE and
    # WFIGS/IRWIN (CAL FIRE lists major federal fires; IRWIN lists everything
    # interagency). Before creating a second incident for it, look for the
    # same fire under the OTHER identity — same normalized name, and close by
    # when both sides know where they are — and attach this reference to it.
    adopted = _adopt_cross_ref(conn, ref_col=ref_col, name=name, lat=lat, lon=lon)
    if adopted is not None:
        # attach the new identity AND let it fill in what the first source
        # never knew — WFIGS often brings the coordinates CAL FIRE lacked
        conn.execute(
            f"""UPDATE incidents SET {ref_col} = ?, lat = COALESCE(lat, ?),
               lon = COALESCE(lon, ?), county = COALESCE(county, ?),
               incident_type = COALESCE(incident_type, ?), updated_at = ?
               WHERE id = ?""",
            (ref_id, lat, lon, county, incident_type, now, adopted),
        )
        conn.execute(
            """INSERT INTO incident_events (id, incident_id, event, detail, actor, at)
               VALUES (?, ?, 'alias_added', ?, 'system', ?)""",
            (ulid(), adopted, f'{{"{ref_col}": "{ref_id}", "via": "{via}"}}', now),
        )
        return adopted, False
    incident_id = ulid()
    conn.execute(
        f"""INSERT INTO incidents (id, canonical_name, status, lat, lon, county, {ref_col},
                                   incident_type, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (incident_id, name, status, lat, lon, county, ref_id, incident_type, now, now),
    )
    conn.execute(
        """INSERT INTO incident_events (id, incident_id, event, detail, actor, at)
           VALUES (?, ?, 'created', ?, 'system', ?)""",
        (ulid(), incident_id, f'{{"via": "{via}"}}', now),
    )
    return incident_id, True


def upsert_incident_by_irwin(
    conn: sqlite3.Connection,
    *,
    irwin_id: str,
    name: str,
    lat: float | None,
    lon: float | None,
    county: str | None,
    incident_type: str | None = None,
    status: str = "active",
) -> tuple[str, bool]:
    """Back-compat wrapper for WFIGS-keyed incidents."""
    return upsert_incident(
        conn,
        ref_col="irwin_id",
        ref_id=irwin_id,
        name=name,
        lat=lat,
        lon=lon,
        county=county,
        incident_type=incident_type,
        status=status,
        via="wfigs",
    )


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
