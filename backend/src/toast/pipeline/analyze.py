"""Point-at-URL analysis: fetch → extract text → LLM claims → facts → attribution.

The Phase 4 newsroom builds UI on this; Phase 2 ships the logic and a plain
API endpoint. Facts land under the 'manual-url' T4 source; each claim is
auto-attributed or left for adjudication.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from bs4 import BeautifulSoup

from toast.core.config import Settings
from toast.core.db import utcnow
from toast.core.envelope import Envelope, insert_envelope
from toast.core.fetch import fetch
from toast.core.ids import ulid
from toast.core.rawstore import store_raw
from toast.llm.client import LLMClient
from toast.llm.extract import extract_claims
from toast.pipeline.attribution import attribute_fact
from toast.pipeline.confidence import initial_confidence
from toast.pipeline.ledger import write_fact

MANUAL_SOURCE_NAME = "Operator: point-at-URL"
MAX_TEXT_CHARS = 24_000


def ensure_manual_source(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT id FROM sources WHERE adapter = 'manual_url'").fetchone()
    if row:
        return row["id"]
    source_id = ulid()
    now = utcnow()
    conn.execute(
        """INSERT INTO sources (id, name, type, adapter, tier, config, enabled,
                                tos_notes, created_at, updated_at)
           VALUES (?, ?, 'scrape', 'manual_url', 4, '{}', 1,
                   'Operator-initiated single-URL analysis.', ?, ?)""",
        (source_id, MANUAL_SOURCE_NAME, now, now),
    )
    return source_id


def html_to_text(body: bytes) -> str:
    soup = BeautifulSoup(body, "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "iframe"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ").split())
    return text[:MAX_TEXT_CHARS]


def analyze_url(
    conn: sqlite3.Connection, url: str, *, client: LLMClient | None = None
) -> dict[str, Any]:
    """Full analysis of one URL. Returns a summary for the operator."""
    settings = Settings()
    source_id = ensure_manual_source(conn)

    body, ctype = fetch(url, accept_types=("text/html", "application/xhtml+xml", "text/plain"))
    raw_ref = store_raw(conn, settings.toast_data_dir, body, ctype, url)
    text = (
        html_to_text(body) if "html" in ctype else body.decode("utf-8", "replace")[:MAX_TEXT_CHARS]
    )

    now = utcnow()
    result = extract_claims(text, reference_time=now, source_name=url, client=client)

    env_id = insert_envelope(
        conn,
        Envelope(
            source_id=source_id,
            external_id=url,
            kind="article",
            payload={"url": url, "chars": len(text), "claims": len(result.claims)},
            raw_ref=raw_ref,
            url=url,
        ),
    )
    if env_id is None:  # identical article content re-analyzed — still report
        env_id = conn.execute(
            "SELECT id FROM envelopes WHERE source_id=? AND external_id=? ORDER BY fetched_at DESC",
            (source_id, url),
        ).fetchone()["id"]

    summary: dict[str, Any] = {
        "url": url,
        "claims": len(result.claims),
        "dropped": result.dropped,
        "facts": [],
        "prompt_version": result.prompt_version,
    }
    for claim in result.claims:
        confidence = initial_confidence(4, claim.confidence)
        fact_id = write_fact(
            conn,
            incident_id=None,
            envelope_id=env_id,
            source_id=source_id,
            tier=4,
            content=claim.content,
            kind=claim.kind,
            observed_at=claim.observed_at or now,
            confidence=confidence,
            acreage=claim.acreage,
            containment_pct=claim.containment_pct,
            evac_status=claim.evac_status,
            structured={
                "incident_name": claim.incident_name,
                "location_text": claim.location_text,
                "extractor_confidence": claim.confidence,
            },
            raw_ref=raw_ref,
            prompt_version=result.prompt_version,
        )
        outcome, candidates = attribute_fact(conn, fact_id)
        summary["facts"].append(
            {
                "fact_id": fact_id,
                "content": claim.content,
                "kind": claim.kind,
                "observed_at": claim.observed_at,
                "confidence": confidence,
                "attribution": outcome,
                "top_candidate": (
                    {
                        "incident": candidates[0].incident_name,
                        "score": round(candidates[0].score, 2),
                    }
                    if candidates
                    else None
                ),
            }
        )
    return summary
