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
        "season": season(conn),
        "records": records(conn),
        "detection": detection_board(conn),
        "growth_curves": growth_curves(conn),
        "containment_race": containment_race(conn),
        "newsroom": newsroom_stats(conn, days),
        "radio": radio_stats(conn, days),
        "dedup": dedup_stats(conn, days),
        "aircraft": aircraft_stats(conn, days),
    }


# ------------------------------------------------------------------ the fun half


def season(conn: sqlite3.Connection) -> dict[str, Any]:
    """The year so far, and fire starts per week — the season's shape."""
    year = _scalar(conn, "SELECT strftime('%Y', 'now')")
    fires_ytd = _scalar(
        conn,
        "SELECT COUNT(*) FROM incidents WHERE substr(created_at, 1, 4) = ?"
        " AND COALESCE(incident_type, 'WF') != 'RX'",
        (year,),
    )
    acres_ytd = _scalar(
        conn,
        """SELECT COALESCE(SUM(a), 0) FROM (
             SELECT (SELECT MAX(acreage) FROM facts v
                     WHERE v.incident_id = i.id AND v.kind = 'acreage') AS a
             FROM incidents i WHERE substr(i.created_at, 1, 4) = ?
               AND COALESCE(i.incident_type, 'WF') != 'RX')""",
        (year,),
    )
    weekly = [
        dict(r)
        for r in conn.execute(
            """SELECT strftime('%Y-W%W', created_at) AS week, COUNT(*) AS fires
               FROM incidents
               WHERE created_at >= datetime('now', '-84 days')
                 AND COALESCE(incident_type, 'WF') != 'RX'
               GROUP BY week ORDER BY week"""
        )
    ]
    return {"year": year, "fires_ytd": fires_ytd, "acres_ytd": acres_ytd, "weekly_starts": weekly}


def records(conn: sqlite3.Connection) -> dict[str, Any]:
    """The record board — biggest, fastest, longest, busiest. Every record is a
    real row, never an estimate; each carries its holder's name."""
    biggest = conn.execute(
        """SELECT i.canonical_name AS name, MAX(f.acreage) AS acres
           FROM facts f JOIN incidents i ON i.id = f.incident_id
           WHERE f.kind = 'acreage' AND f.acreage IS NOT NULL"""
    ).fetchone()
    # fastest observed growth: the largest acres/hour between two consecutive
    # readings in one fire's supersession chain (>=30 min apart — shorter gaps
    # amplify reporting noise into fantasy speeds)
    fastest = conn.execute(
        """SELECT i.canonical_name AS name,
                  MAX((new.acreage - old.acreage) /
                      ((unixepoch(new.observed_at) - unixepoch(old.observed_at)) / 3600.0)
                  ) AS acres_per_hour
           FROM facts new
           JOIN facts old ON new.supersedes = old.id
           JOIN incidents i ON i.id = new.incident_id
           WHERE new.kind = 'acreage' AND new.acreage IS NOT NULL
             AND old.acreage IS NOT NULL AND new.acreage > old.acreage
             AND unixepoch(new.observed_at) - unixepoch(old.observed_at) >= 1800"""
    ).fetchone()
    longest = conn.execute(
        """SELECT canonical_name AS name,
                  CAST((unixepoch('now') - unixepoch(created_at)) / 86400 AS INT) AS days
           FROM incidents WHERE status = 'active'
             AND COALESCE(incident_type, 'WF') != 'RX'
           ORDER BY created_at ASC LIMIT 1"""
    ).fetchone()
    busiest = conn.execute(
        """SELECT substr(ingested_at, 1, 10) AS day, COUNT(*) AS facts
           FROM facts GROUP BY day ORDER BY facts DESC LIMIT 1"""
    ).fetchone()
    corro = conn.execute(
        """SELECT f.content, i.canonical_name AS name, a.corroborating_sources AS n
           FROM fact_assessments a
           JOIN facts f ON f.id = a.fact_id
           LEFT JOIN incidents i ON i.id = f.incident_id
           WHERE a.corroborating_sources > 0
           ORDER BY a.corroborating_sources DESC, f.ingested_at DESC LIMIT 1"""
    ).fetchone()
    return {
        "biggest_fire": dict(biggest) if biggest and biggest["acres"] else None,
        "fastest_growth": (dict(fastest) if fastest and fastest["acres_per_hour"] else None),
        "longest_burning": dict(longest) if longest else None,
        "busiest_day": dict(busiest) if busiest else None,
        "most_corroborated": dict(corro) if corro else None,
    }


def detection_board(conn: sqlite3.Connection) -> dict[str, Any]:
    """'We knew first' — per fire, how far our earliest knowledge ran ahead of
    the first official (tier<=2) record. Zero-lead fires (born from an official
    listing) are counted but not listed; the board is the beats."""
    rows = conn.execute(
        """SELECT i.id, i.canonical_name AS name,
                  MIN(f.ingested_at) AS first_known,
                  (SELECT MIN(o.ingested_at) FROM facts o
                    WHERE o.incident_id = i.id AND o.tier <= 2) AS first_official
           FROM incidents i JOIN facts f ON f.incident_id = i.id
           WHERE i.created_at >= datetime('now', '-90 days')
             AND COALESCE(i.incident_type, 'WF') != 'RX'
           GROUP BY i.id"""
    ).fetchall()
    beats = []
    measured = 0
    for r in rows:
        if not r["first_official"] or not r["first_known"]:
            continue
        measured += 1
        lead = int(
            conn.execute(
                "SELECT unixepoch(?) - unixepoch(?)", (r["first_official"], r["first_known"])
            ).fetchone()[0]
        )
        if lead > 60:  # a real beat, not clock jitter
            beats.append({"name": r["name"], "lead_s": lead})
    beats.sort(key=lambda b: -b["lead_s"])
    return {"measured": measured, "beats": beats[:8], "beat_count": len(beats)}


def growth_curves(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The five biggest active fires' acreage histories, for the overlay chart.
    Supersession IS the history — every point is a real reading."""
    tops = conn.execute(
        """SELECT i.id, i.canonical_name AS name,
                  (SELECT MAX(acreage) FROM facts v
                   WHERE v.incident_id = i.id AND v.kind = 'acreage') AS peak
           FROM incidents i WHERE i.status = 'active'
             AND COALESCE(i.incident_type, 'WF') != 'RX'
           ORDER BY peak DESC LIMIT 5"""
    ).fetchall()
    out = []
    for t in tops:
        if not t["peak"]:
            continue
        pts = [
            {"t": r["observed_at"], "acres": r["acreage"]}
            for r in conn.execute(
                """SELECT observed_at, acreage FROM facts
                   WHERE incident_id = ? AND kind = 'acreage' AND acreage IS NOT NULL
                   ORDER BY observed_at""",
                (t["id"],),
            )
        ]
        if len(pts) >= 2:
            out.append({"name": t["name"], "points": pts})
    return out


def containment_race(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every active fire's current containment — the race to 100."""
    return [
        dict(r)
        for r in conn.execute(
            """SELECT i.canonical_name AS name,
                 (SELECT v.containment_pct FROM facts v
                  WHERE v.incident_id = i.id AND v.kind = 'containment'
                    AND v.id NOT IN (SELECT supersedes FROM facts WHERE supersedes IS NOT NULL)
                  ORDER BY (v.observed_at > strftime('%Y-%m-%dT%H:%M:%fZ','now')) ASC,
                    (v.tier >= 3) ASC, v.observed_at DESC LIMIT 1) AS pct,
                 (SELECT MAX(v.acreage) FROM facts v
                  WHERE v.incident_id = i.id AND v.kind = 'acreage') AS acres
               FROM incidents i WHERE i.status = 'active'
                 AND COALESCE(i.incident_type, 'WF') != 'RX'
               ORDER BY acres DESC LIMIT 14"""
        )
        if r["pct"] is not None
    ]


def newsroom_stats(conn: sqlite3.Connection, days: int) -> dict[str, Any]:
    since = f"-{days} days"
    return {
        "drafted": _scalar(
            conn, "SELECT COUNT(*) FROM drafts WHERE created_at >= datetime('now', ?)", (since,)
        ),
        "published": _scalar(
            conn,
            "SELECT COUNT(*) FROM drafts WHERE status = 'published'"
            " AND updated_at >= datetime('now', ?)",
            (since,),
        ),
        "deliveries": _scalar(
            conn,
            "SELECT COUNT(*) FROM publications WHERE status = 'sent'"
            " AND sent_at >= datetime('now', ?)",
            (since,),
        ),
        # median hours from draft creation to approval — the operator's pace
        "median_hours_to_approve": _scalar(
            conn,
            """SELECT ROUND((unixepoch(approved_at) - unixepoch(created_at)) / 3600.0, 1)
               FROM drafts WHERE approved_at IS NOT NULL
               ORDER BY (unixepoch(approved_at) - unixepoch(created_at))
               LIMIT 1 OFFSET (SELECT COUNT(*) / 2 FROM drafts WHERE approved_at IS NOT NULL)""",
        ),
    }


def radio_stats(conn: sqlite3.Connection, days: int) -> dict[str, Any]:
    since = f"-{days} days"
    return {
        "calls": _scalar(
            conn,
            "SELECT COUNT(*) FROM radio_calls WHERE ingested_at >= datetime('now', ?)",
            (since,),
        ),
        "airtime_s": _scalar(
            conn,
            "SELECT COALESCE(ROUND(SUM(duration_s)), 0) FROM radio_calls"
            " WHERE ingested_at >= datetime('now', ?)",
            (since,),
        ),
        "transcribed": _scalar(
            conn,
            "SELECT COUNT(*) FROM radio_calls WHERE transcript_state = 'done'"
            " AND ingested_at >= datetime('now', ?)",
            (since,),
        ),
        "watch_hits": _scalar(
            conn,
            "SELECT COUNT(*) FROM radio_calls WHERE watch_fact_id IS NOT NULL"
            " AND ingested_at >= datetime('now', ?)",
            (since,),
        ),
    }


def dedup_stats(conn: sqlite3.Connection, days: int) -> dict[str, Any]:
    """How much noise the pipeline ate so the operator didn't have to."""
    since = f"-{days} days"
    envelopes = _scalar(
        conn, "SELECT COUNT(*) FROM envelopes WHERE fetched_at >= datetime('now', ?)", (since,)
    )
    facts = _scalar(
        conn, "SELECT COUNT(*) FROM facts WHERE ingested_at >= datetime('now', ?)", (since,)
    )
    duplicates = _scalar(
        conn,
        """SELECT COUNT(*) FROM fact_assessments a JOIN facts f ON f.id = a.fact_id
           WHERE a.duplicate_of IS NOT NULL AND f.ingested_at >= datetime('now', ?)""",
        (since,),
    )
    return {"envelopes": envelopes, "facts": facts, "near_duplicates_flagged": duplicates}


def aircraft_stats(conn: sqlite3.Connection, days: int) -> dict[str, Any]:
    since = f"-{days} days"
    try:
        sightings = _scalar(
            conn,
            "SELECT COUNT(*) FROM aircraft_positions WHERE seen_at >= datetime('now', ?)",
            (since,),
        )
        hulls = _scalar(
            conn,
            "SELECT COUNT(DISTINCT hex) FROM aircraft_positions"
            " WHERE seen_at >= datetime('now', ?)",
            (since,),
        )
        busiest = conn.execute(
            """SELECT COALESCE(flight, hex) AS ident, COUNT(*) AS n
               FROM aircraft_positions WHERE seen_at >= datetime('now', ?)
                 AND fire_ops = 1
               GROUP BY hex ORDER BY n DESC LIMIT 1""",
            (since,),
        ).fetchone()
    except sqlite3.OperationalError:  # column drift on old DBs — degrade honest
        return {"sightings": 0, "hulls": 0, "busiest": None}
    return {
        "sightings": sightings,
        "hulls": hulls,
        "busiest": dict(busiest) if busiest and busiest["n"] else None,
    }
