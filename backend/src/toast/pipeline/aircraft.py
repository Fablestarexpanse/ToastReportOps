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

from toast.core.db import utcnow
from toast.core.fetch import fetch
from toast.core.logging import get_logger

# api.airplanes.live point query: aircraft within <radius> nm of lat,lon
SERVICE = "https://api.airplanes.live/v2/point"
RADIUS_NM = 20  # tight around the fire — this is air ops, not regional traffic
LOW_CEILING_FT = 10000  # above this it is transit; not stored
ROTOR_CEILING_FT = 8000  # a helicopter this low near a fire is plausibly working it
RETAIN_HOURS = 12

# ICAO type codes by firefighting ROLE. Split so the map and the roster can
# tell a retardant bomber from the lead plane spotting for it from a helo.

# Fixed-wing air tankers — the "bombers" that drop retardant or water.
TANKER_TYPES = frozenset(
    {
        "S2",  # CAL FIRE Grumman S-2T — the backbone of the state fleet
        "C130",
        "RJ85",
        "BAE146",
        "DC10",
        "MD87",
        "B73Q",  # 737 tanker
        "AT8T",
        "AT802",  # Air Tractor / Fire Boss
        "CL2T",
        "CL41",  # CL-415 Super Scooper
        "P3",
        "MD11",
        "B744",
    }
)

# Air attack, lead plane, and aerial supervision — the "spotters" that direct
# the drops and scout the fire, usually small twins.
AIR_ATTACK_TYPES = frozenset(
    {
        "OV10",  # CAL FIRE OV-10 Bronco air-attack
        "BE20",
        "BE30",
        "BE9L",
        "C90",  # King Air lead planes
        "C208",  # Caravan
        "PC12",
        "TBM9",
    }
)

# Rotorcraft — helitankers, recon, and crew transport. Caught by these types OR
# by ADS-B category A7 (the transponder self-declares "rotorcraft").
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

Role = str  # 'tanker' | 'air_attack' | 'helicopter' | 'other'


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) else None


def classify_role(type_code: str | None, category: str | None, alt_ft: int | None) -> Role:
    """What kind of firefighting aircraft is this — or none.

    Airframe first, because it is the only reliable signal: a jet on approach to
    Sacramento is low and slow too. A known tanker/lead/rotor type is classified
    outright; a bare ADS-B rotorcraft (A7) down low is a helicopter even without a
    matching type. Everything else is 'other' — shown near the fire, not claimed
    to be fighting it.
    """
    t = (type_code or "").upper()
    if t in TANKER_TYPES:
        return "tanker"
    if t in AIR_ATTACK_TYPES:
        return "air_attack"
    if t in HELICOPTER_TYPES:
        return "helicopter"
    if (category or "").upper() == "A7" and alt_ft is not None and alt_ft <= ROTOR_CEILING_FT:
        return "helicopter"
    return "other"


def _is_fire_ops(alt_ft: int | None, type_code: str | None, category: str | None) -> bool:
    """Air operations = any classified firefighting role. Conservative by design:
    the flag rests on the airframe, never on 'low + slow' alone (which flagged
    73 of 154 aircraft — airliners descending near a fire — in the first pass)."""
    return classify_role(type_code, category, alt_ft) != "other"


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
    ac = _fetch_near(incident["lat"], incident["lon"])
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
        role = classify_role(type_code, a.get("category"), alt_ft)
        conn.execute(
            """INSERT INTO aircraft_positions
               (hex, incident_id, flight, type, descr, lat, lon, alt_ft, gs, track,
                fire_ops, role, seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(a.get("hex") or ""),
                incident["id"],
                (a.get("flight") or "").strip() or None,
                type_code,
                a.get("desc"),
                lat,
                lon,
                alt_ft,
                gs,
                _num(a.get("track")),
                1 if role != "other" else 0,
                role,
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
        "DELETE FROM aircraft_positions WHERE unixepoch('now') - unixepoch(seen_at) > ? * 3600",
        (RETAIN_HOURS,),
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
                 WHERE unixepoch('now') - unixepoch(seen_at) < ? * 60 GROUP BY hex) last
             ON p.rowid = last.latest""",
        (minutes,),
    ).fetchall()
    craft = []
    for r in rows:
        trail = conn.execute(
            """SELECT lat, lon FROM aircraft_positions
               WHERE hex = ? AND unixepoch('now') - unixepoch(seen_at) < 3600
               ORDER BY seen_at""",
            (r["hex"],),
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
                "incident_id": r["incident_id"],
                "seen_at": r["seen_at"],
                "trail": [[t["lat"], t["lon"]] for t in trail],
            }
        )
    return {
        "aircraft": craft,
        "count": len(craft),
        "fire_ops": sum(1 for c in craft if c["fire_ops"]),
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
                 WHERE incident_id = ? AND unixepoch('now') - unixepoch(seen_at) < ? * 60
                 GROUP BY hex) last ON p.rowid = last.latest
           ORDER BY p.fire_ops DESC, p.alt_ft ASC""",
        (incident_id, minutes),
    ).fetchall()
    return [
        {
            "hex": r["hex"],
            "flight": r["flight"],
            "type": r["type"],
            "descr": r["descr"],
            "role": r["role"],
            "fire_ops": bool(r["fire_ops"]),
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
             AND unixepoch('now') - unixepoch(seen_at) < ? * 3600""",
        (incident_id, hours),
    ).fetchone()["n"]
