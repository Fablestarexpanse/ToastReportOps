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
        AS corroborating_sources"""


@router.get("/incidents")
def list_incidents(request: Request, status: str | None = None, include_rx: bool = False) -> dict:
    clauses, params = [], []
    if status:
        clauses.append("i.status = ?")
        params.append(status)
    if not include_rx:
        clauses.append("COALESCE(i.incident_type, 'WF') != 'RX'")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = (
        _conn(request)
        .execute(
            f"""SELECT i.*, COUNT(f.id) AS fact_count, MAX(f.observed_at) AS last_observed
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
            f"""SELECT {FACT_COLS}, i.canonical_name AS incident_name
            FROM facts f JOIN sources s ON s.id = f.source_id
            LEFT JOIN incidents i ON i.id = f.incident_id
            WHERE f.tier <= ?
            ORDER BY f.ingested_at DESC LIMIT ?""",
            (min_tier, limit),
        )
        .fetchall()
    )
    return {"facts": [_fact_dict(r) for r in rows]}


@router.get("/adjudication")
def adjudication_queue(request: Request, limit: int = Query(50, le=200)) -> dict:
    """Unattributed non-official facts awaiting operator attribution, with
    scored incident candidates for one-click resolve."""
    from toast.pipeline.attribution import REVIEW_THRESHOLD, score_candidates

    conn = _conn(request)
    rows = conn.execute(
        f"""SELECT {FACT_COLS} FROM facts f JOIN sources s ON s.id = f.source_id
            WHERE f.incident_id IS NULL AND f.kind NOT IN ('weather')
            ORDER BY f.ingested_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    items = []
    for row in rows:
        fact = _fact_dict(row)
        structured = fact["structured"]
        candidates = score_candidates(
            conn,
            observed_at=row["observed_at"],
            incident_name=structured.get("incident_name"),
            lat=row["lat"],
            lon=row["lon"],
        )
        items.append(
            {
                "fact": fact,
                "candidates": [
                    {
                        "incident_id": c.incident_id,
                        "incident_name": c.incident_name,
                        "score": round(c.score, 3),
                        "name_score": round(c.name_score, 2),
                        "geo_score": round(c.geo_score, 2),
                        "time_score": round(c.time_score, 2),
                    }
                    for c in candidates
                    if c.score >= REVIEW_THRESHOLD * 0.5
                ],
            }
        )
    return {"queue": items}


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


@router.get("/sources")
def list_sources(request: Request) -> dict:
    rows = (
        _conn(request)
        .execute(
            """SELECT s.id, s.name, s.type, s.adapter, s.tier, s.schedule, s.enabled,
                  s.tos_notes, s.cursor,
                  COUNT(f.id) AS fact_count, MAX(f.ingested_at) AS last_fact_at
           FROM sources s LEFT JOIN facts f ON f.source_id = s.id
           GROUP BY s.id ORDER BY s.tier, s.name"""
        )
        .fetchall()
    )
    return {"sources": [dict(r) for r in rows]}
