"""Aircraft over active fires, from airplanes.live (free, keyless ADS-B).

A tanker's racetrack orbit or a helo working low over a ridge tells you where the
fight actually is — often before any written report. This polls a small radius
around each active incident, keeps only the low-flying aircraft that could be air
operations, and records their positions so an orbit becomes a durable "air
resources observed" signal on the incident.

Honesty, same as every borrowed layer:
- Positions are a live external feed, hours-fresh at best and dependent on
  ground-station coverage — an aircraft not shown may simply be out of range.
- "fire_ops" is a HEURISTIC (a firefighting airframe type, or a low rotorcraft),
  never an assertion. The operator sees the aircraft's own description and decides.
- Nothing enters the ledger. These are observations of the sky, pruned after
  12 hours; the ledger stays a record of claims, not of traffic.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from toast.core.db import utc_ago, utcnow
from toast.core.fetch import fetch
from toast.core.logging import get_logger

# api.airplanes.live point query: aircraft within <radius> nm of lat,lon
SERVICE = "https://api.airplanes.live/v2/point"
RADIUS_NM = 20  # tight around the fire — this is air ops, not regional traffic
LOW_CEILING_FT = 10000  # above this it is transit; not stored
ROTOR_CEILING_FT = 8000  # a helicopter this low near a fire is plausibly working it
RETAIN_HOURS = 12
FEED_STATUS_KEY = "air:feed:status"
FEED_STATUS_TTL_S = 10 * 60  # past this, nothing has polled, so it says nothing about now

# Classification is an ALLOWLIST of fire, not a blocklist of everything else.
# The reason is FedEx: it flies DC-10s and MD-11s (same airframe as the VLAT
# tankers) and hundreds of Cessna 208 feeders. Matching on type alone called all
# of them air operations. So a shared airframe is only fire when a fire CALLSIGN
# or a known fire OPERATOR says so; unconfirmed, it is 'other' — still shown on
# the map, just not asserted to be fighting the fire.

# Airframes essentially nobody but fire flies in California airspace.
UNIQUE_TANKER_TYPES = frozenset(
    {"S2", "AT8T", "AT802", "CL2T", "CL41", "P3"}  # S-2T, Air Tractor, CL-415, P-3
)
# Airframes fire shares with cargo/charter — need a fire callsign or operator.
SHARED_TANKER_TYPES = frozenset({"DC10", "MD11", "MD87", "C130", "RJ85", "BAE146", "B73Q", "B744"})
SHARED_LEAD_TYPES = frozenset({"BE20", "BE30", "BE9L", "C90", "PC12", "TBM9"})  # King Airs etc.
# NOTE: C208 (Caravan) deliberately excluded — it is overwhelmingly a cargo
# feeder (the FedEx-Feeder fleet), not an air-attack platform.

HELICOPTER_TYPES = frozenset(
    {
        "H60",
        "S64",
        "CH47",
        "H47",
        "B407",
        "B412",
        "B212",
        "B205",
        "AS50",
        "AS32",
        "EC30",
        "EC35",
        "B06",
        "R44",
        "R22",
        "UH1",
        "A139",
        "S76",
    }
)

# Operator names (airplanes.live `ownOp`) that confirm a shared airframe is fire.
FIRE_OPERATORS = (
    "CAL FIRE",
    "CALFIRE",
    "CDF",
    "NEPTUNE",
    "10 TANKER",
    "TEN TANKER",
    "COULSON",
    "AERO AIR",
    "AERO-FLITE",
    "AEROFLITE",
    "AERO FLITE",
    "ERICKSON",
    "BRIDGER",
    "DAUNTLESS",
    "US FOREST",
    "FOREST SERVICE",
    "USFS",
    "BLM",
    "NATIONAL GUARD",
    "AIR NATIONAL GUARD",
    "CAL GUARD",
    "GRANDVIEW",
    "BC AIRCRAFT LEASING",
    "SIS-Q",
    "FIREWATCH",
    "SPRING AIR",
    "AERO-SPRAY",
)

Role = str  # 'tanker' | 'air_attack' | 'helicopter' | 'other'


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) else None


def is_military(db_flags: Any) -> bool:
    """airplanes.live dbFlags bit 0 marks a military transponder — National Guard
    MAFFS C-130s and military helos matter around a fire, so it is surfaced."""
    return isinstance(db_flags, int) and bool(db_flags & 1)


def _callsign_role(cs: str) -> Role | None:
    """The strongest signal there is: fire aircraft fly named callsigns. A DC-10
    as TANKER 914 is a tanker no matter who leases the hull."""
    if cs.startswith(("TANKER", "TNKR", "TKR")):
        return "tanker"
    if cs.startswith(("ATGS", "AIRATT", "BRONCO", "LEAD")) or "AIR ATTACK" in cs:
        return "air_attack"
    if cs.startswith(("COPTER", "HELCO", "HELITACK", "FIREHAWK")):
        return "helicopter"
    return None


def classify_role(
    type_code: str | None,
    category: str | None,
    alt_ft: int | None,
    flight: str | None = None,
    own_op: str | None = None,
) -> Role:
    """What kind of firefighting aircraft is this — or 'other'.

    Order of trust: a fire callsign (TANKER 914) beats everything; then airframes
    only fire flies; then shared airframes, but ONLY with a fire operator, so a
    FedEx DC-10 or a cargo Caravan does not masquerade as a tanker; then low
    rotorcraft. Anything unconfirmed is 'other' — shown, not claimed.
    """
    t = (type_code or "").upper()
    cs = (flight or "").upper().strip()
    op = (own_op or "").upper()
    fire_op = any(m in op for m in FIRE_OPERATORS)

    by_callsign = _callsign_role(cs)
    if by_callsign:
        return by_callsign
    if t in UNIQUE_TANKER_TYPES:
        return "tanker"
    if t == "OV10":
        return "air_attack"
    if t in SHARED_TANKER_TYPES:
        return "tanker" if fire_op else "other"
    if t in SHARED_LEAD_TYPES:
        return "air_attack" if fire_op else "other"
    if (category or "").upper() == "A7" and alt_ft is not None and alt_ft <= ROTOR_CEILING_FT:
        return "helicopter"
    if t in HELICOPTER_TYPES:
        return "helicopter"
    return "other"


def _is_fire_ops(alt_ft: int | None, type_code: str | None, category: str | None) -> bool:
    """Air operations = any classified firefighting role."""
    return classify_role(type_code, category, alt_ft) != "other"


def record_feed(conn: sqlite3.Connection, error: str | None) -> None:
    """Remember whether the feed last answered, for every reader of the store."""
    conn.execute(
        "INSERT OR REPLACE INTO map_cache (key, payload, fetched_at) VALUES (?, ?, ?)",
        (FEED_STATUS_KEY, json.dumps({"error": error}), utcnow()),
    )


def feed_error(conn: sqlite3.Connection) -> str | None:
    """Why the feed is failing right now, or None. Empty results read as "no
    aircraft", so anything showing them has to be able to say the feed is down."""
    row = conn.execute(
        """SELECT payload FROM map_cache
           WHERE key = ? AND unixepoch('now') - unixepoch(fetched_at) < ?""",
        (FEED_STATUS_KEY, FEED_STATUS_TTL_S),
    ).fetchone()
    return json.loads(row["payload"])["error"] if row else None


def _fetch_near(lat: float, lon: float) -> list[dict[str, Any]]:
    body, _ctype = fetch(
        f"{SERVICE}/{lat}/{lon}/{RADIUS_NM}",
        accept_types=("application/json", "text/plain"),
        max_bytes=4 * 1024 * 1024,
    )
    data = json.loads(body)
    ac = data.get("ac") or data.get("aircraft") or []
    return ac if isinstance(ac, list) else []


def poll_incident(conn: sqlite3.Connection, incident: sqlite3.Row) -> int:
    """Record low aircraft near one incident. Returns how many were stored."""
    try:
        ac = _fetch_near(incident["lat"], incident["lon"])
    except Exception as exc:
        record_feed(conn, str(exc)[:200])
        raise
    record_feed(conn, None)
    now = utcnow()
    stored = 0
    for a in ac:
        lat, lon = _num(a.get("lat")), _num(a.get("lon"))
        if lat is None or lon is None:
            continue
        alt = a.get("alt_baro")
        alt_ft = int(alt) if isinstance(alt, int | float) else None
        if alt_ft is not None and alt_ft > LOW_CEILING_FT:
            continue  # transit, not fire ops — do not store
        gs = _num(a.get("gs"))
        type_code = a.get("t")
        flight = (a.get("flight") or "").strip() or None
        role = classify_role(type_code, a.get("category"), alt_ft, flight, a.get("ownOp"))
        mil = is_military(a.get("dbFlags"))
        conn.execute(
            """INSERT INTO aircraft_positions
               (hex, incident_id, flight, type, descr, lat, lon, alt_ft, gs, track,
                fire_ops, role, military, seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(a.get("hex") or ""),
                incident["id"],
                flight,
                type_code,
                a.get("desc"),
                lat,
                lon,
                alt_ft,
                gs,
                _num(a.get("track")),
                1 if role != "other" else 0,
                role,
                1 if mil else 0,
                now,
            ),
        )
        stored += 1
    return stored


def poll_active(conn: sqlite3.Connection) -> dict[str, Any]:
    """Poll every active, located incident and prune old positions."""
    incidents = conn.execute(
        """SELECT id, canonical_name, lat, lon FROM incidents
           WHERE status = 'active' AND lat IS NOT NULL AND lon IS NOT NULL"""
    ).fetchall()
    total = 0
    reached = 0
    log = get_logger("aircraft")
    for inc in incidents:
        try:
            total += poll_incident(conn, inc)
            reached += 1
        except Exception as exc:  # one bad incident must not stop the sweep
            log.warning("aircraft_poll_failed", incident=inc["id"], error=str(exc)[:120])
    conn.execute(
        "DELETE FROM aircraft_positions WHERE seen_at < ?",
        (utc_ago(RETAIN_HOURS * 3600),),
    )
    log.info("aircraft_polled", incidents=reached, positions=total)
    return {"incidents_polled": reached, "positions": total}


def current_aircraft(conn: sqlite3.Connection, *, minutes: int = 10) -> dict[str, Any]:
    """Latest position per aircraft seen in the last `minutes`, plus a short
    trail per aircraft for drawing an orbit."""
    # latest by rowid, not seen_at: several positions in one poll cycle share a
    # timestamp, so a MAX(seen_at) join would return every tied row. rowid is
    # monotonic, giving exactly one current position per aircraft.
    rows = conn.execute(
        """SELECT p.* FROM aircraft_positions p
           JOIN (SELECT hex, MAX(rowid) AS latest FROM aircraft_positions
                 WHERE seen_at >= ? GROUP BY hex) last
             ON p.rowid = last.latest""",
        (utc_ago(minutes * 60),),
    ).fetchall()
    craft = []
    for r in rows:
        trail = conn.execute(
            """SELECT lat, lon FROM aircraft_positions
               WHERE hex = ? AND seen_at >= ?
               ORDER BY seen_at""",
            (r["hex"], utc_ago(3600)),
        ).fetchall()
        craft.append(
            {
                "hex": r["hex"],
                "flight": r["flight"],
                "type": r["type"],
                "descr": r["descr"],
                "lat": r["lat"],
                "lon": r["lon"],
                "alt_ft": r["alt_ft"],
                "gs": r["gs"],
                "track": r["track"],
                "fire_ops": bool(r["fire_ops"]),
                "role": r["role"],
                "military": bool(r["military"]),
                "incident_id": r["incident_id"],
                "seen_at": r["seen_at"],
                "trail": [[t["lat"], t["lon"]] for t in trail],
            }
        )
    return {
        "aircraft": craft,
        "count": len(craft),
        "fire_ops": sum(1 for c in craft if c["fire_ops"]),
        "feed_error": feed_error(conn),
        "note": (
            "airplanes.live ADS-B — positions near active fires; 'air ops' is a "
            "heuristic from altitude, speed and type, not a confirmed role."
        ),
    }


def incident_roster(conn: sqlite3.Connection, incident_id: str, *, minutes: int = 15) -> list[dict]:
    """The air resources currently over one fire — latest position per aircraft,
    firefighting roles first, for the intel panel's roster box."""
    rows = conn.execute(
        """SELECT p.* FROM aircraft_positions p
           JOIN (SELECT hex, MAX(rowid) AS latest FROM aircraft_positions
                 WHERE incident_id = ? AND seen_at >= ?
                 GROUP BY hex) last ON p.rowid = last.latest
           ORDER BY p.fire_ops DESC, p.alt_ft ASC""",
        (incident_id, utc_ago(minutes * 60)),
    ).fetchall()
    return [
        {
            "hex": r["hex"],
            "flight": r["flight"],
            "type": r["type"],
            "descr": r["descr"],
            "role": r["role"],
            "fire_ops": bool(r["fire_ops"]),
            "military": bool(r["military"]),
            "alt_ft": r["alt_ft"],
            "gs": r["gs"],
            "track": r["track"],
            "seen_at": r["seen_at"],
        }
        for r in rows
    ]


def observed_over_incident(conn: sqlite3.Connection, incident_id: str, *, hours: int = 6) -> int:
    """Distinct fire-ops aircraft seen over an incident recently — the number
    the intel panel reports as 'air resources observed'."""
    return conn.execute(
        """SELECT COUNT(DISTINCT hex) AS n FROM aircraft_positions
           WHERE incident_id = ? AND fire_ops = 1
             AND seen_at >= ?""",
        (incident_id, utc_ago(hours * 3600)),
    ).fetchone()["n"]
