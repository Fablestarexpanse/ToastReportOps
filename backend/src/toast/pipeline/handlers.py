"""Pipeline job handlers: poll (adapter fetch → envelopes) and normalize
(envelope → incidents + facts)."""

from __future__ import annotations

import json
import sqlite3
import struct
from typing import Any

from toast.core.config import Settings
from toast.core.db import utcnow
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
    # adapters that must check robots.txt themselves need to know whether the
    # operator has explicitly taken that decision (sources.robots_override)
    config["robots_override"] = bool(source["robots_override"])
    # inject an operator-entered credential so keys added in OPTIONS work
    # without editing .env or restarting; never logged (see core/logging.py)
    if key_env := config.get("map_key_env"):
        from toast.core.credentials import get_credential

        if value := get_credential(conn, key_env):
            config["map_key"] = value
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
    # Articles and videos need the LLM extractor — route to its own retryable
    # stage instead of doing model work on the normalize path. A video carries
    # no page to fetch, so extraction runs on its title + description.
    if kind in ("article", "video"):
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
    # try the full article page when robots permits. `text_only` sources (video
    # feeds) never fetch: we extract from what the uploader wrote, nothing else.
    if url and not env_payload.get("text_only") and (src["robots_override"] or robots_allows(url)):
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
    from toast.llm.client import client_for
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
    vec = client_for(conn).embed([fact["content"]])[0]
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


@register("gnis_load")
def gnis_load_handler(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """Load the USGS place-name gazetteer (public domain, no key)."""
    from toast.pipeline.geocode import load_gnis

    load_gnis(conn)


@register("reattribute")
def reattribute_handler(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """Retry attribution on facts that never found their fire.

    Attribution runs once, when a fact is written. Media routinely names a fire
    before CAL FIRE lists it, so a fact can arrive with no incident to match
    and then be orphaned permanently even after the incident appears. This
    sweep gives every unattributed fact another look against the CURRENT
    incident list. It only ever fills in NULL -> value, which is the one
    update the append-only trigger allows."""
    from toast.pipeline.attribution import attribute_fact

    rows = conn.execute(
        """SELECT id FROM facts
           WHERE incident_id IS NULL AND kind != 'weather'
             AND id NOT IN (SELECT fact_id FROM fact_triage)
           ORDER BY ingested_at DESC LIMIT ?""",
        (int(payload.get("limit", 500)),),
    ).fetchall()
    outcomes = {"attributed": 0, "review": 0, "none": 0}
    for row in rows:
        outcome, _ = attribute_fact(conn, row["id"])
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    get_logger("pipeline.reattribute").info(
        "reattributed", examined=len(rows), **{f"n_{k}": v for k, v in outcomes.items()}
    )


@register("reassess")
def reassess_handler(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """Re-run corroboration over already-embedded facts.

    fact_assessments is derived and rebuildable by design; when the
    corroboration rules change, prior assessments are stale until recomputed.
    Embeddings are reused, so this costs no GPU time."""
    import json as _json

    from toast.core.db import utcnow
    from toast.pipeline.confidence import corroborated_confidence
    from toast.pipeline.embeddings import corroborate, ensure_vec_tables, serialize_f32

    if not ensure_vec_tables(conn):
        get_logger("pipeline.reassess").warning("sqlite_vec_unavailable_skipping")
        return
    rows = conn.execute(
        """SELECT f.id, f.confidence, f.source_id, e.embedding
           FROM facts f JOIN fact_embeddings e ON e.fact_id = f.id"""
    ).fetchall()
    changed = 0
    for row in rows:
        vec = list(struct.unpack(f"{len(row['embedding']) // 4}f", row["embedding"]))
        assert serialize_f32(vec) == row["embedding"]  # noqa: S101 - round-trip guard
        sources_n, ids = corroborate(conn, row["id"], vec)
        cur = conn.execute(
            """UPDATE fact_assessments
               SET corroborating_sources = ?, corroborated_by = ?,
                   effective_confidence = ?, updated_at = ?
               WHERE fact_id = ? AND corroborating_sources <> ?""",
            (
                sources_n,
                _json.dumps(ids),
                corroborated_confidence(row["confidence"], sources_n),
                utcnow(),
                row["id"],
                sources_n,
            ),
        )
        changed += cur.rowcount
    get_logger("pipeline.reassess").info("reassessed", facts=len(rows), changed=changed)


@register("trust")
def trust_handler(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """Nightly: recompute what each source has actually earned."""
    from toast.pipeline.trust import compute_source_stats

    stats = compute_source_stats(conn)
    suggestions = [s for s in stats if s["suggested_tier"] is not None]
    get_logger("trust").info(
        "source_stats_computed", sources=len(stats), suggestions=len(suggestions)
    )


@register("backup")
def backup_handler(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """Nightly verified snapshot (+ offsite when B2 credentials exist)."""
    from toast.backup import run_backup

    result = run_backup(conn)
    get_logger("backup").info(
        "backup_done",
        file=result["manifest"]["file"],
        pruned=len(result["pruned"]),
        offsite=bool(result["offsite"]),
    )


@register("weather_refresh")
def weather_refresh_handler(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """Refresh cached weather for every active incident so the wind layer has
    data even for fires nobody opened. NWS asks not to be hammered, so this runs
    on a slow cadence (see scheduler) and skips incidents with no coordinates."""
    from toast.pipeline.weather import incident_weather

    rows = conn.execute(
        "SELECT id FROM incidents WHERE status = 'active' AND lat IS NOT NULL AND lon IS NOT NULL"
    ).fetchall()
    done = 0
    for r in rows:
        try:
            incident_weather(conn, r["id"], refresh=True)
            done += 1
        except Exception as exc:  # one bad point must not stop the sweep
            get_logger("weather").warning(
                "weather_refresh_failed", incident=r["id"], error=str(exc)[:100]
            )
    get_logger("weather").info("weather_refresh_done", incidents=done)


@register("evac_zone_sync")
def evac_zone_sync_handler(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """An evacuation instruction just landed: pull the Cal OES zone layer NOW
    (cache bypassed) so the polygons on the map track the order, not the TTL."""
    from toast.pipeline.evaczones import get_evac_zones

    result = get_evac_zones(conn, refresh=True)
    get_logger("evaczones").info(
        "evac_zone_sync", trigger_incident=payload.get("incident_id"),
        followup=bool(payload.get("followup")), zones=result.get("count"),
        matched=result.get("matched"),
    )


@register("activity_check")
def activity_check_handler(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """Is every active fire actually still burning? Proposals, never actions."""
    from toast.pipeline.activity import check_active

    result = check_active(conn)
    get_logger("activity").info("activity_check_job", **result)


@register("autotriage")
def autotriage_handler(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """Draft dispositions for the adjudication queue. Proposals only — the
    operator accepts through the same endpoints a manual decision uses."""
    from toast.pipeline.autotriage import propose_batch

    result = propose_batch(conn)
    get_logger("autotriage").info("autotriage_batch", **result)


@register("telegram_notify")
def telegram_notify_handler(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """Push new-fire / evac-change / draft-for-review alerts to the ops chat."""
    from toast.pipeline.telegram_ops import notify_sweep

    notify_sweep(conn)


@register("telegram_poll")
def telegram_poll_handler(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """Handle APPROVE / SEND BACK buttons pressed in the ops chat."""
    from toast.pipeline.telegram_ops import poll_updates

    poll_updates(conn)


@register("release_due")
def release_due_handler(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """Enqueue publish jobs for scheduled publications whose time has come."""
    from toast.pipeline.release import release_due

    n = release_due(conn)
    if n:
        get_logger("release").info("scheduled_releases_enqueued", n=n)


@register("radio_ingest")
def radio_ingest_handler(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """Sweep the SDR inbox, then transcribe a few pending calls if a local
    Whisper endpoint is configured. Both halves degrade honestly without
    hardware or endpoint — nothing dead-letters for equipment not yet owned."""
    from toast.pipeline.radio import ingest_inbox, pending_transcriptions, transcribe_call

    result = ingest_inbox(conn)
    transcribed = 0
    for call_id in pending_transcriptions(conn, limit=5):
        out = transcribe_call(conn, call_id)
        if out.get("state") == "pending":
            break  # no endpoint configured; stop asking
        transcribed += 1
    get_logger("radio").info("radio_sweep_done", **result, transcribed=transcribed)


@register("aircraft")
def aircraft_handler(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """Sweep aircraft over active fires. Borrowed live positions, pruned after
    12h — never ledger facts."""
    from toast.pipeline.aircraft import poll_active

    result = poll_active(conn)
    get_logger("aircraft").info("aircraft_sweep_done", **result)


@register("raw_sync")
def raw_sync_handler(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """Ship a batch of the raw archive offsite. Bounded per run so a first sync
    of thousands of blobs spreads over nights instead of stalling the queue."""
    from toast.core.credentials import settings_with_credentials
    from toast.rawsync import sync_raw

    result = sync_raw(conn, settings_with_credentials(conn), limit=payload.get("limit", 250))
    get_logger("rawsync").info(
        "raw_sync_done",
        uploaded=result.get("uploaded", 0),
        pending=result.get("pending"),
        offsite=result.get("offsite"),
    )


@register("publish")
def publish_handler(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """Deliver an approved+published SOCIAL draft to one external channel.

    Never retries, and that is enforced rather than intended: this function
    catches everything, records an outcome, and returns. If any exception
    escaped, jobs.fail would requeue it and the queue would re-post — the
    docstring used to promise no retries while catching only PublishError, so a
    ReadTimeout from a post X had already accepted meant five identical public
    posts.

    Two guards:
      * the publications row must still be 'queued'. requeue_stale can hand the
        same job to a second worker; a row that is already resolved is not
        re-sent.
      * an ambiguous failure lands in 'unknown', not 'failed'. We do not know
        whether it went out, so a human checks the account before deciding."""
    import json as _json

    from toast.publishers import PublishError, get_publisher, split_social_body

    draft_id = payload["draft_id"]
    channel = payload["channel"]
    publication_id = payload["publication_id"]
    log = get_logger("publisher")

    pub = conn.execute("SELECT status FROM publications WHERE id = ?", (publication_id,)).fetchone()
    if pub is None:
        log.warning("publish_row_missing", publication_id=publication_id, channel=channel)
        return
    if pub["status"] != "queued":
        # already resolved by an earlier run — re-sending is the duplicate
        log.info(
            "publish_skipped_not_queued",
            publication_id=publication_id,
            channel=channel,
            status=pub["status"],
        )
        return

    draft = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if draft is None or draft["status"] != "published":
        conn.execute(
            "UPDATE publications SET status = 'failed', receipt = ? WHERE id = ?",
            (_json.dumps({"error": "draft missing or no longer published"}), publication_id),
        )
        return
    post_text, short_text = split_social_body(draft["body_md"] or "")
    # short version for length-capped channels, full post for the rest
    text = short_text if channel in ("bluesky", "x") else post_text

    from toast.core.credentials import settings_with_credentials

    # the attached card rides along where the channel supports images; a
    # missing or unreadable graphic degrades to a text post, never a failure
    image_bytes = None
    if draft["graphic_sha"] and channel in ("bluesky", "telegram"):
        blob = conn.execute(
            "SELECT path FROM raw_blobs WHERE sha256 = ?", (draft["graphic_sha"],)
        ).fetchone()
        if blob:
            from pathlib import Path as _Path

            from toast.core.config import Settings as _Settings

            try:
                image_bytes = (_Path(_Settings().toast_data_dir) / blob["path"]).read_bytes()
            except OSError:
                image_bytes = None

    try:
        publisher = get_publisher(channel, settings_with_credentials(conn))
        if image_bytes is not None:
            receipt = publisher.publish(
                text, image=image_bytes, image_alt="incident map and statistics card"
            )
        else:
            receipt = publisher.publish(text)
    except PublishError as exc:
        # the publisher got a definitive answer: it did not go out
        conn.execute(
            "UPDATE publications SET status = 'failed', receipt = ? WHERE id = ?",
            (_json.dumps({"error": str(exc)}), publication_id),
        )
        log.warning("publish_failed", channel=channel, draft_id=draft_id, error=str(exc))
        return
    except Exception as exc:
        # Transport-level failure — timeout, reset, DNS. The post may well have
        # landed; the response just never came back. Recording this as 'failed'
        # would invite a re-send, and a re-send is the duplicate. Say we do not
        # know, and stop.
        conn.execute(
            "UPDATE publications SET status = 'unknown', receipt = ? WHERE id = ?",
            (
                _json.dumps(
                    {
                        "error": repr(exc),
                        "note": "delivery indeterminate — check the account before re-sending",
                    }
                ),
                publication_id,
            ),
        )
        log.error("publish_indeterminate", channel=channel, draft_id=draft_id, error=repr(exc))
        return
    conn.execute(
        "UPDATE publications SET status = 'sent', external_url = ?, receipt = ?, sent_at = ?"
        " WHERE id = ?",
        (receipt.get("url"), _json.dumps(receipt), utcnow(), publication_id),
    )
    log.info("published", channel=channel, draft_id=draft_id, url=receipt.get("url"))


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
    from toast.core.appsettings import get_setting

    research_id = client.start(payload["query"], model=get_setting(conn, "llm_model"))
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


@register("jobs_prune")
def jobs_prune_handler(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """Retention for the queue itself: terminal jobs older than the window go.
    Without this, done/dead rows accumulate all season (every poll enqueues)
    and the claim query's scan grows with them. Live/queued jobs untouched."""
    cur = conn.execute(
        """DELETE FROM jobs WHERE state IN ('done', 'dead')
           AND unixepoch('now') - unixepoch(created_at) > 7 * 86400"""
    )
    conn.commit()
    if cur.rowcount:
        get_logger("jobs").info("jobs_pruned", removed=cur.rowcount)
