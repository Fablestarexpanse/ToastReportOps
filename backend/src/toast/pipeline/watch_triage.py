"""Watch-board dispositions + the "is this new or old?" age check.

The board clusters unattributed named-fire signals, so plenty of noise lands on
it: anniversary pieces, lawsuit coverage, out-of-state fires, prescribed burns.
The operator needs to clear those without losing the underlying facts, and a
cheap way to ask "is this actually burning right now?" before deciding.

The age check runs the LOCAL model over the cluster's own excerpts — no web
research needed to tell an anniversary retrospective from a live fire. It only
ever suggests; the operator still presses the button.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from toast.core.db import utcnow
from toast.llm.client import LLMClient, LLMError, client_for
from toast.llm.prompts import load_prompt

VALID_STATES = ("ignored", "archived", "keep")
VALID_VERDICTS = ("active_new", "historical", "not_a_fire", "uncertain")
# what each verdict implies — applied in code, not left to the model
VERDICT_ACTION = {
    "active_new": "keep",
    "historical": "archived",
    "not_a_fire": "ignored",
    "uncertain": "keep",  # never auto-dismiss something we couldn't judge
}
MAX_EXCERPTS = 8
MAX_EXCERPT_CHARS = 400


def set_state(
    conn: sqlite3.Connection,
    cluster_key: str,
    state: str,
    *,
    note: str | None = None,
    verdict: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if state not in VALID_STATES:
        raise ValueError(f"state must be one of {VALID_STATES}")
    conn.execute(
        """INSERT INTO watch_triage (cluster_key, state, verdict, note, at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(cluster_key) DO UPDATE SET
             state = excluded.state,
             verdict = COALESCE(excluded.verdict, watch_triage.verdict),
             note = COALESCE(excluded.note, watch_triage.note),
             at = excluded.at""",
        (
            cluster_key,
            state,
            json.dumps(verdict) if verdict else None,
            (note or "")[:500] or None,
            utcnow(),
        ),
    )
    return {"cluster_key": cluster_key, "state": state}


def clear_state(conn: sqlite3.Connection, cluster_key: str) -> None:
    """Put a candidate back on the board — nothing is ever destroyed."""
    conn.execute("DELETE FROM watch_triage WHERE cluster_key = ?", (cluster_key,))


def triage_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in conn.execute("SELECT cluster_key, state, verdict, at FROM watch_triage"):
        try:
            verdict = json.loads(r["verdict"]) if r["verdict"] else None
        except json.JSONDecodeError:
            verdict = None
        out[r["cluster_key"]] = {"state": r["state"], "verdict": verdict, "at": r["at"]}
    return out


def archived_candidates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Filed-away candidates, for the ARCHIVE view."""
    rows = conn.execute(
        """SELECT cluster_key, state, verdict, note, at FROM watch_triage
           WHERE state = 'archived' ORDER BY at DESC"""
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["verdict"] = json.loads(d["verdict"]) if d["verdict"] else None
        except json.JSONDecodeError:
            d["verdict"] = None
        out.append(d)
    return out


def classify_candidate(
    conn: sqlite3.Connection, cluster_key: str, *, client: LLMClient | None = None
) -> dict[str, Any]:
    """Ask the local model whether this candidate is burning now or is old news."""
    from toast.pipeline.watch import build_watchlist

    candidate = next(
        (c for c in build_watchlist(conn, include_triaged=True) if c.key == cluster_key), None
    )
    if candidate is None:
        raise ValueError("candidate not found")

    facts = conn.execute(
        f"""SELECT f.content, f.observed_at, s.name AS source_name
            FROM facts f JOIN sources s ON s.id = f.source_id
            WHERE f.id IN ({",".join("?" * len(candidate.fact_ids))})
            ORDER BY f.observed_at DESC LIMIT {MAX_EXCERPTS}""",
        candidate.fact_ids,
    ).fetchall()
    # neutralize delimiter forgery: excerpts may not fake the block markers
    excerpts = "\n\n".join(
        f"[{r['source_name']} · {r['observed_at']}] "
        f"{r['content'][:MAX_EXCERPT_CHARS].replace('=====', '= = = =')}"
        for r in facts
    )

    prompt = load_prompt("classify_watch", conn=conn)
    rendered = prompt.render(
        today=utcnow(),
        name=candidate.name.replace("=====", "= = = ="),
        location=str(candidate.county or "unknown"),
        latest_observed=candidate.latest_observed,
        excerpts=excerpts,
    )
    client = client or client_for(conn)
    raw = client.chat_json([{"role": "system", "content": rendered}], temperature=0.1)

    verdict = str(raw.get("verdict", "")).strip()
    if verdict not in VALID_VERDICTS:
        raise LLMError(f"invalid verdict: {verdict!r}")
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.0
    # The verdict→action mapping is a fixed rule, so derive it rather than
    # trusting the model to apply it (qwen3 returned "keep" for a historical
    # verdict in testing). A fire outside California is never ours to track.
    action = "ignored" if raw.get("in_california") is False else VERDICT_ACTION[verdict]
    result = {
        "verdict": verdict,
        "confidence": round(confidence, 2),
        "reasoning": str(raw.get("reasoning", ""))[:600],
        "evidence_quote": str(raw.get("evidence_quote", ""))[:300],
        "in_california": raw.get("in_california"),
        "suggested_action": action,
        "checked_at": utcnow(),
        "prompt_version": prompt.prompt_version,
    }
    # record the verdict without acting on it — the operator decides
    existing = conn.execute(
        "SELECT state FROM watch_triage WHERE cluster_key = ?", (cluster_key,)
    ).fetchone()
    set_state(conn, cluster_key, existing["state"] if existing else "keep", verdict=result)
    return result
