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
    (SELECT id FROM facts sup WHERE sup.supersedes = f.id) AS superseded_by"""


@router.get("/incidents")
def list_incidents(request: Request, status: str | None = None) -> dict:
    where, params = "", []
    if status:
        where = "WHERE i.status = ?"
        params.append(status)
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
