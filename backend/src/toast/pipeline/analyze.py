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
from toast.llm.client import LLMClient, client_for
from toast.llm.extract import extract_claims
from toast.pipeline.attribution import attribute_fact
from toast.pipeline.confidence import initial_confidence
from toast.pipeline.geocode import resolve_fire_name, resolve_place
from toast.pipeline.ledger import write_fact

MANUAL_SOURCE_NAME = "Operator: point-at-URL"
MAX_TEXT_CHARS = 24_000

# California bounding box (lat_min, lat_max, lon_min, lon_max) — a generous
# envelope around the state. Backstop: a fact with coordinates outside this is
# not in California, whatever the extractor decided about the article overall.
CA_BBOX = (32.3, 42.1, -124.6, -114.0)


def _outside_california(lat: float | None, lon: float | None) -> bool:
    if lat is None or lon is None:
        return False  # no coords → can't disprove; article-level gate handles it
    lat_min, lat_max, lon_min, lon_max = CA_BBOX
    return not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max)


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


def extract_article(
    conn: sqlite3.Connection,
    *,
    url: str,
    source_id: str,
    tier: int,
    envelope_id: str,
    raw_ref: str | None,
    text: str,
    client: LLMClient | None = None,
) -> dict[str, Any]:
    """Run the LLM extractor over already-fetched article text and write facts.

    Shared by the operator point-at-URL flow and the RSS article-extract stage.
    Facts are written under the given envelope/source/tier and attributed.
    """
    now = utcnow()
    client = client or client_for(conn)  # operator's model choice
    result = extract_claims(text, reference_time=now, source_name=url, client=client)
    summary: dict[str, Any] = {
        "url": url,
        "claims": len(result.claims),
        "dropped": result.dropped,
        "facts": [],
        "prompt_version": result.prompt_version,
        "california_relevant": result.california_relevant,
        "primary_location": result.primary_location,
    }
    # Article-level California gate: a non-CA article writes no facts (raw stays
    # archived, envelope preserved, so the decision is recoverable). The CA_BBOX
    # backstop (_outside_california) applies to facts that carry coordinates —
    # the future geocode stage — since extracted claims are coordless.
    if not result.california_relevant:
        summary["skipped"] = "not_california"
        return summary

    for claim in result.claims:
        confidence = initial_confidence(tier, claim.confidence)
        # Give the claim coordinates when its location text names a real place.
        # Without this the attribution scorer's geo term (weight 0.35) is dead
        # weight on every media fact. A geocode that can't be pinned
        # unambiguously returns None — a wrong pin is worse than no pin.
        geo = None
        if claim.location_text:
            geo = resolve_place(conn, claim.location_text)
        if geo is None and claim.incident_name:
            geo = resolve_fire_name(conn, claim.incident_name)
        if geo and _outside_california(geo.lat, geo.lon):
            geo = None  # CA_BBOX backstop now has something to check
        fact_id = write_fact(
            conn,
            incident_id=None,
            envelope_id=envelope_id,
            source_id=source_id,
            tier=tier,
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
                **(
                    {
                        "geocoded_to": geo.name,
                        "geocode_class": geo.feature_class,
                        "geocode_confidence": geo.confidence,
                    }
                    if geo
                    else {}
                ),
            },
            lat=geo.lat if geo else None,
            lon=geo.lon if geo else None,
            geo_method=geo.method if geo else None,
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


def fetch_article_text(conn: sqlite3.Connection, url: str) -> tuple[str, str]:
    """Safe-fetch a page and return (raw_ref, text). SSRF/size-guarded."""
    settings = Settings()
    body, ctype = fetch(url, accept_types=("text/html", "application/xhtml+xml", "text/plain"))
    raw_ref = store_raw(conn, settings.toast_data_dir, body, ctype, url)
    text = (
        html_to_text(body) if "html" in ctype else body.decode("utf-8", "replace")[:MAX_TEXT_CHARS]
    )
    return raw_ref, text


def analyze_url(
    conn: sqlite3.Connection, url: str, *, client: LLMClient | None = None
) -> dict[str, Any]:
    """Operator point-at-URL: fetch, extract, write T4 facts, attribute."""
    source_id = ensure_manual_source(conn)
    raw_ref, text = fetch_article_text(conn, url)
    env_id = insert_envelope(
        conn,
        Envelope(
            source_id=source_id,
            external_id=url,
            kind="article",
            payload={"url": url, "chars": len(text)},
            raw_ref=raw_ref,
            url=url,
        ),
    )
    if env_id is None:  # identical content re-analyzed — reuse the prior envelope
        env_id = conn.execute(
            "SELECT id FROM envelopes WHERE source_id=? AND external_id=? ORDER BY fetched_at DESC",
            (source_id, url),
        ).fetchone()["id"]
    return extract_article(
        conn,
        url=url,
        source_id=source_id,
        tier=4,
        envelope_id=env_id,
        raw_ref=raw_ref,
        text=text,
        client=client,
    )
