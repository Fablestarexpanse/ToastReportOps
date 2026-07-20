"""Draft generation — turn an incident's ledger facts into a review-ready report.

The AI drafts; nothing publishes without operator approval (enforced by the
draft state-machine trigger). Evacuation facts are passed to the model with an
explicit verbatim contract and are also available to render untouched.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from toast.core.db import utcnow
from toast.core.ids import ulid
from toast.llm.client import LLMClient
from toast.llm.prompts import load_prompt


def gather_facts(
    conn: sqlite3.Connection,
    incident_id: str,
    *,
    window_start: str | None = None,
    window_end: str | None = None,
) -> list[sqlite3.Row]:
    """Non-superseded facts for an incident within an observed_at window,
    oldest first. Superseded facts are excluded — the ledger's current view."""
    clauses = [
        "incident_id = ?",
        "id NOT IN (SELECT supersedes FROM facts WHERE supersedes IS NOT NULL)",
    ]
    params: list[Any] = [incident_id]
    if window_start:
        clauses.append("observed_at >= ?")
        params.append(window_start)
    if window_end:
        clauses.append("observed_at <= ?")
        params.append(window_end)
    return conn.execute(
        f"SELECT * FROM facts WHERE {' AND '.join(clauses)} ORDER BY observed_at ASC",
        params,
    ).fetchall()


def _facts_block(facts: list[sqlite3.Row]) -> tuple[str, dict[str, str]]:
    """Render facts as a numbered block for the prompt; return (text, id_map)
    where id_map maps the short number → real fact id."""
    lines = []
    id_map: dict[str, str] = {}
    for i, f in enumerate(facts, start=1):
        id_map[str(i)] = f["id"]
        tag = f"[#{i}]"
        kind = f["kind"]
        verbatim = " (EVACUATION — reproduce verbatim)" if kind == "evac" else ""
        lines.append(
            f"{tag} kind={kind}{verbatim} tier=T{f['tier']} "
            f"observed={f['observed_at']}: {f['content']}"
        )
    return "\n".join(lines), id_map


def generate_draft(
    conn: sqlite3.Connection,
    incident_id: str,
    *,
    window_start: str | None = None,
    window_end: str | None = None,
    client: LLMClient | None = None,
) -> dict[str, Any]:
    """Generate a draft update for an incident. Inserts a 'draft' row and
    returns a summary. Raises if the incident has no facts in range."""
    incident = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    if incident is None:
        raise ValueError("incident not found")
    facts = gather_facts(conn, incident_id, window_start=window_start, window_end=window_end)
    if not facts:
        return {"created": False, "reason": "no facts in window"}

    facts_text, id_map = _facts_block(facts)
    prompt = load_prompt("draft_update")
    window_desc = f"{window_start or 'incident start'} to {window_end or 'now'}"
    rendered = prompt.render(
        incident=f"{incident['canonical_name']} ({incident['county'] or '?'} County, "
        f"status {incident['status']})",
        window=window_desc,
        facts=facts_text,
    )
    client = client or LLMClient()
    raw = client.chat_json([{"role": "system", "content": rendered}])

    body_md = str(raw.get("body_md", "")).strip()
    title = str(raw.get("title") or f"{incident['canonical_name']} update").strip()
    # translate the model's short [#n] ids back to real fact ids
    cited_short = [str(c) for c in raw.get("cited_fact_ids", [])]
    cited_real = [id_map[c] for c in cited_short if c in id_map]

    draft_id = ulid()
    now = utcnow()
    conn.execute(
        """INSERT INTO drafts (id, incident_id, kind, status, title, window_start, window_end,
                               body_md, cited_fact_ids, created_at, updated_at)
           VALUES (?, ?, 'update', 'draft', ?, ?, ?, ?, ?, ?, ?)""",
        (
            draft_id,
            incident_id,
            title,
            window_start,
            window_end,
            body_md,
            json.dumps(cited_real),
            now,
            now,
        ),
    )
    return {
        "created": True,
        "draft_id": draft_id,
        "title": title,
        "facts_used": len(facts),
        "cited": len(cited_real),
        "body_md": body_md,
    }
