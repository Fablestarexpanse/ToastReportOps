"""Watch board — new-start detection.

Catches fires before they're officially listed: when independent sources name a
fire that isn't (yet) in the active-incident list, they cluster into a candidate
new-start, scored by how many distinct sources corroborate it. The operator
promotes a candidate to a real incident (attributing its signals) or lets it ride
until an official source confirms it.

Signals today are named-fire mentions from news/social/official feeds that failed
to attribute to an existing incident. Aircraft-orbit / hotspot-persistence /
scanner signals slot in here as those sources come online.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from toast.core.db import utcnow
from toast.core.ids import ulid
from toast.pipeline.attribution import _norm_name, name_similarity

CORROBORATING_SOURCES = 2  # distinct sources at/above which a candidate corroborates
RECENT_WINDOW_S = 48 * 3600  # signals older than this don't sustain a new-start
# generic descriptors, not specific fire names — these clusters are noise
GENERIC_NAMES = {
    "brush",
    "grass",
    "structure",
    "vegetation",
    "wildfire",
    "wildland",
    "forest",
    "spot",
    "the",
    "a",
    "new",
    "major",
}


@dataclass
class WatchCandidate:
    key: str  # normalized name (cluster key)
    name: str  # display name (as first reported)
    source_count: int
    fact_count: int
    latest_observed: str
    tiers: list[int]
    lat: float | None
    lon: float | None
    county: str | None
    score: float
    state: str  # 'watching' | 'corroborating'
    fact_ids: list[str] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)


def _active_incident_names(conn: sqlite3.Connection) -> list[tuple[str, list[str]]]:
    names = []
    for inc in conn.execute(
        "SELECT id, canonical_name FROM incidents "
        "WHERE status IN ('candidate','active','contained')"
    ):
        aliases = [
            r["alias"]
            for r in conn.execute(
                "SELECT alias FROM incident_aliases WHERE incident_id = ?", (inc["id"],)
            )
        ]
        names.append((inc["canonical_name"], aliases))
    return names


def _matches_active_incident(name: str, active: list[tuple[str, list[str]]]) -> bool:
    return any(name_similarity(name, canon, aliases) >= 0.85 for canon, aliases in active)


def build_watchlist(conn: sqlite3.Connection) -> list[WatchCandidate]:
    """Cluster unattributed named-fire signals into candidate new-starts."""
    active = _active_incident_names(conn)
    rows = conn.execute(
        """SELECT f.id, f.source_id, f.tier, f.content, f.observed_at, f.lat, f.lon,
                  json_extract(f.structured, '$.incident_name') AS inc_name,
                  json_extract(f.structured, '$.location_text') AS loc
           FROM facts f
           WHERE f.incident_id IS NULL
             AND json_extract(f.structured, '$.incident_name') IS NOT NULL
             AND unixepoch('now') - unixepoch(f.observed_at) < ?
           ORDER BY f.observed_at DESC""",
        (RECENT_WINDOW_S,),
    ).fetchall()

    clusters: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        key = _norm_name(r["inc_name"] or "")
        if not key or key in GENERIC_NAMES:
            continue  # empty or a generic descriptor, not a specific fire name
        clusters.setdefault(key, []).append(r)

    candidates: list[WatchCandidate] = []
    for key, facts in clusters.items():
        display = facts[0]["inc_name"]
        # skip clusters that actually name an existing incident (those are
        # attribution/adjudication cases, not new starts)
        if _matches_active_incident(display, active):
            continue
        sources = {f["source_id"] for f in facts}
        geo = next((f for f in facts if f["lat"] is not None and f["lon"] is not None), None)
        latest = max(f["observed_at"] for f in facts)
        tiers = sorted({f["tier"] for f in facts})
        # score: distinct sources dominate (real cross-checking), signal volume
        # and best tier add smaller lift so a well-reported single-source fire
        # still surfaces above one-off mentions.
        best_tier_bonus = (6 - min(tiers)) * 0.04
        volume_bonus = 0.04 * min(len(facts), 5)
        score = min(1.0, 0.2 + 0.28 * len(sources) + volume_bonus + best_tier_bonus)
        state = "corroborating" if len(sources) >= CORROBORATING_SOURCES else "watching"
        candidates.append(
            WatchCandidate(
                key=key,
                name=display,
                source_count=len(sources),
                fact_count=len(facts),
                latest_observed=latest,
                tiers=tiers,
                lat=geo["lat"] if geo else None,
                lon=geo["lon"] if geo else None,
                county=(facts[0]["loc"] if facts[0]["loc"] else None),
                score=round(score, 2),
                state=state,
                fact_ids=[f["id"] for f in facts],
                samples=[
                    {"content": f["content"], "tier": f["tier"], "observed_at": f["observed_at"]}
                    for f in facts[:4]
                ],
            )
        )
    candidates.sort(
        key=lambda c: (c.source_count, c.fact_count, c.score, c.latest_observed), reverse=True
    )
    return candidates


def promote(conn: sqlite3.Connection, cluster_key: str) -> dict[str, Any]:
    """Promote a watch candidate to a real incident and attribute its signals."""
    candidate = next((c for c in build_watchlist(conn) if c.key == cluster_key), None)
    if candidate is None:
        return {"promoted": False, "reason": "candidate not found"}

    now = utcnow()
    incident_id = ulid()
    conn.execute(
        """INSERT INTO incidents (id, canonical_name, status, lat, lon, county,
                                  created_at, updated_at)
           VALUES (?, ?, 'candidate', ?, ?, ?, ?, ?)""",
        (incident_id, candidate.name, candidate.lat, candidate.lon, candidate.county, now, now),
    )
    conn.execute(
        """INSERT INTO incident_events (id, incident_id, event, detail, actor, at)
           VALUES (?, ?, 'created', ?, 'operator', ?)""",
        (ulid(), incident_id, json.dumps({"via": "watch_promote"}), now),
    )
    # attribute the cluster's facts (the one NULL→value update the trigger allows)
    for fact_id in candidate.fact_ids:
        conn.execute(
            "UPDATE facts SET incident_id = ? WHERE id = ? AND incident_id IS NULL",
            (incident_id, fact_id),
        )
    first = conn.execute(
        "SELECT id FROM facts WHERE incident_id = ? ORDER BY observed_at ASC LIMIT 1",
        (incident_id,),
    ).fetchone()
    if first:
        conn.execute(
            "UPDATE incidents SET first_detected_fact_id = ? WHERE id = ?",
            (first["id"], incident_id),
        )
    return {
        "promoted": True,
        "incident_id": incident_id,
        "name": candidate.name,
        "facts_attributed": len(candidate.fact_ids),
    }
