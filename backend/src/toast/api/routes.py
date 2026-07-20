"""Read-only operator API routes (Phase 1).

All queries parameterized; responses are plain dicts serialized by FastAPI.
Stale detection: a fact is stale-flagged when ingested_at trails observed_at
by more than STALE_THRESHOLD_S (news reporting old information as new).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

STALE_THRESHOLD_S = 6 * 3600

router = APIRouter()


def _conn(request: Request) -> sqlite3.Connection:
    return request.app.state.conn


def _fact_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["structured"] = json.loads(d.get("structured") or "{}")
    if "stale_s" in d:
        d["stale"] = (d["stale_s"] or 0) > STALE_THRESHOLD_S
    return d


FACT_COLS = """f.id, f.incident_id, f.source_id, f.tier, f.content, f.kind,
    f.acreage, f.containment_pct, f.evac_status, f.structured, f.confidence,
    f.observed_at, f.ingested_at, f.lat, f.lon, f.geo_method, f.supersedes,
    f.starred, f.raw_ref,
    (unixepoch(f.ingested_at) - unixepoch(f.observed_at)) AS stale_s,
    s.name AS source_name,
    (SELECT id FROM facts sup WHERE sup.supersedes = f.id) AS superseded_by,
    (SELECT effective_confidence FROM fact_assessments a WHERE a.fact_id = f.id)
        AS effective_confidence,
    (SELECT corroborating_sources FROM fact_assessments a WHERE a.fact_id = f.id)
        AS corroborating_sources,
    (SELECT COALESCE(json_extract(e.payload, '$.url'), json_extract(e.payload, '$.link'))
       FROM envelopes e WHERE e.id = f.envelope_id) AS source_url"""


@router.get("/incidents")
def list_incidents(request: Request, status: str | None = None, include_rx: bool = False) -> dict:
    clauses, params = [], []
    if status:
        clauses.append("i.status = ?")
        params.append(status)
    if not include_rx:
        clauses.append("COALESCE(i.incident_type, 'WF') != 'RX'")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    # latest non-superseded value fact for a metric on an incident
    latest = """(SELECT {col} FROM facts v WHERE v.incident_id = i.id AND v.kind = '{kind}'
                 AND v.id NOT IN (SELECT supersedes FROM facts WHERE supersedes IS NOT NULL)
                 ORDER BY v.observed_at DESC LIMIT 1)"""
    rows = (
        _conn(request)
        .execute(
            f"""SELECT i.*, COUNT(f.id) AS fact_count, MAX(f.observed_at) AS last_observed,
                   MIN(f.observed_at) AS started_at,
                   {latest.format(col="acreage", kind="acreage")} AS acreage,
                   {latest.format(col="containment_pct", kind="containment")} AS containment_pct,
                   -- active evacuation = latest non-superseded evac fact still
                   -- ordering/warning (not lifted). Drives the map's evac color.
                   {latest.format(col="evac_status", kind="evac")} AS evac_status
            FROM incidents i LEFT JOIN facts f ON f.incident_id = i.id
            {where}
            GROUP BY i.id ORDER BY last_observed DESC NULLS LAST""",
            params,
        )
        .fetchall()
    )
    return {"incidents": [dict(r) for r in rows]}


@router.get("/incidents/{incident_id}")
def get_incident(request: Request, incident_id: str) -> dict:
    row = _conn(request).execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "incident not found")
    aliases = [
        r["alias"]
        for r in _conn(request).execute(
            "SELECT alias FROM incident_aliases WHERE incident_id = ?", (incident_id,)
        )
    ]
    return {"incident": dict(row), "aliases": aliases}


@router.get("/incidents/{incident_id}/facts")
def incident_facts(
    request: Request,
    incident_id: str,
    limit: int = Query(200, le=1000),
) -> dict:
    rows = (
        _conn(request)
        .execute(
            f"""SELECT {FACT_COLS} FROM facts f JOIN sources s ON s.id = f.source_id
            WHERE f.incident_id = ?
            ORDER BY f.observed_at DESC LIMIT ?""",
            (incident_id, limit),
        )
        .fetchall()
    )
    return {"facts": [_fact_dict(r) for r in rows]}


@router.get("/intake")
def intake(
    request: Request,
    limit: int = Query(50, le=500),
    min_tier: int = Query(5, ge=1, le=5),
) -> dict:
    """Recent facts across all incidents — the OPS intake stream."""
    rows = (
        _conn(request)
        .execute(
            f"""SELECT {FACT_COLS}, i.canonical_name AS incident_name,
                i.county AS incident_county
            FROM facts f JOIN sources s ON s.id = f.source_id
            LEFT JOIN incidents i ON i.id = f.incident_id
            WHERE f.tier <= ?
            ORDER BY f.ingested_at DESC LIMIT ?""",
            (min_tier, limit),
        )
        .fetchall()
    )
    return {"facts": [_fact_dict(r) for r in rows]}


@router.get("/incidents/{incident_id}/intel")
def get_incident_intel(request: Request, incident_id: str) -> dict:
    """Aggregated intelligence for the OPS panel: header metrics with
    supersession deltas, source-confidence meter, situation bullets, and
    evac/road impacts (verbatim — the UI renders them untouched)."""
    from toast.pipeline.intel import incident_intel

    conn = _conn(request)
    if conn.execute("SELECT 1 FROM incidents WHERE id = ?", (incident_id,)).fetchone() is None:
        raise HTTPException(404, "incident not found")
    return incident_intel(conn, incident_id)


@router.get("/incidents/{incident_id}/timeline")
def get_incident_timeline(request: Request, incident_id: str) -> dict:
    from toast.pipeline.intel import incident_timeline

    return {"timeline": incident_timeline(_conn(request), incident_id)}


@router.post("/incidents/{incident_id}/follow")
def follow_incident(request: Request, incident_id: str, body: dict) -> dict:
    """Toggle the operator's ★ FOLLOW flag on an incident."""
    followed = 1 if body.get("followed") else 0
    cur = _conn(request).execute(
        "UPDATE incidents SET followed = ? WHERE id = ?", (followed, incident_id)
    )
    if cur.rowcount == 0:
        raise HTTPException(404, "incident not found")
    return {"ok": True, "followed": bool(followed)}


# ---------------- backups ----------------


@router.get("/backups")
def get_backups(request: Request) -> dict:
    """Backup posture. `at_risk` is true whenever no offsite copy is
    configured — local-only copies die with the machine."""
    from toast.backup import backup_status, list_snapshots

    return {**backup_status(conn=_conn(request)), "all": list_snapshots()[:10]}


@router.post("/backups/run")
def run_backup_now(request: Request) -> dict:
    """Operator-triggered verified snapshot (same path as the nightly job)."""
    from toast.backup import run_backup

    try:
        return run_backup(_conn(request))
    except Exception as exc:  # surfaced to the operator, never silent
        raise HTTPException(500, f"backup failed: {exc}") from exc


@router.get("/drafts/{draft_id}/export")
def export_draft(request: Request, draft_id: str, fmt: str = "markdown") -> dict:
    """Draft text for manual posting to the operator's own website.

    Publishing is a human act here: nothing is pushed anywhere automatically,
    so this hands back exactly what to paste, with the source attribution
    intact."""
    conn = _conn(request)
    row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "draft not found")
    if fmt not in ("markdown", "html", "text"):
        raise HTTPException(400, "fmt must be markdown, html or text")

    body = row["body_md"] or ""
    title = row["title"] or "Untitled"
    if fmt == "html":
        import html as _html

        import markdown as _md

        rendered = _md.markdown(body, extensions=["extra"])
        content = f"<article>\n<h1>{_html.escape(title)}</h1>\n{rendered}\n</article>"
    elif fmt == "text":
        content = f"{title}\n\n{body}"
    else:
        content = f"# {title}\n\n{body}"
    return {
        "draft_id": draft_id,
        "title": title,
        "format": fmt,
        "content": content,
        "status": row["status"],
    }


@router.post("/site/build")
def build_site_now(request: Request) -> dict:
    """Build the LOCAL static site on demand (preview at /site).

    Never triggered by publishing — the operator runs it when they want to see
    or hand-copy the rendered output."""
    from toast.queue import jobs

    return {"ok": True, "job_id": jobs.enqueue(_conn(request), "build_site", {}, priority=4)}


# ---------------- GNIS gazetteer ----------------


@router.get("/gnis")
def gnis_status(request: Request) -> dict:
    """Gazetteer status — how many place names are loaded."""
    from toast.pipeline.geocode import gnis_count

    return {"features": gnis_count(_conn(request))}


@router.post("/gnis/load")
def gnis_load(request: Request) -> dict:
    """Download and load the California place-name gazetteer (public domain,
    no key). Runs as a job — it is a few seconds of work, not a request."""
    from toast.queue import jobs

    return {"ok": True, "job_id": jobs.enqueue(_conn(request), "gnis_load", {}, priority=8)}


@router.get("/gnis/resolve")
def gnis_resolve(request: Request, q: str, county: str | None = None) -> dict:
    """Look up a place or fire name — useful for checking what the geocoder
    will do before trusting it."""
    from dataclasses import asdict

    from toast.pipeline.geocode import resolve_fire_name, resolve_place

    conn = _conn(request)
    place = resolve_place(conn, q, county=county)
    fire = resolve_fire_name(conn, q, county=county)
    return {
        "query": q,
        "county": county,
        "place": asdict(place) if place else None,
        "as_fire_name": asdict(fire) if fire else None,
    }


# ---------------- API credentials ----------------


@router.get("/credentials")
def list_credentials(request: Request) -> dict:
    """Service registry + configuration/connection status.

    Deliberately returns NO secret values — only whether each field is set, a
    masked hint, and the last test result. There is no read-back endpoint."""
    from toast.core.credentials import service_status

    return {"services": service_status(_conn(request))}


@router.put("/credentials")
def put_credentials(request: Request, body: dict) -> dict:
    """Store credentials. An empty value clears it (falling back to .env)."""
    from toast.core.credentials import service_status, set_credential

    updates = body.get("credentials")
    if not isinstance(updates, dict):
        raise HTTPException(400, "credentials object required")
    conn = _conn(request)
    try:
        for name, value in updates.items():
            set_credential(conn, str(name), "" if value is None else str(value))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "services": service_status(conn)}


@router.post("/credentials/{service}/test")
def test_credentials(request: Request, service: str) -> dict:
    """Make the cheapest real call the provider offers and report the verdict.
    Read-only: nothing is posted, uploaded or published while testing."""
    from toast.core.credcheck import run_check

    try:
        return run_check(_conn(request), service)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


# ---------------- operator settings ----------------


@router.get("/settings")
def get_settings(request: Request) -> dict:
    """Effective runtime settings + the models the LLM endpoint actually has."""
    from toast.core.appsettings import effective_settings
    from toast.llm.client import LLMClient

    return {
        "settings": effective_settings(_conn(request)),
        "models": LLMClient().list_models(),
    }


@router.put("/settings")
def put_settings(request: Request, body: dict) -> dict:
    """Update runtime settings. Only allowlisted keys; empty value reverts to
    the .env default."""
    from toast.core.appsettings import effective_settings, set_setting

    conn = _conn(request)
    updates = body.get("settings")
    if not isinstance(updates, dict):
        raise HTTPException(400, "settings object required")
    try:
        for key, value in updates.items():
            set_setting(conn, key, "" if value is None else str(value))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "settings": effective_settings(conn)}


# ---------------- analyst notes & tasks ----------------


@router.get("/notes")
def list_notes(request: Request, incident_id: str | None = None) -> dict:
    """Operator notes: incident-scoped plus global ones (incident_id NULL)."""
    conn = _conn(request)
    if incident_id:
        rows = conn.execute(
            """SELECT * FROM operator_notes
               WHERE incident_id = ? OR incident_id IS NULL
               ORDER BY done, created_at DESC""",
            (incident_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM operator_notes ORDER BY done, created_at DESC"
        ).fetchall()
    return {"notes": [dict(r) for r in rows]}


@router.post("/notes")
def create_note(request: Request, body: dict) -> dict:
    from toast.core.db import utcnow
    from toast.core.ids import ulid

    text = (body.get("body") or "").strip()
    if not text:
        raise HTTPException(400, "body required")
    note_id = ulid()
    _conn(request).execute(
        "INSERT INTO operator_notes (id, incident_id, body, created_at) VALUES (?, ?, ?, ?)",
        (note_id, body.get("incident_id"), text[:2000], utcnow()),
    )
    return {"ok": True, "note_id": note_id}


@router.post("/notes/{note_id}/toggle")
def toggle_note(request: Request, note_id: str) -> dict:
    cur = _conn(request).execute(
        "UPDATE operator_notes SET done = 1 - done WHERE id = ?", (note_id,)
    )
    if cur.rowcount == 0:
        raise HTTPException(404, "note not found")
    return {"ok": True}


@router.delete("/notes/{note_id}")
def delete_note(request: Request, note_id: str) -> dict:
    cur = _conn(request).execute("DELETE FROM operator_notes WHERE id = ?", (note_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "note not found")
    return {"ok": True}


@router.get("/adjudication")
def adjudication_queue(request: Request, limit: int = Query(50, le=200)) -> dict:
    """Unattributed non-official facts awaiting operator attribution, with
    scored incident candidates for one-click resolve."""
    from toast.pipeline.attribution import REVIEW_THRESHOLD, score_candidates

    conn = _conn(request)
    rows = conn.execute(
        f"""SELECT {FACT_COLS} FROM facts f JOIN sources s ON s.id = f.source_id
            WHERE f.incident_id IS NULL AND f.kind NOT IN ('weather')
              AND f.id NOT IN (SELECT fact_id FROM fact_triage)
            ORDER BY f.ingested_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    items = []
    for row in rows:
        fact = _fact_dict(row)
        structured = fact["structured"]
        claimed_name = structured.get("incident_name")
        candidates = score_candidates(
            conn,
            observed_at=row["observed_at"],
            incident_name=claimed_name,
            lat=row["lat"],
            lon=row["lon"],
        )
        # A candidate carried only by the time signal is noise, not a match:
        # every open incident is "recent", so it would rank identically for
        # every fact. Requiring name or geo evidence keeps the queue honest.
        scored: list[dict[str, Any]] = [
            {
                "incident_id": c.incident_id,
                "incident_name": c.incident_name,
                "score": round(c.score, 3),
                "name_score": round(c.name_score, 2),
                "geo_score": round(c.geo_score, 2),
                "time_score": round(c.time_score, 2),
            }
            for c in candidates
            if c.score >= REVIEW_THRESHOLD * 0.5 and (c.name_score > 0.15 or c.geo_score > 0)
        ]
        items.append(
            {
                "fact": fact,
                "candidates": scored,
                "claimed_name": claimed_name,
                # names a fire we don't track — belongs on the WATCH board as a
                # possible new start, not attributed to an existing incident
                "unknown_fire": bool(claimed_name)
                and not any(float(c["name_score"]) >= 0.6 for c in scored),
            }
        )
    return {"queue": items}


@router.post("/adjudication/{fact_id}/triage")
def triage_fact(request: Request, fact_id: str, body: dict) -> dict:
    """Dispose of a fact that belongs to no tracked incident.

    The fact is never modified — triage is recorded in a derived table, so the
    claim stays exactly as observed while leaving the operator's queue."""
    from toast.core.db import utcnow

    state = (body.get("state") or "").strip()
    if state not in ("not_incident", "watch", "deferred"):
        raise HTTPException(400, "state must be not_incident, watch or deferred")
    conn = _conn(request)
    row = conn.execute("SELECT incident_id FROM facts WHERE id = ?", (fact_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "fact not found")
    if row["incident_id"] is not None:
        raise HTTPException(409, "fact is already attributed")
    conn.execute(
        """INSERT INTO fact_triage (fact_id, state, note, at) VALUES (?, ?, ?, ?)
           ON CONFLICT(fact_id) DO UPDATE SET state = excluded.state,
                                              note = excluded.note, at = excluded.at""",
        (fact_id, state, (body.get("note") or "")[:500] or None, utcnow()),
    )
    return {"ok": True, "fact_id": fact_id, "state": state}


@router.post("/adjudication/triage")
def triage_bulk(request: Request, body: dict) -> dict:
    """Triage many facts at once — clearing a backlog one row at a time is how
    backlogs stay."""
    from toast.core.db import utcnow

    state = (body.get("state") or "").strip()
    fact_ids = body.get("fact_ids") or []
    if state not in ("not_incident", "watch", "deferred"):
        raise HTTPException(400, "state must be not_incident, watch or deferred")
    if not isinstance(fact_ids, list) or not fact_ids:
        raise HTTPException(400, "fact_ids required")
    conn = _conn(request)
    now = utcnow()
    done = 0
    for fact_id in fact_ids[:500]:
        row = conn.execute("SELECT incident_id FROM facts WHERE id = ?", (str(fact_id),)).fetchone()
        if row is None or row["incident_id"] is not None:
            continue
        conn.execute(
            """INSERT INTO fact_triage (fact_id, state, at) VALUES (?, ?, ?)
               ON CONFLICT(fact_id) DO UPDATE SET state = excluded.state, at = excluded.at""",
            (str(fact_id), state, now),
        )
        done += 1
    return {"ok": True, "triaged": done, "state": state}


@router.post("/adjudication/{fact_id}/untriage")
def untriage_fact(request: Request, fact_id: str) -> dict:
    """Put a triaged fact back in the queue (nothing is ever destroyed)."""
    _conn(request).execute("DELETE FROM fact_triage WHERE fact_id = ?", (fact_id,))
    return {"ok": True}


@router.post("/adjudication/{fact_id}")
def resolve_adjudication(request: Request, fact_id: str, body: dict) -> dict:
    """Operator resolves attribution: {"incident_id": "..."} attaches the fact
    (the one UPDATE the append-only trigger permits: NULL → value)."""
    conn = _conn(request)
    incident_id = body.get("incident_id")
    if not incident_id:
        raise HTTPException(400, "incident_id required")
    fact = conn.execute("SELECT incident_id FROM facts WHERE id = ?", (fact_id,)).fetchone()
    if fact is None:
        raise HTTPException(404, "fact not found")
    if fact["incident_id"] is not None:
        raise HTTPException(409, "fact already attributed")
    if conn.execute("SELECT 1 FROM incidents WHERE id = ?", (incident_id,)).fetchone() is None:
        raise HTTPException(404, "incident not found")
    conn.execute("UPDATE facts SET incident_id = ? WHERE id = ?", (incident_id, fact_id))
    return {"ok": True, "fact_id": fact_id, "incident_id": incident_id}


# ---------------- watch board: new starts ----------------


@router.get("/watch")
def watch_board(request: Request) -> dict:
    """Candidate new-start fires — unattributed named-fire signals not in the
    active-incident list, clustered and scored by corroboration."""
    from dataclasses import asdict

    from toast.pipeline.watch import build_watchlist

    return {"candidates": [asdict(c) for c in build_watchlist(_conn(request))]}


@router.post("/watch/triage")
def watch_triage(request: Request, body: dict) -> dict:
    """Dispose of a watch candidate: ignored (not a real start), archived (a
    real fire, but old), or keep. Underlying facts are never modified."""
    from toast.pipeline.watch_triage import set_state

    key = (body.get("key") or "").strip()
    if not key:
        raise HTTPException(400, "key required")
    try:
        return set_state(
            _conn(request), key, (body.get("state") or "").strip(), note=body.get("note")
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/watch/untriage")
def watch_untriage(request: Request, body: dict) -> dict:
    """Put a filed candidate back on the board."""
    from toast.pipeline.watch_triage import clear_state

    key = (body.get("key") or "").strip()
    if not key:
        raise HTTPException(400, "key required")
    clear_state(_conn(request), key)
    return {"ok": True, "key": key}


@router.get("/watch/archived")
def watch_archived(request: Request) -> dict:
    from toast.pipeline.watch_triage import archived_candidates

    return {"archived": archived_candidates(_conn(request))}


@router.post("/watch/classify")
def watch_classify(request: Request, body: dict) -> dict:
    """Age check: ask the local model whether this candidate is burning now or
    is an old fire being referenced. Suggests only — the operator decides."""
    from toast.llm.client import LLMError
    from toast.pipeline.watch_triage import classify_candidate

    key = (body.get("key") or "").strip()
    if not key:
        raise HTTPException(400, "key required")
    try:
        return classify_candidate(_conn(request), key)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(502, f"age check failed: {exc}") from exc


@router.post("/watch/promote")
def promote_candidate(request: Request, body: dict) -> dict:
    """Promote a watch candidate to a real (candidate-status) incident."""
    from toast.pipeline.watch import promote

    key = (body.get("key") or "").strip()
    if not key:
        raise HTTPException(400, "key required")
    result = promote(_conn(request), key)
    if not result.get("promoted"):
        raise HTTPException(404, result.get("reason", "could not promote"))
    return result


# ---------------- active research (Local Deep Research) ----------------


@router.post("/research")
def start_research(request: Request, body: dict) -> dict:
    """Queue a deep-research run. The result lands as a kind='analysis' draft
    in the NEWSROOM for review — never directly in the ledger."""
    from toast.queue import jobs

    query = (body.get("query") or "").strip()
    if not query or len(query) < 8:
        raise HTTPException(400, "query required (be specific)")
    incident_id = body.get("incident_id")
    conn = _conn(request)
    if (
        incident_id
        and not conn.execute("SELECT 1 FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    ):
        raise HTTPException(404, "incident not found")
    job_id = jobs.enqueue(
        conn, "deep_research", {"query": query, "incident_id": incident_id}, priority=8
    )
    return {"queued": True, "job_id": job_id}


@router.post("/research/cancel")
def cancel_research(request: Request, body: dict) -> dict:
    """Cancel/dismiss a research run: pending jobs are closed out (audit trail
    kept in last_error); a failed entry is dismissed the same way."""
    key = (body.get("key") or "").strip()
    if not key:
        raise HTTPException(400, "key required")
    conn = _conn(request)
    cancelled = 0
    for row in conn.execute(
        """SELECT id, payload FROM jobs
           WHERE stage IN ('deep_research', 'deep_research_poll')
             AND state IN ('queued', 'running', 'dead')"""
    ).fetchall():
        payload = json.loads(row["payload"])
        if (payload.get("research_id") or payload.get("query")) == key:
            conn.execute(
                "UPDATE jobs SET state='done', last_error='cancelled by operator' WHERE id = ?",
                (row["id"],),
            )
            cancelled += 1
    if not cancelled:
        raise HTTPException(404, "no matching research run")
    return {"ok": True, "cancelled_jobs": cancelled}


# ---------------- newsroom: drafts ----------------

DRAFT_TRANSITIONS = {
    "draft": {"needs_review", "rejected"},
    "needs_review": {"approved", "draft", "rejected"},
    "approved": {"published", "draft"},
}
OPERATOR = "operator"  # single-operator system; auth arrives if UI is ever exposed


@router.post("/incidents/{incident_id}/draft")
def create_draft(request: Request, incident_id: str, body: dict) -> dict:
    """Generate an AI draft update for an incident over an observed_at window."""
    from toast.core.fetch import FetchError
    from toast.llm.client import LLMError
    from toast.pipeline.drafting import generate_draft

    try:
        result = generate_draft(
            _conn(request),
            incident_id,
            window_start=body.get("window_start"),
            window_end=body.get("window_end"),
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (LLMError, FetchError) as exc:
        raise HTTPException(503, f"LLM unavailable: {exc}") from exc
    if not result.get("created"):
        raise HTTPException(422, result.get("reason", "nothing to draft"))
    return result


@router.get("/drafts")
def list_drafts(request: Request, status: str | None = None) -> dict:
    where, params = "", []
    if status:
        where = "WHERE d.status = ?"
        params.append(status)
    conn = _conn(request)
    rows = conn.execute(
        f"""SELECT d.id, d.incident_id, d.kind, d.status, d.title, d.window_start,
               d.window_end, d.approved_by, d.approved_at, d.created_at, d.updated_at,
               d.parent_id, i.canonical_name AS incident_name,
               json_array_length(d.cited_fact_ids) AS cited_count
        FROM drafts d LEFT JOIN incidents i ON i.id = d.incident_id
        {where}
        ORDER BY d.updated_at DESC""",
        params,
    ).fetchall()
    return {"drafts": [dict(r) for r in rows], "research_jobs": _active_research(conn)}


def _active_research(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """In-flight deep-research runs for the draft queue — so the operator can
    see a research is working before its draft lands. Dead runs from the last
    24h surface as 'failed' so silent losses are visible."""
    rows = conn.execute(
        """SELECT id, stage, state, payload, run_after, created_at, last_error
           FROM jobs
           WHERE stage IN ('deep_research', 'deep_research_poll')
             AND (state IN ('queued', 'running')
                  OR (state = 'dead'
                      AND unixepoch('now') - unixepoch(created_at) < 86400))
           ORDER BY created_at ASC"""
    ).fetchall()
    items: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = json.loads(row["payload"])
        query = payload.get("query", "research")
        key = payload.get("research_id") or query  # poll hops share research_id
        if row["state"] == "dead":
            state = "failed"
        elif payload.get("rl_hops") and row["state"] == "queued":
            state = "parked"  # waiting out LDR's rate limiter
        elif row["stage"] == "deep_research":
            state = "starting"
        else:
            state = "researching"
        incident_id = payload.get("incident_id")
        incident_name = None
        if incident_id:
            inc = conn.execute(
                "SELECT canonical_name FROM incidents WHERE id = ?", (incident_id,)
            ).fetchone()
            incident_name = inc["canonical_name"] if inc else None
        # later rows (newer hops) overwrite earlier ones; failed wins over stale
        prior = items.get(key)
        if prior and prior["state"] == "failed" and state != "failed":
            continue
        items[key] = {
            "key": key,
            "query": query,
            "state": state,
            "incident_id": incident_id,
            "incident_name": incident_name,
            "since": row["created_at"],
            "error": (row["last_error"] or "")[:140] if row["state"] == "dead" else None,
        }
    return list(items.values())


@router.get("/drafts/{draft_id}")
def get_draft(request: Request, draft_id: str) -> dict:
    conn = _conn(request)
    row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "draft not found")
    draft = dict(row)
    cited_ids = json.loads(draft.get("cited_fact_ids") or "[]")
    cited = []
    if cited_ids:
        placeholders = ",".join("?" * len(cited_ids))
        cited = [
            dict(r)
            for r in conn.execute(
                f"""SELECT f.id, f.content, f.kind, f.tier, f.observed_at, s.name AS source_name
                    FROM facts f JOIN sources s ON s.id = f.source_id
                    WHERE f.id IN ({placeholders})""",
                cited_ids,
            )
        ]
    pubs = [
        dict(r)
        for r in conn.execute(
            """SELECT id, channel, status, external_url, sent_at
               FROM publications WHERE draft_id = ? ORDER BY sent_at""",
            (draft_id,),
        )
    ]
    return {"draft": draft, "cited_facts": cited, "publications": pubs}


@router.post("/drafts/{draft_id}/social")
def create_social(request: Request, draft_id: str) -> dict:
    """Second writing layer: rewrite a report draft in a human voice for
    posting (full + X-length). Lands as a linked kind='social' draft."""
    from toast.core.fetch import FetchError
    from toast.llm.client import LLMError
    from toast.pipeline.social import generate_social

    try:
        return generate_social(_conn(request), draft_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (LLMError, FetchError) as exc:
        raise HTTPException(503, f"LLM unavailable: {exc}") from exc


@router.put("/drafts/{draft_id}")
def edit_draft(request: Request, draft_id: str, body: dict) -> dict:
    """Edit the draft body/title. Only allowed before publish."""
    from toast.core.db import utcnow

    conn = _conn(request)
    row = conn.execute("SELECT status FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "draft not found")
    if row["status"] == "published":
        raise HTTPException(409, "published drafts are immutable")
    conn.execute(
        "UPDATE drafts SET body_md = COALESCE(?, body_md), title = COALESCE(?, title),"
        " updated_at = ? WHERE id = ?",
        (body.get("body_md"), body.get("title"), utcnow(), draft_id),
    )
    return {"ok": True, "draft_id": draft_id}


@router.post("/drafts/{draft_id}/status")
def transition_draft(request: Request, draft_id: str, body: dict) -> dict:
    """Advance a draft through the approval workflow. The DB trigger is the
    real guard; this validates + sets approver fields before publish is possible."""
    from toast.core.db import utcnow

    conn = _conn(request)
    target = body.get("status")
    row = conn.execute("SELECT status, kind FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "draft not found")
    current = row["status"]
    if target not in DRAFT_TRANSITIONS.get(current, set()):
        raise HTTPException(409, f"cannot go {current} → {target}")
    now = utcnow()
    if target == "approved":
        conn.execute(
            "UPDATE drafts SET approved_by = ?, approved_at = ?, updated_at = ? WHERE id = ?",
            (OPERATOR, now, now, draft_id),
        )
    try:
        conn.execute(
            "UPDATE drafts SET status = ?, updated_at = ? WHERE id = ?", (target, now, draft_id)
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, f"illegal transition: {exc}") from exc

    # Publishing to the static site needs no credentials — record the delivery
    # and rebuild the site. External channels (X/Facebook/email) remain deferred
    # until their credentials are configured; they are not triggered here.
    if target == "published":
        from toast.core.ids import ulid
        from toast.queue import jobs

        # NOTE: publishing does NOT push to any website. The operator publishes
        # to their own site by hand (NEWSROOM → ⧉ COPY / EXPORT); 'published'
        # here means "cleared for release", and the local static site is built
        # on demand from ARCHIVE, never automatically.

        # SOCIAL drafts fan out to every credentialed channel.
        channels: list[str] = []
        if row["kind"] == "social":
            from toast.core.credentials import settings_with_credentials
            from toast.publishers import configured_publishers

            for p in configured_publishers(settings_with_credentials(conn)):
                pub_id = ulid()
                conn.execute(
                    "INSERT INTO publications (id, draft_id, channel, status)"
                    " VALUES (?, ?, ?, 'queued')",
                    (pub_id, draft_id, p.channel),
                )
                jobs.enqueue(
                    conn,
                    "publish",
                    {"draft_id": draft_id, "channel": p.channel, "publication_id": pub_id},
                    priority=3,
                )
                channels.append(p.channel)
        return {"ok": True, "draft_id": draft_id, "status": target, "channels": channels}
    return {"ok": True, "draft_id": draft_id, "status": target}


@router.post("/analyze")
def analyze(request: Request, body: dict) -> dict:
    """Operator points the AI at a URL; claims are extracted, written as T4
    facts, and attributed. Safe fetcher enforces SSRF/size limits."""
    from toast.core.fetch import BlockedURLError, FetchError
    from toast.llm.client import LLMError
    from toast.pipeline.analyze import analyze_url

    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "url required")
    try:
        return analyze_url(_conn(request), url)
    except BlockedURLError as exc:
        raise HTTPException(400, f"URL blocked: {exc}") from exc
    except FetchError as exc:
        raise HTTPException(502, f"fetch failed: {exc}") from exc
    except LLMError as exc:
        raise HTTPException(503, f"LLM unavailable: {exc}") from exc


@router.get("/search/fires")
def search_fires_route(request: Request, q: str = "", year: int | None = None) -> dict:
    """Search the external CAL FIRE catalog for historical fires (read-only)."""
    from toast.core.fetch import FetchError
    from toast.pipeline.fire_search import search_fires

    try:
        return {"results": search_fires(_conn(request), q, year=year)}
    except FetchError as exc:
        raise HTTPException(502, f"CAL FIRE catalog unavailable: {exc}") from exc


@router.post("/import/fire")
def import_fire_route(request: Request, body: dict) -> dict:
    """Import one historical fire (by CAL FIRE ref_id) into the ledger."""
    from toast.core.fetch import FetchError
    from toast.pipeline.fire_search import import_fire

    ref_id = (body.get("ref_id") or "").strip()
    if not ref_id:
        raise HTTPException(400, "ref_id required")
    try:
        result = import_fire(_conn(request), ref_id)
    except FetchError as exc:
        raise HTTPException(502, f"CAL FIRE catalog unavailable: {exc}") from exc
    if not result.get("imported") and result.get("reason") == "not found in CAL FIRE catalog":
        raise HTTPException(404, "fire not found in CAL FIRE catalog")
    return result


@router.post("/sources/probe")
def probe_source(request: Request, body: dict) -> dict:
    """Detect what a URL is and propose an adapter + config (add-source flow)."""
    from dataclasses import asdict

    from toast.core.fetch import BlockedURLError, FetchError
    from toast.sources.probe import probe_url

    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "url required")
    try:
        return asdict(probe_url(url))
    except BlockedURLError as exc:
        raise HTTPException(400, f"URL blocked: {exc}") from exc
    except FetchError as exc:
        raise HTTPException(502, f"could not fetch URL: {exc}") from exc


@router.post("/sources")
def create_source(request: Request, body: dict) -> dict:
    """Create a source from a probe result. Only known adapters are accepted."""
    import json as _json

    from toast.core.db import utcnow
    from toast.core.ids import ulid
    from toast.sources import get_adapter

    adapter = (body.get("adapter") or "").strip()
    name = (body.get("name") or "").strip()
    config = body.get("config") or {}
    tier = int(body.get("tier", 5))
    enabled = 1 if body.get("enabled", True) else 0
    schedule = body.get("schedule", "600")
    if not adapter or get_adapter(adapter) is None:
        raise HTTPException(400, f"unknown adapter {adapter!r}")
    if not name:
        raise HTTPException(400, "name required")
    if not (1 <= tier <= 5):
        raise HTTPException(400, "tier must be 1-5")

    conn = _conn(request)
    source_id = ulid()
    now = utcnow()
    conn.execute(
        """INSERT INTO sources (id, name, type, adapter, tier, config, schedule, enabled,
                                tos_notes, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            source_id,
            name,
            body.get("type", "scrape"),
            adapter,
            tier,
            _json.dumps(config),
            schedule,
            enabled,
            body.get("tos_notes", "Operator-added via URL probe."),
            now,
            now,
        ),
    )
    return {"ok": True, "source_id": source_id, "name": name, "adapter": adapter}


@router.get("/sources/suggestions")
def source_suggestions(request: Request) -> dict:
    """Cross-reference: rescan dossiers/article links and list unknown domains
    awaiting the operator's APPROVE/DISMISS."""
    from toast.pipeline.source_discovery import pending_suggestions, scan_for_suggestions

    conn = _conn(request)
    scan_for_suggestions(conn)
    return {"suggestions": pending_suggestions(conn)}


@router.post("/sources/suggestions/{suggestion_id}/approve")
def approve_suggestion(request: Request, suggestion_id: str, body: dict | None = None) -> dict:
    """Approve a suggested domain: discover its feed, add it as a real source.
    Fails (suggestion stays pending) when no feed can be found."""
    import json as _json

    from toast.core.db import utcnow
    from toast.core.ids import ulid
    from toast.sources.probe import discover_feed

    conn = _conn(request)
    row = conn.execute("SELECT * FROM source_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "suggestion not found")
    if row["status"] != "pending":
        raise HTTPException(409, f"suggestion already {row['status']}")

    tier = int((body or {}).get("tier", 4))
    if not (3 <= tier <= 5):
        raise HTTPException(400, "suggested sources start at tier 3-5")
    feed = discover_feed(f"https://{row['domain']}/")
    if feed is None:
        raise HTTPException(
            422,
            f"no RSS/Atom feed found on {row['domain']} — "
            "add it manually with a specific feed URL if you know one",
        )

    source_id = ulid()
    now = utcnow()
    conn.execute(
        """INSERT INTO sources (id, name, type, adapter, tier, config, schedule, enabled,
                                tos_notes, created_at, updated_at)
           VALUES (?, ?, 'scrape', 'rss', ?, ?, '900', 1, ?, ?, ?)""",
        (
            source_id,
            row["domain"],
            tier,
            _json.dumps({"feed_url": feed, "keywords": "fire", "max_entries": 10}),
            "Added via cross-reference suggestion (operator-approved).",
            now,
            now,
        ),
    )
    conn.execute(
        "UPDATE source_suggestions SET status = 'approved', source_id = ? WHERE id = ?",
        (source_id, suggestion_id),
    )
    return {"ok": True, "source_id": source_id, "feed_url": feed, "domain": row["domain"]}


@router.post("/sources/suggestions/{suggestion_id}/dismiss")
def dismiss_suggestion(request: Request, suggestion_id: str) -> dict:
    conn = _conn(request)
    cur = conn.execute(
        "UPDATE source_suggestions SET status = 'dismissed' WHERE id = ? AND status = 'pending'",
        (suggestion_id,),
    )
    if cur.rowcount == 0:
        raise HTTPException(404, "no pending suggestion with that id")
    return {"ok": True}


@router.get("/sources")
def list_sources(request: Request) -> dict:
    rows = (
        _conn(request)
        .execute(
            """SELECT s.id, s.name, s.type, s.adapter, s.tier, s.schedule, s.enabled,
                  s.tos_notes, s.cursor,
                  COUNT(f.id) AS fact_count, MAX(f.ingested_at) AS last_fact_at,
                  st.lead_time_median_s, st.superseded_rate, st.yield_facts_per_day,
                  st.suggested_tier, st.computed_at AS stats_computed_at
           FROM sources s
           LEFT JOIN facts f ON f.source_id = s.id
           LEFT JOIN source_stats st ON st.source_id = s.id
           GROUP BY s.id ORDER BY s.tier, s.name"""
        )
        .fetchall()
    )
    return {"sources": [dict(r) for r in rows]}


@router.post("/sources/{source_id}/tier")
def set_source_tier(request: Request, source_id: str, body: dict) -> dict:
    """Operator approves a tier change. Past facts keep the tier they were
    written with — a promotion never rewrites history."""
    from toast.pipeline.trust import apply_tier

    try:
        return apply_tier(_conn(request), source_id, int(body.get("tier", 0)))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/sources/{source_id}/tier/dismiss")
def dismiss_tier_suggestion(request: Request, source_id: str) -> dict:
    from toast.pipeline.trust import dismiss_suggestion

    dismiss_suggestion(_conn(request), source_id)
    return {"ok": True}


@router.post("/facts/reassess")
def reassess_facts(request: Request) -> dict:
    """Recompute corroboration for every embedded fact under the current rules.
    fact_assessments is derived — rebuilding it never touches the ledger."""
    from toast.queue import jobs

    job_id = jobs.enqueue(_conn(request), "reassess", {}, priority=8)
    return {"ok": True, "job_id": job_id}


@router.post("/sources/stats/recompute")
def recompute_source_stats(request: Request) -> dict:
    """Run the trust loop now instead of waiting for the nightly job."""
    from toast.pipeline.trust import compute_source_stats

    stats = compute_source_stats(_conn(request))
    return {
        "computed": len(stats),
        "suggestions": [s for s in stats if s["suggested_tier"] is not None],
    }
