"""A fire's life, in order: one chronological stream per incident.

Everything the workstation knows about a fire, laid on a single time axis —
when it started, every acreage and containment revision, every evacuation
notice (verbatim), the cause and damage as they were confirmed, each article
at its release time, perimeter maps, and the lifecycle marks (created,
contained). Nothing invented: every row is a real ledger record with its
source and tier, so the timeline is auditable, not narrated.

The events come straight from the append-only ledger and its neighbours
(publications, drafts, perimeter_history, incident_events); the caller renders
them. Times are the OBSERVED time (when a thing was true), not when we ingested
it — a 6 a.m. evacuation order sits at 6 a.m., however late we heard.
"""

from __future__ import annotations

import sqlite3
from typing import Any

# fact.kind -> the timeline category the UI colours and filters by
_KIND_CATEGORY = {
    "start": "start",
    "acreage": "acreage",
    "containment": "containment",
    "evac": "evac",
    "cause": "cause",
    "damage": "damage",
    "road": "road",
    "weather": "weather",
    "resource": "resource",
}


def _fact_title(row: sqlite3.Row) -> str:
    """A short headline for a fact event; the full content rides as detail."""
    kind = row["kind"]
    if kind == "acreage" and row["acreage"] is not None:
        return f"{int(row['acreage']):,} acres"
    if kind == "containment" and row["containment_pct"] is not None:
        return f"{int(row['containment_pct'])}% contained"
    if kind == "evac":
        st = (row["evac_status"] or "").replace("_", " ").upper()
        return f"EVACUATION {st}".strip() if st else "Evacuation update"
    if kind == "start":
        return "Fire reported"
    if kind == "cause":
        return "Cause"
    if kind == "damage":
        return "Damage / structures"
    if kind == "road":
        return "Road / closure"
    if kind == "weather":
        return "Fire weather"
    if kind == "resource":
        return "Resources"
    return (row["content"] or "").strip()[:80] or "Update"


def fire_timeline(conn: sqlite3.Connection, incident_id: str) -> dict[str, Any]:
    inc = conn.execute(
        "SELECT id, canonical_name, county, status, lat, lon, created_at, updated_at"
        " FROM incidents WHERE id = ?",
        (incident_id,),
    ).fetchone()
    if inc is None:
        raise ValueError("incident not found")

    # which facts were later superseded — shown struck-through, not hidden, so
    # the progression (acreage climbing, containment growing) stays visible
    superseded = {
        r[0] for r in conn.execute(
            "SELECT supersedes FROM facts WHERE supersedes IS NOT NULL AND supersedes != ''"
        )
    }

    events: list[dict[str, Any]] = []

    # 1) the ledger itself — the substance of the timeline
    facts = conn.execute(
        """SELECT f.id, f.kind, f.content, f.tier, f.observed_at, f.ingested_at,
                  f.acreage, f.containment_pct, f.evac_status, f.confidence,
                  f.starred, s.name AS source_name, s.type AS source_type
           FROM facts f LEFT JOIN sources s ON s.id = f.source_id
           WHERE f.incident_id = ? AND f.observed_at IS NOT NULL
           ORDER BY f.observed_at ASC""",
        (incident_id,),
    ).fetchall()
    start_seen = False
    for f in facts:
        # every source re-reports the start; the timeline wants ONE detection
        # moment, not twenty. Keep the earliest, drop the rest.
        if f["kind"] == "start":
            if start_seen:
                continue
            start_seen = True
        events.append({
            "at": f["observed_at"],
            "ingested_at": f["ingested_at"],
            "category": _KIND_CATEGORY.get(f["kind"], "note"),
            "title": _fact_title(f),
            "detail": (f["content"] or "").strip() or None,
            "source": f["source_name"],
            "tier": f["tier"],
            "confidence": f["confidence"],
            "starred": bool(f["starred"]),
            "superseded": f["id"] in superseded,
            "url": None,
        })

    # 2) articles at their release time — published pieces about this fire
    pubs = conn.execute(
        """SELECT p.sent_at, p.channel, p.external_url, d.title
           FROM publications p JOIN drafts d ON d.id = p.draft_id
           WHERE d.incident_id = ? AND p.sent_at IS NOT NULL""",
        (incident_id,),
    ).fetchall()
    for p in pubs:
        events.append({
            "at": p["sent_at"],
            "category": "article",
            "title": p["title"] or "Published report",
            "detail": f"Released to {p['channel']}" if p["channel"] else None,
            "source": p["channel"],
            "tier": None,
            "url": p["external_url"],
            "superseded": False,
        })

    # 3) reports the operator approved (even if not externally published) — the
    #    newsroom paper trail for this fire
    reports = conn.execute(
        """SELECT title, kind, status, approved_at, created_at
           FROM drafts WHERE incident_id = ? AND archived = 0
             AND status IN ('approved', 'published')""",
        (incident_id,),
    ).fetchall()
    for r in reports:
        events.append({
            "at": r["approved_at"] or r["created_at"],
            "category": "report",
            "title": r["title"] or "Report",
            "detail": f"{(r['kind'] or 'report').upper()} · {r['status']}",
            "source": "Newsroom",
            "tier": None,
            "url": None,
            "superseded": False,
        })

    # 4) perimeter maps — each infrared/GIS footprint as it landed
    perims = conn.execute(
        "SELECT mapped_at, captured_at, acres FROM perimeter_history"
        " WHERE incident_id = ? ORDER BY captured_at ASC",
        (incident_id,),
    ).fetchall()
    for pm in perims:
        acres = f"{int(pm['acres']):,} ac" if pm["acres"] is not None else "mapped"
        events.append({
            "at": pm["mapped_at"] or pm["captured_at"],
            "category": "perimeter",
            "title": f"Perimeter mapped — {acres}",
            "detail": None,
            "source": "Perimeter feed",
            "tier": 1,
            "url": None,
            "superseded": False,
        })

    # 5) lifecycle marks — created, status changes (contained, reopened)
    for ev in conn.execute(
        "SELECT event, detail, actor, at FROM incident_events WHERE incident_id = ?"
        " ORDER BY at ASC",
        (incident_id,),
    ).fetchall():
        events.append({
            "at": ev["at"],
            "category": "status",
            "title": (ev["event"] or "event").replace("_", " ").title(),
            "detail": ev["detail"],
            "source": ev["actor"],
            "tier": None,
            "url": None,
            "superseded": False,
        })

    events = [e for e in events if e["at"]]
    events.sort(key=lambda e: e["at"])

    # start = the first thing we can point to; finish only when it's actually out
    start = events[0]["at"] if events else inc["created_at"]
    ongoing = (inc["status"] or "") not in ("contained", "frozen")
    finish = None
    if not ongoing:
        full = conn.execute(
            """SELECT observed_at FROM facts
               WHERE incident_id = ? AND kind = 'containment' AND containment_pct >= 100
               ORDER BY observed_at ASC LIMIT 1""",
            (incident_id,),
        ).fetchone()
        finish = (full["observed_at"] if full else None) or inc["updated_at"]

    counts: dict[str, int] = {}
    for e in events:
        counts[e["category"]] = counts.get(e["category"], 0) + 1

    return {
        "incident": {
            "id": inc["id"],
            "name": inc["canonical_name"],
            "county": inc["county"],
            "status": inc["status"],
            "lat": inc["lat"],
            "lon": inc["lon"],
        },
        "start": start,
        "finish": finish,
        "ongoing": ongoing,
        "counts": counts,
        "event_count": len(events),
        "events": events,
    }
