"""Social rewrite — second writing layer for the newsroom.

Takes a factual (ideally approved) report draft and produces a human-voiced
version for posting: a full post (FB/Bluesky length) and a short one (X).
The rewrite is faithful by contract — the model may only restate what the
source report says; evacuation text stays verbatim.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from toast.core.db import utcnow
from toast.core.ids import ulid
from toast.llm.client import LLMClient, LLMError, client_for
from toast.llm.prompts import load_prompt


def generate_social(
    conn: sqlite3.Connection, draft_id: str, *, client: LLMClient | None = None
) -> dict[str, Any]:
    """Generate a kind='social' draft from an existing report draft."""
    src = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if src is None:
        raise ValueError("draft not found")
    if not (src["body_md"] or "").strip():
        raise ValueError("source draft is empty")

    prompt = load_prompt("rewrite_social")
    rendered = prompt.render(report=src["body_md"])
    client = client or client_for(conn)
    raw = client.chat_json([{"role": "system", "content": rendered}], temperature=0.4)

    post_text = str(raw.get("post_text", "")).strip()
    short_text = str(raw.get("short_text", "")).strip()
    if not post_text:
        raise LLMError("social rewrite returned no post_text")

    body = post_text
    if short_text:
        body += f"\n\n---\n\n**Short version (X/Twitter, {len(short_text)} chars)**\n\n{short_text}"

    social_id = ulid()
    now = utcnow()
    base_title = (src["title"] or "update").removeprefix("RESEARCH — ")
    conn.execute(
        """INSERT INTO drafts (id, incident_id, kind, status, title, body_md,
                               cited_fact_ids, parent_id, created_at, updated_at)
           VALUES (?, ?, 'social', 'draft', ?, ?, ?, ?, ?, ?)""",
        (
            social_id,
            src["incident_id"],
            f"SOCIAL — {base_title[:70]}",
            body,
            src["cited_fact_ids"] or "[]",  # provenance carries through
            draft_id,
            now,
            now,
        ),
    )
    return {
        "draft_id": social_id,
        "parent_id": draft_id,
        "post_chars": len(post_text),
        "short_chars": len(short_text),
    }
