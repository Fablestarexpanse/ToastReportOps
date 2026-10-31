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
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from toast.core.db import utcnow
from toast.core.ids import ulid
from toast.pipeline.attribution import (
    _norm_name,
    county_conflicts,
    name_similarity,
    stated_counties,
)

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
# Board sections, in display order. 'new' is the only one that means "go look": the
# others are explained rather than hidden, because a wrong sort must stay visible.
GROUP_ORDER = ("new", "official", "over", "prescribed", "mentions")
PRESCRIBED_RE = re.compile(r"prescribed|underburn|fuels? treatment", re.IGNORECASE)
# An undated fact is only as new as its article: observed_at is a stand-in for those.
EFFECTIVE_AT = """COALESCE(
    CASE WHEN json_extract(f.structured, '$.observed_at_estimated')
         THEN json_extract(e.payload, '$.published_at') END,
    f.observed_at)"""


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
    verdict: dict[str, Any] | None = None  # LLM age check, once one has been run
    # 'reported' when a signal carried coordinates; 'named_feature' when the
    # location was inferred from the fire's name. The UI must distinguish them:
    # an inferred pin is an approximation, not an observation.
    geo_source: str | None = None
    geo_place: str | None = None
    official: dict[str, Any] = field(default_factory=dict)  # official_lookup.check()
    group: str = "new"  # one of GROUP_ORDER


def _group(official: dict[str, Any], facts: list[sqlite3.Row], name: str) -> str:
    if official["status"] == "closed":
        return "over"
    text = " ".join([name, *(f["content"] or "" for f in facts)])
    if official["category"] == "RX" or PRESCRIBED_RE.search(text):
        return "prescribed"
    if official["status"] in ("active", "reported"):
        return "official"
    return "new" if any(f["kind"] == "start" for f in facts) else "mentions"


def _active_incident_names(conn: sqlite3.Connection) -> list[tuple[str, list[str], str | None]]:
    names = []
    for inc in conn.execute(
        "SELECT id, canonical_name, county FROM incidents "
        "WHERE status IN ('candidate','active','contained')"
    ):
        aliases = [
            r["alias"]
            for r in conn.execute(
                "SELECT alias FROM incident_aliases WHERE incident_id = ?", (inc["id"],)
            )
        ]
        names.append((inc["canonical_name"], aliases, inc["county"]))
    return names


def _matches_active_incident(
    name: str, active: list[tuple[str, list[str], str | None]], stated: set[str]
) -> bool:
    """The name is already tracked. Names repeat, so a tracked fire in a county other
    than the one the reports state is a different fire, and must not hide this one."""
    return any(
        name_similarity(name, canon, aliases) >= 0.85 and not county_conflicts(stated, county)
        for canon, aliases, county in active
    )


def build_watchlist(
    conn: sqlite3.Connection, *, include_triaged: bool = False
) -> list[WatchCandidate]:
    """Cluster unattributed named-fire signals into candidate new-starts.

    Candidates the operator has ignored or archived stay off the board;
    include_triaged=True is for lookups that need them anyway (e.g. re-running
    an age check on something already filed)."""
    from toast.pipeline import official_lookup
    from toast.pipeline.watch_triage import triage_map

    triaged = triage_map(conn)
    hidden = {k for k, v in triaged.items() if v["state"] in ("ignored", "archived")}
    active = _active_incident_names(conn)
    rows = conn.execute(
        f"""SELECT f.id, f.source_id, f.tier, f.content, f.kind, f.lat, f.lon,
                   {EFFECTIVE_AT} AS observed_at,
                   json_extract(f.structured, '$.incident_name') AS inc_name,
                   json_extract(f.structured, '$.location_text') AS loc
            FROM facts f LEFT JOIN envelopes e ON e.id = f.envelope_id
            WHERE f.incident_id IS NULL
              AND json_extract(f.structured, '$.incident_name') IS NOT NULL
              AND unixepoch('now') - unixepoch({EFFECTIVE_AT}) < ?
            ORDER BY observed_at DESC""",
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
        if not include_triaged and key in hidden:
            continue
        display = facts[0]["inc_name"]
        # skip clusters that actually name an existing incident (those are
        # attribution/adjudication cases, not new starts)
        stated = stated_counties(*(f["content"] for f in facts), *(f["loc"] for f in facts))
        if _matches_active_incident(display, active, stated):
            continue
        sources = {f["source_id"] for f in facts}
        geo = next((f for f in facts if f["lat"] is not None and f["lon"] is not None), None)
        county_hint = facts[0]["loc"] if facts[0]["loc"] else None
        # No signal carried coordinates — try the fire's own name. CA fires are
        # named after nearby features, so this can place a new start that no
        # official source has listed yet.
        named_place = None
        if geo is None:
            from toast.pipeline.geocode import resolve_fire_name

            named_place = resolve_fire_name(conn, display, county=county_hint)
        latest = max(f["observed_at"] for f in facts)
        tiers = sorted({f["tier"] for f in facts})
        # score: distinct sources dominate (real cross-checking), signal volume
        # and best tier add smaller lift so a well-reported single-source fire
        # still surfaces above one-off mentions.
        best_tier_bonus = (6 - min(tiers)) * 0.04
        volume_bonus = 0.04 * min(len(facts), 5)
        score = min(1.0, 0.2 + 0.28 * len(sources) + volume_bonus + best_tier_bonus)
        state = "corroborating" if len(sources) >= CORROBORATING_SOURCES else "watching"
        county = county_hint or (named_place.county if named_place else None)
        official = official_lookup.check(conn, display, county)
        candidates.append(
            WatchCandidate(
                key=key,
                name=display,
                source_count=len(sources),
                fact_count=len(facts),
                latest_observed=latest,
                tiers=tiers,
                lat=geo["lat"] if geo else (named_place.lat if named_place else None),
                lon=geo["lon"] if geo else (named_place.lon if named_place else None),
                county=county,
                score=round(score, 2),
                state=state,
                fact_ids=[f["id"] for f in facts],
                samples=[
                    {"content": f["content"], "tier": f["tier"], "observed_at": f["observed_at"]}
                    for f in facts[:4]
                ],
                verdict=(triaged.get(key) or {}).get("verdict"),
                geo_source=("reported" if geo else ("named_feature" if named_place else None)),
                geo_place=(named_place.name if named_place and not geo else None),
                official=official,
                group=_group(official, facts, display),
            )
        )
    # Section first, then independent sources, then newest. Counting facts or score
    # here let a long story about an old fire outrank a fire reported an hour ago.
    candidates.sort(key=lambda c: c.latest_observed, reverse=True)
    candidates.sort(key=lambda c: (GROUP_ORDER.index(c.group), -c.source_count))
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
