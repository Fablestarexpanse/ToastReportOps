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


MIN_ARTICLE_CHARS = 200  # below this, prefer the feed headline+summary


@register("extract")
def extract_handler(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """Extract claims from an article envelope. Prefers the full article page;
    falls back to the RSS entry's title+summary when the page can't be fetched
    (robots-blocked aggregators like Google News, paywalls, errors) so we still
    get headline-level signal to cross-check."""
    import json as _json

    from toast.core.fetch import FetchError, robots_allows
    from toast.pipeline.analyze import extract_article, fetch_article_text, html_to_text

    env_id = payload["envelope_id"]
    env = conn.execute("SELECT * FROM envelopes WHERE id = ?", (env_id,)).fetchone()
    if env is None:
        return
    src = conn.execute(
        "SELECT tier, robots_override FROM sources WHERE id = ?", (env["source_id"],)
    ).fetchone()
    env_payload = _json.loads(env["payload"])
    url = env_payload.get("url")
    title = env_payload.get("title") or ""
    summary_text = env_payload.get("summary") or ""

    raw_ref: str | None = None
    text: str | None = None
    fetched_full = False
    # try the full article page when robots permits
    if url and (src["robots_override"] or robots_allows(url)):
        try:
            raw_ref, text = fetch_article_text(conn, url)
            fetched_full = bool(text and len(text) >= MIN_ARTICLE_CHARS)
        except FetchError:
            text = None

    # fall back to feed headline + summary (strip any HTML in the summary)
    if not fetched_full:
        clean_summary = html_to_text(summary_text.encode()) if "<" in summary_text else summary_text
        headline = f"{title}. {clean_summary}".strip(" .")
        if len(headline) >= 10:
            text = headline
            raw_ref = None  # no page snapshot for headline-only

    if not text:
        get_logger("pipeline.extract").info("extract_no_text", envelope_id=env_id, url=url)
        return

    result = extract_article(
        conn,
        url=url or "feed",
        source_id=env["source_id"],
        tier=src["tier"],
        envelope_id=env_id,
        raw_ref=raw_ref,
        text=text,
    )
    for fact in result["facts"]:
        jobs.enqueue(conn, "embed", {"fact_id": fact["fact_id"]}, priority=7)
    if result.get("skipped") == "not_california":
        get_logger("pipeline.extract").info(
            "extract_skipped_non_ca",
            envelope_id=env_id,
            url=url,
            location=result.get("primary_location"),
        )
    else:
        get_logger("pipeline.extract").info(
            "extract_done",
            envelope_id=env_id,
            url=url,
            facts=len(result["facts"]),
            mode="full" if fetched_full else "headline",
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


@register("build_site")
def build_site_handler(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """Regenerate the static public site from published reports."""
    from toast.core.config import Settings
    from toast.sitegen.build import build_site

    counts = build_site(conn, Settings().site_dir)
    get_logger("sitegen").info("site_built", **counts)


def _requeue_rate_limited(conn: sqlite3.Connection, stage: str, payload: dict[str, Any]) -> None:
    """LDR's login limiter punishes fast retries — retrying through the normal
    backoff makes it WORSE (each attempt is another login). Park the job for
    10 minutes instead."""
    from datetime import UTC, datetime, timedelta

    hops = int(payload.get("rl_hops", 0)) + 1
    if hops > 6:  # give up after ~an hour of limiter
        raise RuntimeError("LDR rate limiter persisted > 1h")
    run_after = (datetime.now(UTC) + timedelta(seconds=600)).strftime("%Y-%m-%dT%H:%M:%S.%f")[
        :-3
    ] + "Z"
    jobs.enqueue(conn, stage, {**payload, "rl_hops": hops}, priority=8, run_after=run_after)
    get_logger("research").info("research_parked_rate_limited", stage=stage, hop=hops)


@register("deep_research")
def deep_research_handler(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """Kick off an LDR research run, then hand off to the poll stage.

    Split start/poll so a multi-minute research run never blocks the single
    worker — between polls the worker keeps processing live extraction."""
    from toast.pipeline.research import ResearchError, get_client

    client = get_client()
    try:
        client.login_or_register()
    except ResearchError as exc:
        if "rate-limited" in str(exc):
            _requeue_rate_limited(conn, "deep_research", payload)
            return
        raise
    research_id = client.start(payload["query"])
    jobs.enqueue(
        conn,
        "deep_research_poll",
        {
            "research_id": research_id,
            "query": payload["query"],
            "incident_id": payload.get("incident_id"),
        },
        priority=8,  # low: live pipeline preempts research polling
        max_attempts=40,  # polls ~every 30s via backoffless requeue below
    )
    get_logger("research").info("research_started", research_id=research_id, query=payload["query"])


@register("deep_research_poll")
def deep_research_poll_handler(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """Poll one research run; requeue while in progress, land a draft when done."""
    from datetime import UTC, datetime, timedelta

    from toast.pipeline.research import get_client, land_analysis_draft

    client = get_client()
    client.login_or_register()
    status = client.status(payload["research_id"])
    state = str(status.get("status", "")).lower()
    log = get_logger("research")
    if state in ("completed", "success", "done"):
        report = client.report(payload["research_id"])
        draft_id = land_analysis_draft(
            conn,
            query=payload["query"],
            report=report,
            incident_id=payload.get("incident_id"),
        )
        log.info("research_landed", research_id=payload["research_id"], draft_id=draft_id)
        return
    if state in ("failed", "error", "cancelled"):
        raise RuntimeError(f"research failed: {status.get('error', state)}")
    # still running — requeue in ~30s. Hop cap so a hung run can't poll forever.
    hops = int(payload.get("hops", 0)) + 1
    if hops > 60:  # ~30 min ceiling
        raise RuntimeError(f"research timed out after {hops} polls")
    run_after = (datetime.now(UTC) + timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%S.%f")[
        :-3
    ] + "Z"
    jobs.enqueue(
        conn, "deep_research_poll", {**payload, "hops": hops}, priority=8, run_after=run_after
    )
    log.info(
        "research_in_progress",
        research_id=payload["research_id"],
        progress=status.get("progress"),
    )
