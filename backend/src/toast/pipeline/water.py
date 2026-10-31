"""Flood season: river gauges and NWS flood alerts, officials only.

Two free, keyless, official feeds:

* NOAA/NWS water gauges (api.water.noaa.gov) — each gauge carries the observed
  and the NWS-forecast flood category (action / minor / moderate / major).
* NWS active alerts — Flood Watch/Warning/Advisory, Flash Flood Watch/Warning.

Doctrine, same as the fire side: we relay the official category and the official
forecast. We never compute a crest, never advise, and every reading carries the
time it was valid — gauge data is provisional and a stale number is worse than
none. Alert text is stored verbatim and treated as hostile input.
"""

from __future__ import annotations

import html
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

import httpx

from toast.core.appsettings import get_setting
from toast.core.config import Settings
from toast.core.db import utcnow
from toast.core.fetch import fetch
from toast.core.logging import get_logger

GAUGES_URL = (
    "https://api.water.noaa.gov/nwps/v1/gauges"
    "?bbox.xmin=-125&bbox.ymin=32&bbox.xmax=-114&bbox.ymax=42.5&srid=EPSG_4326"
)
ALERTS_URL = "https://api.weather.gov/alerts/active?area=CA"
CACHE_KEY = "water:ca"
CACHE_TTL_S = 10 * 60
STATE_KEY = "water:notify:state"
STALE_OBS_S = 3 * 3600

FLOOD_EVENTS = frozenset(
    {
        "Flash Flood Warning",
        "Flash Flood Watch",
        "Flood Warning",
        "Flood Watch",
        "Flood Advisory",
        "Coastal Flood Warning",
        "Coastal Flood Watch",
        "Coastal Flood Advisory",
    }
)
# events worth waking the operator for (the rest show in the app only)
URGENT_EVENTS = frozenset({"Flash Flood Warning", "Flood Warning", "Coastal Flood Warning"})

CATEGORY_RANK = {"action": 1, "minor": 2, "moderate": 3, "major": 4}
SEVERITY_RANK = {"Extreme": 4, "Severe": 3, "Moderate": 2, "Minor": 1}


def _rank(category: str | None) -> int:
    return CATEGORY_RANK.get((category or "").lower(), 0)


def _reading(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not raw:
        return None
    return {
        "stage": raw.get("primary") if raw.get("primaryUnit") == "ft" else None,
        "flow": raw.get("secondary"),
        "flow_unit": raw.get("secondaryUnit"),
        "category": raw.get("floodCategory"),
        "valid_time": raw.get("validTime"),
    }


def _age_s(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    try:
        return (now - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds()
    except ValueError:
        return None


def watched_lids(conn: sqlite3.Connection) -> set[str]:
    raw = get_setting(conn, "water_watch_lids") or ""
    return {t.strip().upper() for t in raw.replace(";", ",").split(",") if t.strip()}


def build_gauges(raw: list[dict[str, Any]], pinned: set[str]) -> list[dict[str, Any]]:
    """Gauges at or above action stage (observed or forecast), plus pinned ones."""
    now = datetime.now(UTC)
    out: list[dict[str, Any]] = []
    for g in raw:
        status = g.get("status") or {}
        obs, fcst = _reading(status.get("observed")), _reading(status.get("forecast"))
        severity = max(_rank((obs or {}).get("category")), _rank((fcst or {}).get("category")))
        lid = str(g.get("lid") or "").upper()
        if severity == 0 and lid not in pinned:
            continue
        age = _age_s((obs or {}).get("valid_time"), now)
        out.append(
            {
                "lid": lid,
                "name": g.get("name"),
                "lat": g.get("latitude"),
                "lon": g.get("longitude"),
                "observed": obs,
                "forecast": fcst,
                "severity": severity,
                "pinned": lid in pinned,
                "stale": age is None or age > STALE_OBS_S,
            }
        )
    out.sort(key=lambda g: (-g["severity"], not g["pinned"], g["name"] or ""))
    return out


def build_alerts(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in features:
        p = f.get("properties") or {}
        if p.get("event") not in FLOOD_EVENTS:
            continue
        out.append(
            {
                "id": p.get("id") or f.get("id"),
                "event": p.get("event"),
                "severity": p.get("severity"),
                "urgency": p.get("urgency"),
                "headline": (p.get("headline") or "")[:250],
                "area": (p.get("areaDesc") or "")[:300],
                "onset": p.get("onset"),
                "ends": p.get("ends") or p.get("expires"),
                "description": (p.get("description") or "")[:1500],
                "instruction": (p.get("instruction") or "")[:800],
                "sender": p.get("senderName"),
            }
        )
    out.sort(key=lambda a: (-SEVERITY_RANK.get(a["severity"] or "", 0), a["event"] or ""))
    return out


def _get_json(url: str) -> dict[str, Any]:
    body, _ = fetch(
        url,
        accept_types=("application/json", "application/geo+json", "text/plain"),
        max_bytes=8 * 1024 * 1024,
    )
    return json.loads(body)


def get_water(conn: sqlite3.Connection, *, refresh: bool = False) -> dict[str, Any]:
    """The WATER board payload, cached for CACHE_TTL_S. Pinned-gauge changes
    apply immediately because the cache holds the filtered view's inputs."""
    if not refresh:
        row = conn.execute(
            """SELECT payload, fetched_at FROM map_cache
               WHERE key = ? AND unixepoch('now') - unixepoch(fetched_at) < ?""",
            (CACHE_KEY, CACHE_TTL_S),
        ).fetchone()
        if row:
            cached = json.loads(row["payload"])
            cached["gauges"] = build_gauges(cached.pop("raw_gauges"), watched_lids(conn))
            cached["cached"] = True
            return cached

    errors: list[str] = []
    raw_gauges: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    try:
        raw_gauges = _get_json(GAUGES_URL).get("gauges") or []
    except Exception as exc:  # one feed down must not blank the other
        errors.append(f"gauges: {type(exc).__name__}")
    try:
        alerts = build_alerts(_get_json(ALERTS_URL).get("features") or [])
    except Exception as exc:
        errors.append(f"alerts: {type(exc).__name__}")
    if errors and not raw_gauges and not alerts:
        # both feeds failed — keep the last good payload rather than caching "all clear"
        raise RuntimeError("water feeds unreachable: " + ", ".join(errors))

    fetched_at = utcnow()
    # keep only the fields build_gauges reads: ~1,000 gauges, a handful matter
    slim = [
        {k: g.get(k) for k in ("lid", "name", "latitude", "longitude", "status")}
        for g in raw_gauges
        if g.get("lid")
    ]
    payload: dict[str, Any] = {
        "alerts": alerts,
        "raw_gauges": slim,
        "errors": errors,
        "fetched_at": fetched_at,
        "note": "Official NWS/NOAA flood categories and alerts, relayed as issued. "
        "Gauge readings are provisional; check the valid time on each.",
    }
    conn.execute(
        "INSERT OR REPLACE INTO map_cache (key, payload, fetched_at) VALUES (?, ?, ?)",
        (CACHE_KEY, json.dumps(payload), fetched_at),
    )
    conn.commit()
    get_logger("water").info("water_refreshed", gauges=len(slim), alerts=len(alerts))
    view = dict(payload)
    view["gauges"] = build_gauges(view.pop("raw_gauges"), watched_lids(conn))
    view["cached"] = False
    return view


def notify_water(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Phone alerts for new urgent flood warnings and gauges newly in moderate/
    major flood. First run baselines silently; each item alerts once."""
    from toast.pipeline import telegram_ops

    cfg = telegram_ops._cfg(settings)
    state = telegram_ops._state(conn, STATE_KEY)
    first_run = state is None
    state = state or {"alerts": [], "gauges": {}}
    seen_alerts = set(state["alerts"])
    seen_gauges: dict[str, int] = state["gauges"]
    messages: list[str] = []

    for a in payload["alerts"]:
        if a["event"] in URGENT_EVENTS and a["id"] and a["id"] not in seen_alerts:
            seen_alerts.add(a["id"])
            messages.append(
                f"🌊 <b>{html.escape(a['event'].upper())}</b>\n{html.escape(a['area'][:160])}\n"
                "Official text is in the workstation — verify before repeating."
            )
    for g in payload["gauges"]:
        level = max(_rank((g["observed"] or {}).get("category")), 0)
        if level >= CATEGORY_RANK["moderate"] and seen_gauges.get(g["lid"], 0) < level:
            label = (g["observed"] or {}).get("category", "").upper()
            messages.append(
                f"🌊 <b>{html.escape(g['name'] or g['lid'])}</b> at {label} flood stage"
            )
        seen_gauges[g["lid"]] = level
    # forget alerts that have expired so the set doesn't grow all winter
    live_ids = {a["id"] for a in payload["alerts"]}
    state["alerts"] = sorted(seen_alerts & live_ids) if not first_run else sorted(seen_alerts)
    state["gauges"] = seen_gauges

    sent = 0
    if cfg and messages and not first_run:
        token, chat = cfg
        own = client is None
        client = client or httpx.Client(timeout=30)
        try:
            for m in messages:
                if telegram_ops._send(client, token, chat, m):
                    sent += 1
        finally:
            if own:
                client.close()
    telegram_ops._save(conn, STATE_KEY, state)
    return {"sent": sent, "baselined": first_run}
