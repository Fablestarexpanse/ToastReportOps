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
_SITUATION_KINDS = ("damage", "resource", "weather", "cause", "other")

_NOT_SUPERSEDED = "id NOT IN (SELECT supersedes FROM facts WHERE supersedes IS NOT NULL)"


def _latest(conn: sqlite3.Connection, incident_id: str, kind: str) -> sqlite3.Row | None:
    return conn.execute(
        f"""SELECT f.*, s.name AS source_name FROM facts f
            JOIN sources s ON s.id = f.source_id
            WHERE f.incident_id = ? AND f.kind = ? AND f.{_NOT_SUPERSEDED}
            ORDER BY f.observed_at DESC LIMIT 1""",
        (incident_id, kind),
    ).fetchone()


def _metric(conn: sqlite3.Connection, incident_id: str, kind: str) -> dict[str, Any]:
    col = _METRICS[kind]
    row = _latest(conn, incident_id, kind)
    if row is None:
        return {"value": None, "prev": None, "delta": None, "observed_at": None}
    prev_value = None
    if row["supersedes"]:
        prev = conn.execute(
            f"SELECT {col} AS v FROM facts WHERE id = ?", (row["supersedes"],)
        ).fetchone()
        prev_value = prev["v"] if prev else None
    value = row[col]
    delta = round(value - prev_value, 1) if value is not None and prev_value is not None else None
    return {
        "value": value,
        "prev": prev_value,
        "delta": delta,
        "observed_at": row["observed_at"],
        "source_name": row["source_name"],
        "tier": row["tier"],
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
                       f.structured, s.name AS source_name
                FROM facts f JOIN sources s ON s.id = f.source_id
                WHERE f.incident_id = ? AND f.kind = ? AND f.{_NOT_SUPERSEDED}
                ORDER BY f.observed_at DESC""",
            (incident_id, kind),
        ).fetchall()
        impacts[kind] = [dict(r) for r in rows]

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

    return {
        "metrics": metrics,
        "confidence": dict(conf) if conf else {},
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
