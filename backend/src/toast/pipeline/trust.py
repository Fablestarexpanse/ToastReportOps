"""Trust loop — sources earn their tier from measured performance.

Tiers started as an operator's prior. This module turns that prior into
evidence: how much a source yields, how often it gets corrected, and how far
ahead of the official record it runs. The result is a *suggestion* — the
operator approves every tier change, because trust is a judgment call and a
promotion changes how much weight future facts carry.

History is never rewritten: `facts.tier` is denormalized at write time, so a
promotion today cannot retroactively make yesterday's claims look stronger.
"""

from __future__ import annotations

import sqlite3
import statistics
from typing import Any

from toast.core.db import utcnow

WINDOW_DAYS = 30
OFFICIAL_TIER = 2  # T1-T2 are the official record we measure lead time against

# Conservative gates — a tier is a standing claim about reliability, so
# promotion needs real evidence, while a correction problem demotes quickly.
MIN_FACTS_TO_JUDGE = 8
PROMOTE_MAX_CORRECTION_RATE = 0.10
PROMOTE_MIN_LEAD_S = 900  # consistently 15+ min ahead of official
DEMOTE_CORRECTION_RATE = 0.35


def _fact_stats(conn: sqlite3.Connection, window_days: int) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """SELECT s.id AS source_id, s.tier,
                  COUNT(f.id) AS fact_count,
                  SUM(CASE WHEN EXISTS (
                        SELECT 1 FROM facts sup
                        WHERE sup.supersedes = f.id AND sup.tier < f.tier)
                      THEN 1 ELSE 0 END) AS corrected_by_higher,
                  MIN(f.observed_at) AS first_at,
                  MAX(f.observed_at) AS last_at
           FROM sources s
           LEFT JOIN facts f ON f.source_id = s.id
                AND f.observed_at >= datetime('now', ?)
           GROUP BY s.id""",
        (f"-{window_days} days",),
    ).fetchall()
    return {
        r["source_id"]: {
            "tier": r["tier"],
            "fact_count": r["fact_count"] or 0,
            "corrected_by_higher": r["corrected_by_higher"] or 0,
            "first_at": r["first_at"],
            "last_at": r["last_at"],
        }
        for r in rows
    }


MAX_CREDIBLE_LEAD_S = 3 * 24 * 3600  # beyond this it's a dating artifact, not a scoop


def _lead_times(conn: sqlite3.Connection, window_days: int) -> dict[str, list[float]]:
    """Seconds this source got information into our ledger ahead of the
    official record, per incident+kind.

    Measured on `ingested_at` — when we actually learned it — not `observed_at`.
    An article published today can describe an event from last week, so an
    observed_at comparison rewards back-dating rather than genuine speed. We
    additionally require the source's claim to describe an event no later than
    the official one, and discard implausibly large leads as dating artifacts.
    Only the source's earliest claim per (incident, kind) counts, so a chatty
    feed can't inflate its lead by repeating itself."""
    rows = conn.execute(
        """WITH official AS (
             SELECT incident_id, kind,
                    MIN(ingested_at) AS official_in,
                    MIN(observed_at) AS official_obs
             FROM facts
             WHERE tier <= ? AND incident_id IS NOT NULL
               AND observed_at >= datetime('now', ?)
             GROUP BY incident_id, kind
           ),
           earliest AS (
             SELECT source_id, incident_id, kind,
                    MIN(ingested_at) AS first_in,
                    MIN(observed_at) AS first_obs
             FROM facts
             WHERE incident_id IS NOT NULL AND tier > ?
               AND observed_at >= datetime('now', ?)
             GROUP BY source_id, incident_id, kind
           )
           SELECT e.source_id,
                  (unixepoch(o.official_in) - unixepoch(e.first_in)) AS lead_s
           FROM earliest e
           JOIN official o
             ON o.incident_id = e.incident_id AND o.kind = e.kind
           WHERE e.first_obs <= o.official_obs""",
        (OFFICIAL_TIER, f"-{window_days} days", OFFICIAL_TIER, f"-{window_days} days"),
    ).fetchall()
    out: dict[str, list[float]] = {}
    for r in rows:
        lead = float(r["lead_s"])
        if abs(lead) <= MAX_CREDIBLE_LEAD_S:
            out.setdefault(r["source_id"], []).append(lead)
    return out


def _suggest_tier(
    *, tier: int, fact_count: int, correction_rate: float, lead_median_s: float | None
) -> int | None:
    """Suggested tier, or None to leave it alone. Never suggests T1 — the
    official-source tier is an editorial designation, not an earned one."""
    if fact_count < MIN_FACTS_TO_JUDGE:
        return None
    if correction_rate >= DEMOTE_CORRECTION_RATE and tier < 5:
        return tier + 1  # gets corrected too often — trust it less
    if (
        tier > 2
        and correction_rate <= PROMOTE_MAX_CORRECTION_RATE
        and lead_median_s is not None
        and lead_median_s >= PROMOTE_MIN_LEAD_S
    ):
        return tier - 1  # fast and rarely wrong — trust it more
    return None


def compute_source_stats(
    conn: sqlite3.Connection, *, window_days: int = WINDOW_DAYS
) -> list[dict[str, Any]]:
    """Recompute rolling per-source performance. Idempotent; safe to re-run."""
    facts = _fact_stats(conn, window_days)
    leads = _lead_times(conn, window_days)
    now = utcnow()
    results = []

    for source_id, st in facts.items():
        count = st["fact_count"]
        correction_rate = (st["corrected_by_higher"] / count) if count else 0.0
        source_leads = leads.get(source_id, [])
        lead_median = statistics.median(source_leads) if source_leads else None
        yield_per_day = count / window_days

        suggested = _suggest_tier(
            tier=st["tier"],
            fact_count=count,
            correction_rate=correction_rate,
            lead_median_s=lead_median,
        )
        conn.execute(
            """INSERT INTO source_stats (source_id, window_days, lead_time_median_s,
                                         superseded_rate, yield_facts_per_day,
                                         computed_at, suggested_tier)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source_id, window_days) DO UPDATE SET
                 lead_time_median_s = excluded.lead_time_median_s,
                 superseded_rate = excluded.superseded_rate,
                 yield_facts_per_day = excluded.yield_facts_per_day,
                 computed_at = excluded.computed_at,
                 suggested_tier = excluded.suggested_tier""",
            (
                source_id,
                window_days,
                int(lead_median) if lead_median is not None else None,
                round(correction_rate, 4),
                round(yield_per_day, 3),
                now,
                suggested,
            ),
        )
        results.append(
            {
                "source_id": source_id,
                "tier": st["tier"],
                "fact_count": count,
                "correction_rate": round(correction_rate, 4),
                "lead_median_s": lead_median,
                "yield_per_day": round(yield_per_day, 3),
                "suggested_tier": suggested,
            }
        )
    return results


def apply_tier(conn: sqlite3.Connection, source_id: str, tier: int) -> dict[str, Any]:
    """Operator-approved tier change. Only affects facts written from now on."""
    if not 1 <= tier <= 5:
        raise ValueError("tier must be 1-5")
    row = conn.execute("SELECT tier, name FROM sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        raise ValueError("source not found")
    conn.execute(
        "UPDATE sources SET tier = ?, updated_at = ? WHERE id = ?", (tier, utcnow(), source_id)
    )
    # clear the suggestion we just acted on
    conn.execute("UPDATE source_stats SET suggested_tier = NULL WHERE source_id = ?", (source_id,))
    return {"source_id": source_id, "name": row["name"], "from_tier": row["tier"], "to_tier": tier}


def dismiss_suggestion(conn: sqlite3.Connection, source_id: str) -> None:
    conn.execute("UPDATE source_stats SET suggested_tier = NULL WHERE source_id = ?", (source_id,))
