"""AI triage for the adjudication queue: the model proposes, the human decides.

The queue is repetitive-judgment work — exactly what an LLM should draft and
exactly what it must not decide. Each proposal is stored beside the fact and
rendered as a one-click suggestion in the UI; accepting flows through the SAME
attach/triage endpoints a manual decision uses, so the audit trail cannot tell
the difference and the append-only rules bind identically.

Guardrails:
- An "attach" incident_id is only accepted if it appears in the candidate list
  WE computed — a model-invented id is discarded as 'unsure', not trusted.
- The claim text goes into the prompt as fenced untrusted data (the standing
  injection posture); nothing from the model's output is executed or fetched.
- LLM outage skips the batch quietly; the queue just stays manual.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from toast.core.db import utcnow
from toast.core.logging import get_logger
from toast.llm.client import LLMClient, LLMError
from toast.llm.prompts import load_prompt

BATCH = 12  # local models are slow; keep a batch inside one job heartbeat


def _queue_rows(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT f.id, f.content, f.kind, f.observed_at, f.lat, f.lon, f.structured
           FROM facts f JOIN sources s ON s.id = f.source_id
           WHERE f.incident_id IS NULL AND f.kind NOT IN ('weather')
             AND s.type != 'satellite'
             AND f.id NOT IN (SELECT fact_id FROM fact_triage)
             AND f.id NOT IN (SELECT fact_id FROM triage_proposals)
             AND NOT EXISTS (
               SELECT 1 FROM facts f2 JOIN fact_triage t ON t.fact_id = f2.id
               WHERE f2.source_id = f.source_id AND f2.kind = f.kind
                 AND f2.content = f.content)
           ORDER BY f.ingested_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()


def propose_batch(
    conn: sqlite3.Connection, *, limit: int = BATCH, client: LLMClient | None = None
) -> dict[str, Any]:
    from toast.pipeline.attribution import score_candidates

    rows = _queue_rows(conn, limit)
    if not rows:
        return {"proposed": 0, "note": "queue has no unproposed items"}
    llm = client or LLMClient()
    prompt = load_prompt("autotriage", conn=conn)
    log = get_logger("autotriage")
    proposed = failed = 0

    for row in rows:
        structured = json.loads(row["structured"] or "{}")
        candidates = score_candidates(
            conn,
            observed_at=row["observed_at"],
            incident_name=structured.get("incident_name"),
            lat=row["lat"],
            lon=row["lon"],
        )
        cand_lines = [
            {
                "incident_id": c.incident_id,
                "name": c.incident_name,
                "name_score": round(c.name_score, 2),
                "geo_score": round(c.geo_score, 2),
                "time_score": round(c.time_score, 2),
            }
            for c in candidates[:6]
        ]
        rendered = prompt.render(
            claim=(row["content"] or "")[:1200],
            observed_at=row["observed_at"] or "unknown",
            kind=row["kind"] or "other",
            candidates=json.dumps(cand_lines, indent=1) if cand_lines else "(none)",
        )
        try:
            out = llm.chat_json([{"role": "system", "content": rendered}])
        except LLMError as exc:
            log.warning("autotriage_llm_failed", error=str(exc)[:120])
            failed += 1
            continue

        action = str(out.get("action") or "unsure")
        incident_id = out.get("incident_id")
        valid_ids = {c["incident_id"] for c in cand_lines}
        if action not in ("attach", "watch", "not_incident", "unsure"):
            action = "unsure"
        if action == "attach" and incident_id not in valid_ids:
            # a model-invented id is never trusted
            action, incident_id = "unsure", None
        if action != "attach":
            incident_id = None
        name = next(
            (c["name"] for c in cand_lines if c["incident_id"] == incident_id), None
        )
        try:
            conf = max(0.0, min(1.0, float(out.get("confidence") or 0)))
        except (TypeError, ValueError):
            conf = 0.0
        conn.execute(
            """INSERT OR REPLACE INTO triage_proposals
               (fact_id, action, incident_id, incident_name, confidence, reasoning,
                prompt_version, proposed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["id"], action, incident_id, name, conf,
                str(out.get("reasoning") or "")[:400], prompt.version, utcnow(),
            ),
        )
        proposed += 1
    conn.commit()
    log.info("autotriage_done", proposed=proposed, failed=failed)
    return {"proposed": proposed, "failed": failed}
