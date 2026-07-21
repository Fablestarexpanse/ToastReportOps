"""AIR mode: the aviation-first view of a fire.

Everything here is throttled around one hard fact: airplanes.live allows about
one request per second, total, for the whole application. The scheduler's
60-second all-fires sweep keeps running underneath; this module adds the
fast, focused asks an operator watching air ops actually needs:

- focus_poll: the SELECTED fire's airspace every few seconds — smooth orbits.
- base_watch: CAL FIRE / USFS tanker bases, one base per window, round-robin.
  A tanker lifting from base is the earliest signal a fire is getting worked.
- mil_snapshot: the /mil feed clipped to California — MAFFS and Guard hulls.
- activity_log: arrivals/departures per fire, derived from our own stored
  positions. Borrowed live data never enters the ledger; the log is a VIEW
  over the position store, recomputed on demand, gone when the store prunes.

Throttles live in map_cache so every worker and window shares one budget.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from toast.core.db import utcnow
from toast.core.fetch import fetch
from toast.pipeline.aircraft import (
    SERVICE,
    classify_role,
    is_military,
    poll_incident,
)

FOCUS_MIN_INTERVAL_S = 4  # keeps two open windows inside the shared 1 req/s
BASE_MIN_INTERVAL_S = 5
MIL_MIN_INTERVAL_S = 30
TRAIL_MINUTES = 15  # one full tanker orbit and the last few drop runs
CURRENT_WINDOW_S = 180  # silent for 3 min = no longer on station
SESSION_GAP_S = 600  # >10 min silent then back = a new sortie
BASE_RADIUS_NM = 8

# CAL FIRE air attack / USFS tanker bases, curated. Coordinates are airport
# reference points — the 8 nm watch circle forgives small error. Labelled data,
# not asserted fact; a base sighting never enters the ledger.
BASES: list[dict[str, Any]] = [
    {"id": "CIC", "name": "Chico Air Attack Base", "lat": 39.795, "lon": -121.858},
    {"id": "MCC", "name": "McClellan Air Tanker Base", "lat": 38.667, "lon": -121.401},
    {"id": "RDD", "name": "Redding Air Attack Base", "lat": 40.509, "lon": -122.293},
    {"id": "GOO", "name": "Grass Valley AAB", "lat": 39.224, "lon": -121.003},
    {"id": "STS", "name": "Sonoma AAB (Santa Rosa)", "lat": 38.509, "lon": -122.813},
    {"id": "UKI", "name": "Ukiah AAB", "lat": 39.126, "lon": -123.201},
    {"id": "O22", "name": "Columbia AAB", "lat": 38.030, "lon": -120.415},
    {"id": "CVH", "name": "Hollister AAB", "lat": 36.893, "lon": -121.410},
    {"id": "FAT", "name": "Fresno Air Tanker Base", "lat": 36.776, "lon": -119.718},
    {"id": "PTV", "name": "Porterville AAB", "lat": 36.030, "lon": -119.063},
    {"id": "PRB", "name": "Paso Robles AAB", "lat": 35.673, "lon": -120.627},
    {"id": "WJF", "name": "Fox Field (Lancaster)", "lat": 34.741, "lon": -118.219},
    {"id": "SBD", "name": "San Bernardino ATB", "lat": 34.095, "lon": -117.235},
    {"id": "HMT", "name": "Hemet-Ryan AAB", "lat": 33.734, "lon": -117.023},
    {"id": "RNM", "name": "Ramona AAB", "lat": 33.039, "lon": -116.915},
]

CA_BBOX = (32.0, 42.5, -125.0, -113.5)  # lat min/max, lon min/max


def _alt(a: dict[str, Any]) -> int | None:
    """alt_baro is an int in flight and the string "ground" on the ramp."""
    v = a.get("alt_baro")
    return v if isinstance(v, int | float) else None


class Throttle:
    """A shared minimum interval, kept in map_cache so all callers see it."""

    def __init__(self, key: str, min_interval_s: int):
        self.key = f"air:throttle:{key}"
        self.min_interval_s = min_interval_s

    def allow(self, conn: sqlite3.Connection) -> bool:
        row = conn.execute(
            """SELECT 1 FROM map_cache
               WHERE key = ? AND unixepoch('now') - unixepoch(fetched_at) < ?""",
            (self.key, self.min_interval_s),
        ).fetchone()
        if row:
            return False
        conn.execute(
            "INSERT OR REPLACE INTO map_cache (key, payload, fetched_at) VALUES (?, '{}', ?)",
            (self.key, utcnow()),
        )
        conn.commit()
        return True


def _cache_put(conn: sqlite3.Connection, key: str, payload: dict[str, Any]) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO map_cache (key, payload, fetched_at) VALUES (?, ?, ?)",
        (key, json.dumps(payload), utcnow()),
    )
    conn.commit()


def _cache_get(conn: sqlite3.Connection, key: str) -> tuple[dict[str, Any] | None, str | None]:
    row = conn.execute(
        "SELECT payload, fetched_at FROM map_cache WHERE key = ?", (key,)
    ).fetchone()
    if not row:
        return None, None
    return json.loads(row["payload"]), row["fetched_at"]


# ---------------------------------------------------------------- focus poll


def focus_poll(conn: sqlite3.Connection, incident_id: str) -> dict[str, Any]:
    """The selected fire's airspace, refreshed as fast as the budget allows.

    Between allowed upstream hits, callers get the current picture from the
    position store — so a 2-second UI poll degrades gracefully to reads."""
    inc = conn.execute(
        "SELECT * FROM incidents WHERE id = ?", (incident_id,)
    ).fetchone()
    if inc is None:
        raise ValueError("unknown incident")
    if inc["lat"] is None or inc["lon"] is None:
        return {"aircraft": [], "count": 0, "note": "incident has no coordinates"}

    polled = False
    if Throttle(f"focus:{incident_id}", FOCUS_MIN_INTERVAL_S).allow(conn):
        poll_incident(conn, inc)
        polled = True

    aircraft = _over_incident(conn, incident_id)
    return {
        "aircraft": aircraft,
        "count": len(aircraft),
        "fire_ops": sum(1 for a in aircraft if a["fire_ops"]),
        "polled_upstream": polled,
        "fetched_at": utcnow(),
        "note": "airplanes.live (borrowed live data — never enters the ledger)",
    }


def _over_incident(conn: sqlite3.Connection, incident_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""SELECT * FROM aircraft_positions
            WHERE incident_id = ?
              AND unixepoch('now') - unixepoch(seen_at) < {TRAIL_MINUTES * 60}
            ORDER BY seen_at""",
        (incident_id,),
    ).fetchall()
    by_hex: dict[str, dict[str, Any]] = {}
    for r in rows:
        entry = by_hex.setdefault(
            r["hex"],
            {"hex": r["hex"], "trail": [], "first_seen": r["seen_at"]},
        )
        entry["trail"].append([r["lat"], r["lon"]])
        entry.update(
            {
                "flight": r["flight"],
                "type": r["type"],
                "descr": r["descr"],
                "lat": r["lat"],
                "lon": r["lon"],
                "alt_ft": r["alt_ft"],
                "gs": r["gs"],
                "track": r["track"],
                "role": r["role"],
                "military": bool(r["military"]),
                "fire_ops": bool(r["fire_ops"]),
                "seen_at": r["seen_at"],
            }
        )
    cutoff = CURRENT_WINDOW_S
    current = [
        a
        for a in by_hex.values()
        if _age_s(conn, a["seen_at"]) < cutoff
    ]
    current.sort(key=lambda a: (not a["fire_ops"], a["role"] or "z"))
    return current


def _age_s(conn: sqlite3.Connection, iso: str) -> float:
    return conn.execute(
        "SELECT unixepoch('now') - unixepoch(?)", (iso,)
    ).fetchone()[0]


# ---------------------------------------------------------------- base watch


def base_watch(conn: sqlite3.Connection) -> dict[str, Any]:
    """Round-robin over the base list: one base per throttle window, and a
    rolling picture of which fire aircraft sit at (or just left) each base."""
    state, _at = _cache_get(conn, "air:bases:state")
    state = state or {"next": 0, "sightings": {}}

    if Throttle("bases", BASE_MIN_INTERVAL_S).allow(conn):
        i = state["next"] % len(BASES)
        base = BASES[i]
        state["next"] = i + 1
        try:
            body, _ct = fetch(
                f"{SERVICE}/{base['lat']}/{base['lon']}/{BASE_RADIUS_NM}",
                accept_types=("application/json", "text/plain"),
                max_bytes=2 * 1024 * 1024,
            )
            ac = json.loads(body).get("ac") or []
        except Exception:
            ac = []  # a failed base scan skips its turn; the rotation continues
        now = utcnow()
        seen_now = set()
        for a in ac:
            role = classify_role(
                a.get("t"), a.get("category"), _alt(a),
                a.get("flight"), a.get("ownOp"),
            )
            if role == "other" and not is_military(a.get("dbFlags")):
                continue  # base watch cares about fire + military hulls only
            hx = (a.get("hex") or "").lower()
            if not hx:
                continue
            seen_now.add(hx)
            state["sightings"][hx] = {
                "hex": hx,
                "base": base["id"],
                "base_name": base["name"],
                "flight": (a.get("flight") or "").strip() or None,
                "type": a.get("t"),
                "descr": a.get("desc"),
                "role": role,
                "military": is_military(a.get("dbFlags")),
                "airborne": _alt(a) is not None,
                "last_seen": now,
            }
        # a hull seen at THIS base earlier but absent from this scan has left it
        for hx, s in state["sightings"].items():
            if s["base"] == base["id"] and hx not in seen_now and not s.get("departed_at"):
                s["departed_at"] = now
        # forget sightings older than an hour — this is a watch, not a record
        state["sightings"] = {
            hx: s
            for hx, s in state["sightings"].items()
            if _age_s(conn, s["last_seen"]) < 3600
        }
        state["scanned"] = {"base": base["id"], "at": now}
        _cache_put(conn, "air:bases:state", state)

    sightings = sorted(
        state["sightings"].values(),
        key=lambda s: (s.get("departed_at") is None, s["last_seen"]),
        reverse=True,
    )
    return {
        "bases": BASES,
        "sightings": sightings,
        "last_scan": state.get("scanned"),
        "note": "one base scanned per window — a full sweep takes ~"
        f"{len(BASES) * BASE_MIN_INTERVAL_S // 60} min",
    }


# ---------------------------------------------------------------- military


def mil_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    """Military aircraft over California, from the dedicated /mil feed."""
    cached, at = _cache_get(conn, "air:mil")
    # the throttle is consumed even on a cold cache, so the FIRST fetch also
    # starts the window — otherwise two cold calls hit upstream back-to-back
    allowed = Throttle("mil", MIL_MIN_INTERVAL_S).allow(conn)
    if cached is not None and not allowed:
        cached["fetched_at"] = at
        return cached
    try:
        body, _ct = fetch(
            f"{SERVICE.rsplit('/point', 1)[0]}/mil",
            accept_types=("application/json", "text/plain"),
            max_bytes=8 * 1024 * 1024,
        )
        ac = json.loads(body).get("ac") or []
    except Exception:
        if cached is not None:
            cached["fetched_at"] = at
            cached["stale"] = True
            return cached
        raise
    lat0, lat1, lon0, lon1 = CA_BBOX
    out = []
    for a in ac:
        lat, lon = a.get("lat"), a.get("lon")
        if not isinstance(lat, int | float) or not isinstance(lon, int | float):
            continue
        if not (lat0 <= lat <= lat1 and lon0 <= lon <= lon1):
            continue
        out.append(
            {
                "hex": (a.get("hex") or "").lower(),
                "flight": (a.get("flight") or "").strip() or None,
                "type": a.get("t"),
                "descr": a.get("desc"),
                "lat": lat,
                "lon": lon,
                "alt_ft": _alt(a),
                "gs": a.get("gs"),
                "track": a.get("track"),
                "role": classify_role(
                    a.get("t"), a.get("category"), _alt(a),
                    a.get("flight"), a.get("ownOp"),
                ),
            }
        )
    payload = {"aircraft": out, "count": len(out), "note": "airplanes.live /mil, CA only"}
    _cache_put(conn, "air:mil", payload)
    payload["fetched_at"] = utcnow()
    return payload


# ---------------------------------------------------------------- activity log


def activity_log(conn: sqlite3.Connection, incident_id: str) -> dict[str, Any]:
    """Arrivals and departures over one fire, from our own position store.

    A session is continuous presence; >10 minutes silent then back is a new
    sortie (load-and-return is exactly this pattern for a tanker)."""
    rows = conn.execute(
        """SELECT hex, flight, type, descr, role, military, fire_ops, seen_at
           FROM aircraft_positions WHERE incident_id = ? ORDER BY hex, seen_at""",
        (incident_id,),
    ).fetchall()
    sessions: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for r in rows:
        gap = (
            None
            if cur is None or cur["hex"] != r["hex"]
            else conn.execute(
                "SELECT unixepoch(?) - unixepoch(?)", (r["seen_at"], cur["last_seen"])
            ).fetchone()[0]
        )
        if cur is None or cur["hex"] != r["hex"] or (gap is not None and gap > SESSION_GAP_S):
            cur = {
                "hex": r["hex"],
                "flight": r["flight"],
                "type": r["type"],
                "descr": r["descr"],
                "role": r["role"],
                "military": bool(r["military"]),
                "fire_ops": bool(r["fire_ops"]),
                "arrived": r["seen_at"],
                "last_seen": r["seen_at"],
                "positions": 1,
            }
            sessions.append(cur)
        else:
            cur["last_seen"] = r["seen_at"]
            cur["positions"] += 1
            if r["flight"] and not cur["flight"]:
                cur["flight"] = r["flight"]
    for s in sessions:
        s["on_station_s"] = conn.execute(
            "SELECT unixepoch(?) - unixepoch(?)", (s["last_seen"], s["arrived"])
        ).fetchone()[0]
        s["ongoing"] = _age_s(conn, s["last_seen"]) < CURRENT_WINDOW_S
    sessions.sort(key=lambda s: s["arrived"], reverse=True)
    return {
        "sessions": sessions,
        "count": len(sessions),
        "note": f"derived from stored positions (kept {12} h) — a view, not a record",
    }
