"""Evacuation zone polygons, from Cal OES — the compliant path.

The zones people know ("US-CA-XBU-PARADISE-A") live in Genasys Protect, whose
terms prohibit programmatic use. It does not matter: the California Office of
Emergency Services aggregates every county's zone service into one public
feature layer on the state open-data portal — same polygons, same Zonehaven
zone ids, same statuses, refreshed every 5 minutes, published by the state's
own emergency authority. Tier-1 official, no vendor terms in the way.

Doctrine holds here harder than anywhere: STATUS and the official text fields
(CRITICAL_INFO / PUBLIC_INFO) are rendered VERBATIM — never summarized, never
rewritten. The layer only ever contains zones a county has elevated, so every
polygon on the map is an active instruction to someone.
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
from toast.pipeline.incidents import haversine_km
from toast.pipeline.perimeters import _centroid

SERVICE = (
    "https://services.arcgis.com/BLN4oKB0N1YSgvY8/arcgis/rest/services/"
    "CA_EVACUATIONS_CalOESHosted_view/FeatureServer/0/query"
)
CACHE_KEY = "evaczones:ca"
CACHE_TTL_S = 4 * 60  # the layer rebuilds every 5 minutes; never poll faster
MATCH_RADIUS_KM = 60.0

# Non-fire hazards ride this same statewide layer — Tulare's flood evacuations
# sit next to Inyo's wildfire ones. A wildfire tool must not present a flood
# order as a fire order, so each zone is tagged with the hazard its own official
# text names. Checked fire-first: a wildfire note always says so.
_HAZARD_WORDS: tuple[tuple[str, str], ...] = (
    ("debris flow", "debris flow"),
    ("mudslide", "mudslide"),
    ("mud slide", "mudslide"),
    ("flood", "flood"),
    ("hazmat", "hazmat"),
    ("chemical", "hazmat"),
    ("gas leak", "gas leak"),
    ("tsunami", "tsunami"),
    ("earthquake", "earthquake"),
    ("law enforcement", "law enforcement"),
    ("shooting", "law enforcement"),
    ("storm", "storm"),
)


def public_zone_id(raw: str | None) -> str | None:
    """The zone id a county publishes and a resident recognises.

    Cal OES ids are `US-CA-<county code>-<agency>-<zone>[-<sub>]`, e.g.
    `US-CA-XYU-YUB-E098-A`. The county's own notice calls that zone
    "YUB-E098-A" — country/state/county-code are routing, not identity. So we
    strip that prefix and keep the agency onward.

    This matters more than cosmetics: the map used to show only the LAST dash
    segment, which collapsed `ICU-123-A` and `ICU-143-A` both to "A" — two
    different zones, under different orders, wearing the same label.
    """
    if not raw:
        return None
    s = str(raw).strip()
    parts = s.split("-")
    if len(parts) >= 5 and parts[0].upper() == "US" and parts[1].upper() == "CA":
        return "-".join(parts[3:])
    if len(parts) >= 3 and parts[0].upper() == "US" and parts[1].upper() == "CA":
        return "-".join(parts[2:])
    return s


def hazard_of(props: dict[str, Any]) -> str | None:
    """What this evacuation is FOR, from the zone's own official text."""
    text = " ".join(
        str(props.get(k) or "")
        for k in ("EVENT_TYPE", "NOTES", "CRITICAL_INFO", "PUBLIC_INFO")
    ).lower()
    if not text.strip():
        return None
    if "fire" in text:  # wildfire/wild fire/structure fire — the common case
        return "fire"
    for needle, label in _HAZARD_WORDS:
        if needle in text:
            return label
    return None


def _inherit_family_hazard(features: list[dict[str, Any]]) -> None:
    """A zone split into A/B is one event. When one sibling's text names the
    hazard ("...related to the Chutes Fire") and the other is generic
    ("Mandatory Evacuation. Please stay out of the area."), the silent one
    inherits — same place, same emergency. Only ever fills a blank; a zone that
    states its own hazard is never overwritten."""
    families: dict[str, list[dict[str, Any]]] = {}
    for f in features:
        p = f.get("properties") or {}
        parent = _parent_zone(p.get("ZONE_ID"))
        if parent:
            families.setdefault(parent, []).append(p)
    for members in families.values():
        stated = {p.get("hazard") for p in members if p.get("hazard")}
        if len(stated) != 1:
            continue  # silent family, or siblings disagree — claim nothing
        hazard = stated.pop()
        for p in members:
            if not p.get("hazard"):
                p["hazard"] = hazard
                p["hazard_inherited"] = True


def _parent_zone(zone_id: str | None) -> str | None:
    """`ICU-123-A` -> `ICU-123`. A county splitting a zone into A/B keeps them
    adjacent on the ground, so they belong to the same fire."""
    pid = public_zone_id(zone_id)
    if not pid:
        return None
    parts = pid.split("-")
    if len(parts) > 1 and len(parts[-1]) == 1 and parts[-1].isalpha():
        return "-".join(parts[:-1])
    return pid


def _epoch_iso(ms: Any) -> str | None:
    if not isinstance(ms, int | float) or ms <= 0:
        return None
    return (
        datetime.fromtimestamp(ms / 1000.0, tz=UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def fetch_zones() -> dict[str, Any]:
    query = urlencode(
        {
            "where": "1=1",  # the layer carries only elevated zones
            "outFields": "COUNTY,CITY,ZONE_ID,ZONE_NAME,STATUS,EVENT_TYPE,"
            "CRITICAL_INFO,PUBLIC_INFO,NOTES,EDIT_DATE,STATEWIDE_LAST_UPDATED,"
            "CreationDate,EditDate",
            "returnGeometry": "true",
            "outSR": 4326,
            "f": "geojson",
        }
    )
    body, _ctype = fetch(
        f"{SERVICE}?{query}",
        accept_types=("application/json", "application/geo+json", "text/plain"),
        max_bytes=24 * 1024 * 1024,
    )
    data = json.loads(body)
    if data.get("type") != "FeatureCollection":
        raise RuntimeError(f"Cal OES evac layer returned no collection: {str(data)[:120]}")
    return data


def _match_incidents(conn: sqlite3.Connection, features: list[dict[str, Any]]) -> int:
    """Tag each zone with the nearest active fire within range, so the intel
    panel can say which zones belong to which incident."""
    incidents = [
        dict(r)
        for r in conn.execute(
            """SELECT id, canonical_name, lat, lon FROM incidents
               WHERE status = 'active' AND lat IS NOT NULL"""
        )
    ]
    matched = 0
    # first pass: nearest active fire within range, per zone
    for feature in features:
        props = feature.setdefault("properties", {})
        props["incident_id"] = None
        centre = _centroid(feature.get("geometry") or {})
        if not centre:
            continue
        best = None
        for inc in incidents:
            km = haversine_km(centre[0], centre[1], inc["lat"], inc["lon"])
            if km <= MATCH_RADIUS_KM and (best is None or km < best[1]):
                best = (inc, km)
        if best:
            props["incident_id"] = best[0]["id"]
            props["incident_name"] = best[0]["canonical_name"]
            props["distance_km"] = round(best[1], 1)

    # second pass: a zone a county SPLIT (ICU-123-A/B/C/D) is one place on the
    # ground. Nearest-centroid alone scattered those siblings across two fires
    # when both were in range, mis-attributing each fire's evacuation count.
    # The closest sibling decides for the whole family.
    families: dict[str, list[dict[str, Any]]] = {}
    for feature in features:
        props = feature["properties"]
        parent = _parent_zone(props.get("ZONE_ID"))
        if parent and props.get("incident_id"):
            families.setdefault(parent, []).append(props)
    for members in families.values():
        if len(members) < 2:
            continue
        anchor = min(members, key=lambda p: p.get("distance_km") or 1e9)
        for p in members:
            if p["incident_id"] != anchor["incident_id"]:
                p["incident_id"] = anchor["incident_id"]
                p["incident_name"] = anchor.get("incident_name")
                p["regrouped_with"] = anchor.get("ZONE_ID")

    matched = sum(1 for f in features if f["properties"].get("incident_id"))
    return matched


def get_evac_zones(conn: sqlite3.Connection, *, refresh: bool = False) -> dict[str, Any]:
    """Elevated evacuation zones statewide, statuses verbatim."""
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

    data = fetch_zones()
    features = data.get("features", [])
    for f in features:
        p = f.setdefault("properties", {})
        for key in ("EDIT_DATE", "STATEWIDE_LAST_UPDATED", "CreationDate", "EditDate"):
            p[key] = _epoch_iso(p.get(key)) or p.get(key)
        # CreationDate is when this elevated zone entered the state layer — the
        # closest thing the feed has to "when it was issued". EditDate is the
        # county's last touch. Both official, both shown as-is.
        p["issued_at"] = p.get("CreationDate")
        p["last_updated_at"] = p.get("EditDate") or p.get("EDIT_DATE")
        # the id a resident recognises, computed once here so every surface
        # (map label, tooltip, EVAC tab, cards, reports) says the same thing
        p["zone_label"] = public_zone_id(p.get("ZONE_ID"))
        p["hazard"] = hazard_of(p)
    _inherit_family_hazard(features)
    matched = _match_incidents(conn, features)
    statuses: dict[str, int] = {}
    hazards: dict[str, int] = {}
    for f in features:
        s = (f["properties"].get("STATUS") or "unknown").strip()
        statuses[s] = statuses.get(s, 0) + 1
        h = f["properties"].get("hazard") or "unstated"
        hazards[h] = hazards.get(h, 0) + 1

    payload: dict[str, Any] = {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "matched": matched,
        "statuses": statuses,
        "hazards": hazards,
        "source": "Cal OES California Evacuation Aggregation Layer (state open data)",
        "note": (
            "Official zone statuses, rendered verbatim. The layer holds only "
            "zones a county has elevated — every polygon is an active "
            "instruction. Rebuilds every 5 minutes from county sources."
        ),
        "fetched_at": utcnow(),
    }
    conn.execute(
        "INSERT OR REPLACE INTO map_cache (key, payload, fetched_at) VALUES (?, ?, ?)",
        (CACHE_KEY, json.dumps(payload), payload["fetched_at"]),
    )
    conn.commit()
    get_logger("evaczones").info("evac_zones_fetched", zones=len(features), matched=matched)
    return payload
