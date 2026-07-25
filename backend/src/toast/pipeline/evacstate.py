"""Authoritative evacuation state, derived from the Cal OES zone layer.

The free-text evac *facts* never reconcile: a lift never supersedes an order,
the official tier is ranked above recency, and the state they yield is only ever
incident-level. So a reduced zone, a downgrade, or a cancellation does not
reliably change what the pin, the counts, the graphics, or a report show.

The Cal OES aggregation layer is the opposite, and it is the source of truth
here. One row per *elevated* zone, status verbatim, refreshed every few minutes.
Because a county only publishes a zone while it is elevated, a reduced or
cancelled zone simply drops out of the layer on the next poll — the lifecycle is
self-correcting at the source. `pipeline/evaczones.py` already matches each zone
to the nearest active fire, so we can ask "what is THIS fire's current
evacuation state" and answer it from the zones a county has actually elevated.

The facts are used only as a fallback, and then only official ones (tier <= 2),
for the window where a fire has no matched zone yet — a brand-new order, or a
county outside Zonehaven coverage. Media/community evac claims never drive the
authoritative state; they remain visible in the intel panel, badged REPORTED.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from toast.pipeline.ledger import not_superseded, value_order

ZONE_CACHE_KEY = "evaczones:ca"
OFFICIAL_MAX_TIER = 2
# a zone snapshot older than this is not trusted to still be current — an evac
# that was lifted an hour ago could still be sitting in a frozen cache, so we
# fall back to official facts rather than assert a stale "active" zone
STALE_S = 60 * 60

ACTIVE = ("order", "warning", "shelter_in_place", "advisory")
# the single most urgent instruction decides the incident-level colour/word
_RANK = {"order": 4, "shelter_in_place": 3, "warning": 2, "advisory": 1}
_COUNT_KEYS = ("order", "warning", "shelter_in_place", "advisory")


def normalize_status(raw: str | None) -> str | None:
    """Map a Cal OES STATUS string (or a fact's evac_status) into our vocabulary.

    Cal OES writes "Evacuation Order"/"Evacuation Warning"/"Shelter In Place"/
    "Advisory"; a lift shows up as the zone vanishing, but if a de-elevated or
    repopulated row ever survives we treat it as not-in-effect rather than
    mis-paint it as an active order."""
    if not raw:
        return None
    s = raw.strip().lower()
    if "order" in s:
        return "order"
    if "shelter" in s:
        return "shelter_in_place"
    if "warning" in s:
        return "warning"
    if "advisory" in s:
        return "advisory"
    if "lifted" in s or "cancel" in s or "repopulat" in s or "none" in s:
        return "lifted"
    return None


@dataclass
class EvacState:
    """The current evacuation picture for one incident."""

    status: str | None  # strongest active status, or None when nothing is in effect
    counts: dict[str, int]  # per-status zone tally (order/warning/shelter/advisory)
    zones: list[dict[str, Any]]  # matched zone summaries, verbatim official fields
    source: str  # 'zones' | 'facts' | 'none' — where `status` came from

    @property
    def active(self) -> bool:
        return self.status in ACTIVE


def _zone_index(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """incident_id -> its matched Cal OES zones, or {} if the cache is missing
    or too stale to trust. One cache read serves a whole page of incidents."""
    row = conn.execute(
        """SELECT payload FROM map_cache
           WHERE key = ? AND unixepoch('now') - unixepoch(fetched_at) < ?""",
        (ZONE_CACHE_KEY, STALE_S),
    ).fetchone()
    if not row:
        return {}
    try:
        data = json.loads(row[0])
    except (ValueError, TypeError):
        return {}
    idx: dict[str, list[dict[str, Any]]] = {}
    for feature in data.get("features", []):
        p = feature.get("properties") or {}
        inc = p.get("incident_id")
        if not inc:
            continue
        idx.setdefault(inc, []).append(
            {
                "zone_id": p.get("ZONE_ID"),
                # the public id a resident recognises (YUB-E098-A), computed in
                # evaczones so every surface says the same thing
                "zone_label": p.get("zone_label"),
                "hazard": p.get("hazard"),
                "zone_name": p.get("ZONE_NAME"),
                "status_raw": p.get("STATUS"),
                "status": normalize_status(p.get("STATUS")),
                "issued_at": p.get("issued_at"),
                "last_updated_at": p.get("last_updated_at"),
                "critical_info": p.get("CRITICAL_INFO"),
                "public_info": p.get("PUBLIC_INFO"),
                "county": p.get("COUNTY"),
            }
        )
    return idx


def _from_zones(zones: list[dict[str, Any]]) -> EvacState:
    counts = dict.fromkeys(_COUNT_KEYS, 0)
    best: str | None = None
    for z in zones:
        st = z.get("status")
        if st in counts:
            counts[st] += 1
        if st and (best is None or _RANK.get(st, 0) > _RANK.get(best, 0)):
            best = st
    return EvacState(status=best, counts=counts, zones=zones, source="zones")


def _from_facts(conn: sqlite3.Connection, incident_id: str) -> EvacState:
    """Fallback: the current OFFICIAL evac fact only. Media/community evac never
    paints the pin — that is what let a Spain wildfire pulse as a CA alert."""
    row = conn.execute(
        f"""SELECT evac_status FROM facts
            WHERE incident_id = ? AND kind = 'evac' AND tier <= ?
              AND {not_superseded()}
            ORDER BY {value_order()} LIMIT 1""",
        (incident_id, OFFICIAL_MAX_TIER),
    ).fetchone()
    counts = dict.fromkeys(_COUNT_KEYS, 0)
    if row is None:
        return EvacState(status=None, counts=counts, zones=[], source="none")
    st = normalize_status(row[0])
    if st == "lifted":
        st = None
    if st in counts:
        counts[st] = 1
    return EvacState(status=st, counts=counts, zones=[], source="facts")


def evac_state(
    conn: sqlite3.Connection,
    incident_id: str,
    *,
    zone_index: dict[str, list[dict[str, Any]]] | None = None,
) -> EvacState:
    """The authoritative evacuation state for one incident: zones first, official
    facts as a fallback. Pass `zone_index` (from `_zone_index`) when computing
    many incidents so the cache is read once."""
    idx = zone_index if zone_index is not None else _zone_index(conn)
    zones = idx.get(incident_id, [])
    if zones:
        return _from_zones(zones)
    return _from_facts(conn, incident_id)


def zone_index(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """Public accessor for callers that compute a whole page of incidents."""
    return _zone_index(conn)
