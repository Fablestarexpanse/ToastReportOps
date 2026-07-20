"""Incident intelligence — the OPS right-panel aggregations.

Everything here is a read over the append-only ledger. Deltas come straight
from supersession chains (prior value -> current value is what supersession
stores); the source-confidence meter aggregates fact_assessments — the stat
no other tracker has, because nobody else runs a corroboration model.
"""

from __future__ import annotations

import sqlite3
from typing import Any

# metric kinds shown in the header grid; value column per kind
_METRICS = {"acreage": "acreage", "containment": "containment_pct"}

# kinds rendered as CURRENT SITUATION bullets (latest non-superseded each)
# Kinds rendered as CURRENT SITUATION bullets — deliberately excluding kinds
# that already have their own block in the panel. 'resource' has the RESOURCES
# DEPLOYED tiles and 'cause' has a header tile; repeating them as bullets makes
# one fact look like two independent reports.
_SITUATION_KINDS = ("damage", "weather", "other")

_NOT_SUPERSEDED = "id NOT IN (SELECT supersedes FROM facts WHERE supersedes IS NOT NULL)"


OFFICIAL_MAX_TIER = 2  # T1-T2 = agency record; T3+ = press, social, scraped
# Relative gap at which official and reported disagree enough to say so.
DIVERGENCE_PCT = 0.15


def _latest(
    conn: sqlite3.Connection,
    incident_id: str,
    kind: str,
    *,
    official: bool | None = None,
) -> sqlite3.Row | None:
    """Latest non-superseded fact of a kind. `official` selects the tier band."""
    clause = ""
    if official is True:
        clause = f" AND f.tier <= {OFFICIAL_MAX_TIER}"
    elif official is False:
        clause = f" AND f.tier > {OFFICIAL_MAX_TIER}"
    return conn.execute(
        f"""SELECT f.*, s.name AS source_name FROM facts f
            JOIN sources s ON s.id = f.source_id
            WHERE f.incident_id = ? AND f.kind = ? AND f.{_NOT_SUPERSEDED}{clause}
            ORDER BY f.observed_at DESC LIMIT 1""",
        (incident_id, kind),
    ).fetchone()


def _reading(conn: sqlite3.Connection, row: sqlite3.Row | None, col: str) -> dict[str, Any] | None:
    """One side of the official/reported comparison, with its own delta."""
    if row is None:
        return None
    prev_value = None
    if row["supersedes"]:
        prev = conn.execute(
            f"SELECT {col} AS v FROM facts WHERE id = ?", (row["supersedes"],)
        ).fetchone()
        prev_value = prev["v"] if prev else None
    value = row[col]
    return {
        "value": value,
        "prev": prev_value,
        "delta": (
            round(value - prev_value, 1) if value is not None and prev_value is not None else None
        ),
        "observed_at": row["observed_at"],
        "source_name": row["source_name"],
        "tier": row["tier"],
    }


def _metric(conn: sqlite3.Connection, incident_id: str, kind: str) -> dict[str, Any]:
    """A metric as the agency reports it AND as the press reports it.

    They are kept apart on purpose. The official record is authoritative but
    lags; scraped reporting is faster but noisier. Averaging them would hide
    both facts, and the DISAGREEMENT is itself information — an early scoop or
    an error, either of which the operator needs to see rather than a blend.
    """
    col = _METRICS[kind]
    official = _reading(conn, _latest(conn, incident_id, kind, official=True), col)
    reported = _reading(conn, _latest(conn, incident_id, kind, official=False), col)

    divergence = None
    if official and reported and official["value"] and reported["value"]:
        gap = abs(float(official["value"]) - float(reported["value"]))
        base = max(float(official["value"]), 1.0)
        pct = gap / base
        if pct >= DIVERGENCE_PCT:
            divergence = {
                "gap": round(gap, 1),
                "pct": round(pct, 3),
                "reported_higher": float(reported["value"]) > float(official["value"]),
            }

    # The headline number is the official one whenever we have it: a tile
    # without a visible source should never be showing press figures.
    primary = official or reported or {}
    return {
        **{"value": None, "prev": None, "delta": None, "observed_at": None},
        **primary,
        "official": official,
        "reported": reported,
        "divergence": divergence,
    }


def incident_intel(conn: sqlite3.Connection, incident_id: str) -> dict[str, Any]:
    """One payload for the whole intelligence panel."""
    metrics: dict[str, Any] = {k: _metric(conn, incident_id, k) for k in _METRICS}
    for kind in ("start", "cause"):
        row = _latest(conn, incident_id, kind)
        metrics[kind] = (
            {
                "content": row["content"],
                "observed_at": row["observed_at"],
                "source_name": row["source_name"],
                "tier": row["tier"],
            }
            if row
            else None
        )

    conf = conn.execute(
        """SELECT ROUND(AVG(a.effective_confidence), 3) AS avg_confidence,
                  COUNT(DISTINCT f.source_id) AS source_count,
                  COUNT(DISTINCT CASE WHEN a.corroborating_sources > 0
                        THEN f.id END) AS corroborated_facts,
                  COUNT(f.id) AS fact_count
           FROM facts f LEFT JOIN fact_assessments a ON a.fact_id = f.id
           WHERE f.incident_id = ?""",
        (incident_id,),
    ).fetchone()

    situation = []
    for kind in _SITUATION_KINDS:
        row = _latest(conn, incident_id, kind)
        if row:
            situation.append(
                {
                    "kind": kind,
                    "content": row["content"],
                    "observed_at": row["observed_at"],
                    "source_name": row["source_name"],
                    "tier": row["tier"],
                    "confidence": row["confidence"],
                }
            )

    impacts = {}
    for kind in ("evac", "road"):
        rows = conn.execute(
            f"""SELECT f.id, f.content, f.evac_status, f.observed_at, f.tier,
                       f.structured, s.name AS source_name,
                       -- The mission rule is verbatim text PLUS the official
                       -- deep-link PLUS the notice. The link was never plumbed,
                       -- so an operator could not click through to the county's
                       -- own page to confirm an order before publishing it.
                       (SELECT COALESCE(json_extract(e.payload, '$.url'),
                                        json_extract(e.payload, '$.link'))
                          FROM envelopes e WHERE e.id = f.envelope_id) AS source_url
                FROM facts f JOIN sources s ON s.id = f.source_id
                WHERE f.incident_id = ? AND f.kind = ? AND f.{_NOT_SUPERSEDED}
                ORDER BY f.observed_at DESC""",
            (incident_id, kind),
        ).fetchall()
        # Whether an evacuation instruction came from the agency or from a
        # newsroom paraphrase is the single most important thing about it.
        impacts[kind] = [{**dict(r), "official": r["tier"] <= OFFICIAL_MAX_TIER} for r in rows]

    # Orders and warnings mean very different things to someone deciding
    # whether to leave, so they are never collapsed into one number.
    impact_counts = {
        "orders": sum(1 for f in impacts["evac"] if f["evac_status"] == "order"),
        "warnings": sum(1 for f in impacts["evac"] if f["evac_status"] == "warning"),
        "shelter_in_place": sum(
            1 for f in impacts["evac"] if f["evac_status"] == "shelter_in_place"
        ),
        "lifted": sum(1 for f in impacts["evac"] if f["evac_status"] == "lifted"),
        "unclassified": sum(1 for f in impacts["evac"] if not f["evac_status"]),
        "road_closures": len(impacts["road"]),
    }

    from toast.pipeline.resources import current_resources

    return {
        "metrics": metrics,
        "confidence": dict(conf) if conf else {},
        "resources": current_resources(conn, incident_id),
        "situation": situation,
        "evac": impacts["evac"],
        "road": impacts["road"],
        "impact_counts": impact_counts,
    }


def incident_timeline(
    conn: sqlite3.Connection, incident_id: str, limit: int = 80
) -> list[dict[str, Any]]:
    """Lifecycle events + the fact stream, merged chronologically (newest
    first). The full history of what we knew and when we knew it."""
    events = [
        {
            "at": r["at"],
            "type": "event",
            "label": r["event"].replace("_", " ").upper(),
            "detail": r["detail"],
            "actor": r["actor"],
        }
        for r in conn.execute(
            "SELECT event, detail, actor, at FROM incident_events WHERE incident_id = ?",
            (incident_id,),
        ).fetchall()
    ]
    facts = [
        {
            "at": r["observed_at"],
            "type": "fact",
            "label": r["kind"].upper(),
            "content": r["content"],
            "tier": r["tier"],
            "source_name": r["source_name"],
            "superseded": bool(r["superseded_by"]),
        }
        for r in conn.execute(
            """SELECT f.kind, f.content, f.tier, f.observed_at,
                      (SELECT id FROM facts x WHERE x.supersedes = f.id) AS superseded_by,
                      s.name AS source_name
               FROM facts f JOIN sources s ON s.id = f.source_id
               WHERE f.incident_id = ?""",
            (incident_id,),
        ).fetchall()
    ]
    merged = sorted(events + facts, key=lambda e: e["at"], reverse=True)
    return merged[:limit]
