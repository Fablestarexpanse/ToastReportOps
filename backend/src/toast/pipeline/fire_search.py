"""Historical fire search + on-demand import.

Search queries the external CAL FIRE catalog (inactive/by-year) WITHOUT writing
anything, flagging which results are already in the ledger. Import fetches one
fire and runs it through the normal ingest pipeline so it lands as a
`contained` incident with its final facts — no bespoke write path.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from toast.core.config import Settings
from toast.core.db import utcnow
from toast.core.envelope import insert_envelope
from toast.core.ids import ulid
from toast.core.rawstore import store_raw
from toast.queue import jobs
from toast.sources.calfire import CalFireAdapter, fetch_incidents, incident_to_payload

SEARCH_SOURCE_NAME = "CAL FIRE historical (search/import)"


def _year_of(payload: dict[str, Any]) -> str | None:
    started = payload.get("discovered_at")
    return started[:4] if started else None


def search_fires(
    conn: sqlite3.Connection,
    query: str = "",
    *,
    year: int | None = None,
) -> list[dict[str, Any]]:
    """Search the CAL FIRE catalog. Read-only: writes nothing to the ledger.

    Without a year, searches this year's inactive (closed) incidents. `query`
    filters by name/county substring. Each result is flagged already_in_ledger.
    """
    adapter = CalFireAdapter()
    items = fetch_incidents(adapter, inactive=True, year=year)
    q = query.strip().lower()
    known = {
        r["calfire_id"]
        for r in conn.execute("SELECT calfire_id FROM incidents WHERE calfire_id IS NOT NULL")
    }
    results = []
    for item in items:
        env = incident_to_payload(item, "search")
        p = env.payload
        haystack = f"{p.get('name') or ''} {p.get('county') or ''}".lower()
        if q and q not in haystack:
            continue
        results.append(
            {
                "ref_type": "calfire",
                "ref_id": p["ref_id"],
                "name": p["name"],
                "county": p["county"],
                "acreage": p["acreage"],
                "containment_pct": p["containment_pct"],
                "year": _year_of(p),
                "started_at": p["discovered_at"],
                "is_active": p["is_active"],
                "url": p["url"],
                "already_in_ledger": p["ref_id"] in known,
            }
        )
    results.sort(key=lambda r: r["started_at"] or "", reverse=True)
    return results


def _ensure_search_source(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT id FROM sources WHERE adapter = 'calfire' AND name = ?", (SEARCH_SOURCE_NAME,)
    ).fetchone()
    if row:
        return row["id"]
    source_id = ulid()
    now = utcnow()
    conn.execute(
        """INSERT INTO sources (id, name, type, adapter, tier, config, enabled,
                                tos_notes, created_at, updated_at)
           VALUES (?, ?, 'api', 'calfire', 1, '{}', 0,
                   'Operator-initiated historical import.', ?, ?)""",
        (source_id, SEARCH_SOURCE_NAME, now, now),
    )
    return source_id


def import_fire(conn: sqlite3.Connection, ref_id: str) -> dict[str, Any]:
    """Fetch one historical CAL FIRE incident and ingest it via the normal
    pipeline. Returns a summary. Idempotent: re-import is a no-op (dedup)."""
    from toast.pipeline.normalize import normalize_envelope

    settings = Settings()
    adapter = CalFireAdapter()
    # search the closed set for the requested id (may span years — try current)
    items = fetch_incidents(adapter, inactive=True)
    match = next((i for i in items if i.get("UniqueId") == ref_id), None)
    if match is None:
        # also check the active set (a fire may still be active)
        items = fetch_incidents(adapter, inactive=False)
        match = next((i for i in items if i.get("UniqueId") == ref_id), None)
    if match is None:
        return {"imported": False, "reason": "not found in CAL FIRE catalog"}

    source_id = _ensure_search_source(conn)
    env = incident_to_payload(match, source_id)
    raw_ref = None
    if adapter.fetches:
        body, ctype, url = adapter.fetches[-1]
        raw_ref = store_raw(conn, settings.toast_data_dir, body, ctype, url)
    env.raw_ref = raw_ref

    env_id = insert_envelope(conn, env)
    if env_id is None:
        return {"imported": False, "reason": "already in ledger", "ref_id": ref_id}
    facts = normalize_envelope(conn, env_id)
    for fact_id in facts:
        jobs.enqueue(conn, "embed", {"fact_id": fact_id}, priority=7)
    incident = conn.execute(
        "SELECT id, canonical_name, status FROM incidents WHERE calfire_id = ?", (ref_id,)
    ).fetchone()
    return {
        "imported": True,
        "ref_id": ref_id,
        "incident_id": incident["id"] if incident else None,
        "name": incident["canonical_name"] if incident else env.payload["name"],
        "status": incident["status"] if incident else None,
        "facts": len(facts),
    }
