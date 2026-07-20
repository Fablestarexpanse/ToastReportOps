"""Pipeline job handlers: poll (adapter fetch → envelopes) and normalize
(envelope → incidents + facts)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from toast.core.config import Settings
from toast.core.envelope import insert_envelope
from toast.core.logging import get_logger
from toast.core.rawstore import store_raw
from toast.queue import jobs
from toast.queue.handlers import register
from toast.sources import get_adapter, load_builtin_adapters

load_builtin_adapters()


@register("poll")
def poll_handler(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    log = get_logger("pipeline.poll")
    settings = Settings()
    source = conn.execute("SELECT * FROM sources WHERE id = ?", (payload["source_id"],)).fetchone()
    if source is None:
        raise RuntimeError(f"source {payload['source_id']} not found")
    if not source["enabled"]:
        log.info("poll_skipped_disabled", source_id=source["id"])
        return
    adapter = get_adapter(source["adapter"])
    if adapter is None:
        raise RuntimeError(f"no adapter {source['adapter']!r}")

    config = json.loads(source["config"])
    config["source_id"] = source["id"]
    envelopes, next_cursor = adapter.poll(config, source["cursor"])

    # preserve raw responses first; single-response polls stamp every envelope
    shas = [
        store_raw(conn, settings.toast_data_dir, body, ctype, url)
        for body, ctype, url in getattr(adapter, "fetches", [])
    ]
    default_raw = shas[0] if len(shas) == 1 else None

    new = 0
    for env in envelopes:
        env.raw_ref = env.raw_ref or default_raw
        env_id = insert_envelope(conn, env)
        if env_id:
            jobs.enqueue(conn, "normalize", {"envelope_id": env_id})
            new += 1
    conn.execute("UPDATE sources SET cursor = ? WHERE id = ?", (next_cursor, source["id"]))
    log.info(
        "poll_done",
        source=source["name"],
        fetched=len(envelopes),
        new=new,
        cursor=next_cursor,
    )


@register("normalize")
def normalize_handler(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    from toast.pipeline.normalize import normalize_envelope

    env_id = payload["envelope_id"]
    kind = conn.execute("SELECT kind FROM envelopes WHERE id = ?", (env_id,)).fetchone()["kind"]
    # Articles need the LLM extractor — route to its own retryable stage instead
    # of doing model work on the normalize path.
    if kind == "article":
        jobs.enqueue(conn, "extract", {"envelope_id": env_id}, priority=6)
        return
    facts = normalize_envelope(conn, env_id)
    for fact_id in facts:
        jobs.enqueue(conn, "embed", {"fact_id": fact_id}, priority=7)
    get_logger("pipeline.normalize").info("normalize_done", envelope_id=env_id, facts=len(facts))


@register("extract")
def extract_handler(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """Fetch an article envelope's page and extract claims into facts (LLM)."""
    import json as _json

    from toast.core.fetch import robots_allows
    from toast.pipeline.analyze import extract_article, fetch_article_text

    env_id = payload["envelope_id"]
    env = conn.execute("SELECT * FROM envelopes WHERE id = ?", (env_id,)).fetchone()
    if env is None:
        return
    src = conn.execute(
        "SELECT tier, robots_override FROM sources WHERE id = ?", (env["source_id"],)
    ).fetchone()
    url = _json.loads(env["payload"]).get("url")
    if not url:
        return
    # robots disallow is permanent — skip (done), don't retry/dead-letter.
    if not src["robots_override"] and not robots_allows(url):
        get_logger("pipeline.extract").info("extract_skipped_robots", envelope_id=env_id, url=url)
        return

    raw_ref, text = fetch_article_text(conn, url)
    summary = extract_article(
        conn,
        url=url,
        source_id=env["source_id"],
        tier=src["tier"],
        envelope_id=env_id,
        raw_ref=raw_ref,
        text=text,
    )
    for fact in summary["facts"]:
        jobs.enqueue(conn, "embed", {"fact_id": fact["fact_id"]}, priority=7)
    if summary.get("skipped") == "not_california":
        get_logger("pipeline.extract").info(
            "extract_skipped_non_ca",
            envelope_id=env_id,
            url=url,
            location=summary.get("primary_location"),
        )
    else:
        get_logger("pipeline.extract").info(
            "extract_done", envelope_id=env_id, url=url, facts=len(summary["facts"])
        )


@register("embed")
def embed_handler(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """Embed a fact's content, then assess corroboration/duplication."""
    import json as _json

    from toast.core.db import utcnow
    from toast.llm.client import LLMClient
    from toast.pipeline.confidence import corroborated_confidence
    from toast.pipeline.embeddings import (
        corroborate,
        ensure_vec_tables,
        is_near_duplicate,
        store_fact_embedding,
    )

    fact = conn.execute("SELECT * FROM facts WHERE id = ?", (payload["fact_id"],)).fetchone()
    if fact is None:
        return
    if not ensure_vec_tables(conn):
        get_logger("pipeline.embed").warning("sqlite_vec_unavailable_skipping")
        return
    vec = LLMClient().embed([fact["content"]])[0]
    store_fact_embedding(conn, fact["id"], vec)

    sources_n, corroborating_ids = corroborate(conn, fact["id"], vec)
    duplicate_of = is_near_duplicate(conn, fact["id"], vec, fact["source_id"])
    conn.execute(
        """INSERT INTO fact_assessments
           (fact_id, corroborating_sources, corroborated_by, duplicate_of,
            effective_confidence, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(fact_id) DO UPDATE SET
             corroborating_sources = excluded.corroborating_sources,
             corroborated_by = excluded.corroborated_by,
             duplicate_of = excluded.duplicate_of,
             effective_confidence = excluded.effective_confidence,
             updated_at = excluded.updated_at""",
        (
            fact["id"],
            sources_n,
            _json.dumps(corroborating_ids),
            duplicate_of,
            corroborated_confidence(fact["confidence"], sources_n),
            utcnow(),
        ),
    )
    get_logger("pipeline.embed").info(
        "embed_done",
        fact_id=fact["id"],
        corroborating=sources_n,
        duplicate=bool(duplicate_of),
    )
