"""Attribution scorer — match unattributed facts to incidents.

Signals (each 0-1, weighted):
- name: fuzzy match of claimed incident name vs canonical name + aliases
- geo: haversine distance between fact and incident coordinates
- time: fact observed_at vs incident recency (stale incidents score low)

Score >= AUTO_THRESHOLD  → auto-attribute (facts.incident_id NULL→value).
Score >= REVIEW_THRESHOLD → adjudication queue with candidates listed.
Below → stays unattributed (statewide noise).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from difflib import SequenceMatcher

from toast.pipeline.incidents import haversine_km

AUTO_THRESHOLD = 0.75
REVIEW_THRESHOLD = 0.35
GEO_FULL_KM = 5.0  # within this distance geo signal is 1.0
GEO_ZERO_KM = 60.0  # beyond this it is 0
TIME_ZERO_S = 14 * 86400  # incidents idle this long score 0 on time

WEIGHTS = {"name": 0.5, "geo": 0.35, "time": 0.15}


def _norm_name(name: str) -> str:
    name = name.lower()
    name = re.sub(r"\b(fire|incident|complex|rx|burn)\b", " ", name)
    return re.sub(r"[^a-z0-9]+", " ", name).strip()


def name_similarity(claimed: str | None, canonical: str, aliases: list[str]) -> float:
    if not claimed:
        return 0.0
    a = _norm_name(claimed)
    if not a:
        return 0.0
    best = 0.0
    for candidate in [canonical, *aliases]:
        b = _norm_name(candidate)
        if not b:
            continue
        if a == b:
            return 1.0
        best = max(best, SequenceMatcher(None, a, b).ratio())
    return best


def geo_score(
    fact_lat: float | None, fact_lon: float | None, inc_lat: float | None, inc_lon: float | None
) -> float:
    if fact_lat is None or fact_lon is None or inc_lat is None or inc_lon is None:
        return 0.0
    d = haversine_km(fact_lat, fact_lon, inc_lat, inc_lon)
    if d <= GEO_FULL_KM:
        return 1.0
    if d >= GEO_ZERO_KM:
        return 0.0
    return 1.0 - (d - GEO_FULL_KM) / (GEO_ZERO_KM - GEO_FULL_KM)


@dataclass
class Candidate:
    incident_id: str
    incident_name: str
    score: float
    name_score: float
    geo_score: float
    time_score: float


def score_candidates(
    conn: sqlite3.Connection,
    *,
    observed_at: str,
    incident_name: str | None,
    lat: float | None,
    lon: float | None,
    limit: int = 5,
) -> list[Candidate]:
    out: list[Candidate] = []
    incidents = conn.execute(
        """SELECT i.id, i.canonical_name, i.lat, i.lon,
                  COALESCE((SELECT MAX(observed_at) FROM facts WHERE incident_id = i.id),
                           i.updated_at) AS last_activity
           FROM incidents i WHERE i.status IN ('candidate', 'active', 'contained')"""
    ).fetchall()
    # Geo is unavailable for most media/news facts (extracted claims are
    # coordless). When the fact has no coordinates, score over the available
    # signals (name + time) and renormalize — otherwise a coordless fact could
    # never reach the auto-attribute threshold even on a perfect name match.
    geo_available = lat is not None and lon is not None
    for inc in incidents:
        aliases = [
            r["alias"]
            for r in conn.execute(
                "SELECT alias FROM incident_aliases WHERE incident_id = ?", (inc["id"],)
            )
        ]
        n = name_similarity(incident_name, inc["canonical_name"], aliases)
        # unixepoch() is NULL for an unparseable stamp. Rows like that can no
        # longer be written, but one already in the ledger must not take the
        # whole adjudication queue down with a TypeError — the route scores
        # every queued fact, so one bad date used to 500 all of them. Unknown
        # time scores zero: no time evidence, not a free match.
        delta = conn.execute(
            "SELECT unixepoch(?) - unixepoch(?)", (observed_at, inc["last_activity"])
        ).fetchone()[0]
        t = 0.0 if delta is None else max(0.0, 1.0 - abs(delta) / TIME_ZERO_S)
        if geo_available:
            g = geo_score(lat, lon, inc["lat"], inc["lon"])
            score = WEIGHTS["name"] * n + WEIGHTS["geo"] * g + WEIGHTS["time"] * t
        else:
            g = 0.0
            denom = WEIGHTS["name"] + WEIGHTS["time"]
            score = (WEIGHTS["name"] * n + WEIGHTS["time"] * t) / denom
        if score > 0.05:
            out.append(Candidate(inc["id"], inc["canonical_name"], score, n, g, t))
    out.sort(key=lambda c: c.score, reverse=True)
    return out[:limit]


def attribute_fact(conn: sqlite3.Connection, fact_id: str) -> tuple[str, list[Candidate]]:
    """Try to attribute one unattributed fact.

    Returns (outcome, candidates) where outcome is 'attributed' | 'review' | 'none'.
    """
    fact = conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
    if fact is None or fact["incident_id"] is not None:
        return "none", []
    import json

    structured = json.loads(fact["structured"] or "{}")
    candidates = score_candidates(
        conn,
        observed_at=fact["observed_at"],
        incident_name=structured.get("incident_name"),
        lat=fact["lat"],
        lon=fact["lon"],
    )
    if not candidates:
        return "none", []
    best = candidates[0]
    runner_up = candidates[1].score if len(candidates) > 1 else 0.0
    # auto-attribute only when clearly best (no close second guessing)
    if best.score >= AUTO_THRESHOLD and best.score - runner_up >= 0.15:
        conn.execute("UPDATE facts SET incident_id = ? WHERE id = ?", (best.incident_id, fact_id))
        return "attributed", candidates
    if best.score >= REVIEW_THRESHOLD:
        return "review", candidates
    return "none", candidates
