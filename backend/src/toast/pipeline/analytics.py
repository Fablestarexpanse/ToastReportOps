"""ANALYTICS — what the ledger says about itself.

Every number here is computed from stored rows, not estimated. Where a metric
would be misleading without context (a corroboration rate over a handful of
facts, say) the sample size is returned alongside it so the UI can say so.
"""

from __future__ import annotations

import sqlite3
from typing import Any


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def overview(conn: sqlite3.Connection, *, days: int = 14) -> dict[str, Any]:
    since = f"-{days} days"

    totals = {
        "facts": _scalar(conn, "SELECT COUNT(*) FROM facts"),
        "incidents": _scalar(conn, "SELECT COUNT(*) FROM incidents"),
        "active_incidents": _scalar(conn, "SELECT COUNT(*) FROM incidents WHERE status = 'active'"),
        "sources_enabled": _scalar(conn, "SELECT COUNT(*) FROM sources WHERE enabled = 1"),
        "sources_total": _scalar(conn, "SELECT COUNT(*) FROM sources"),
        "drafts": _scalar(conn, "SELECT COUNT(*) FROM drafts"),
        "released": _scalar(conn, "SELECT COUNT(*) FROM drafts WHERE status = 'published'"),
        "acres_tracked": _scalar(
            conn,
            """SELECT COALESCE(SUM(a), 0) FROM (
                 SELECT (SELECT acreage FROM facts v
                         WHERE v.incident_id = i.id AND v.kind = 'acreage'
                           AND v.id NOT IN (SELECT supersedes FROM facts
                                            WHERE supersedes IS NOT NULL)
                         ORDER BY (v.observed_at > strftime('%Y-%m-%dT%H:%M:%fZ','now')) ASC,
                           (v.tier >= 3) ASC, v.observed_at DESC LIMIT 1) AS a
                 FROM incidents i WHERE i.status = 'active')""",
        ),
    }

    # attribution: the share of facts that found their fire
    attributed = _scalar(conn, "SELECT COUNT(*) FROM facts WHERE incident_id IS NOT NULL")
    unattributed = _scalar(
        conn,
        """SELECT COUNT(*) FROM facts
           WHERE incident_id IS NULL AND kind != 'weather'
             AND id NOT IN (SELECT fact_id FROM fact_triage)""",
    )
    triaged = _scalar(conn, "SELECT COUNT(*) FROM fact_triage")

    assessed = _scalar(conn, "SELECT COUNT(*) FROM fact_assessments") or 0
    corroborated = (
        _scalar(conn, "SELECT COUNT(*) FROM fact_assessments WHERE corroborating_sources > 0") or 0
    )

    quality = {
        "attributed": attributed,
        "unattributed_open": unattributed,
        "triaged": triaged,
        "attribution_rate": (
            round(attributed / (attributed + unattributed), 3)
            if (attributed + unattributed)
            else None
        ),
        "assessed": assessed,
        "corroborated": corroborated,
        # sample size travels with the rate — a percentage over 12 facts is not
        # the same claim as one over 12,000
        "corroboration_rate": round(corroborated / assessed, 3) if assessed else None,
        "avg_confidence": _scalar(
            conn, "SELECT ROUND(AVG(effective_confidence), 3) FROM fact_assessments"
        ),
        "superseded": _scalar(conn, "SELECT COUNT(*) FROM facts WHERE supersedes IS NOT NULL"),
    }

    daily = [
        dict(r)
        for r in conn.execute(
            """SELECT substr(ingested_at, 1, 10) AS day, COUNT(*) AS facts,
                      COUNT(DISTINCT source_id) AS sources
               FROM facts WHERE ingested_at >= datetime('now', ?)
               GROUP BY day ORDER BY day""",
            (since,),
        )
    ]

    by_kind = [
        dict(r)
        for r in conn.execute("SELECT kind, COUNT(*) AS n FROM facts GROUP BY kind ORDER BY n DESC")
    ]

    by_tier = [
        dict(r)
        for r in conn.execute("SELECT tier, COUNT(*) AS n FROM facts GROUP BY tier ORDER BY tier")
    ]

    by_county = [
        dict(r)
        for r in conn.execute(
            """SELECT COALESCE(county, 'unknown') AS county, COUNT(*) AS incidents,
                      SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active
               FROM incidents GROUP BY county ORDER BY incidents DESC LIMIT 12"""
        )
    ]

    top_sources = [
        dict(r)
        for r in conn.execute(
            """SELECT s.name, s.tier, COUNT(f.id) AS facts,
                      st.lead_time_median_s, st.superseded_rate
               FROM sources s
               LEFT JOIN facts f ON f.source_id = s.id
               LEFT JOIN source_stats st ON st.source_id = s.id
               GROUP BY s.id HAVING facts > 0
               ORDER BY facts DESC LIMIT 10"""
        )
    ]

    return {
        "window_days": days,
        "totals": totals,
        "quality": quality,
        "daily": daily,
        "by_kind": by_kind,
        "by_tier": by_tier,
        "by_county": by_county,
        "top_sources": top_sources,
    }
