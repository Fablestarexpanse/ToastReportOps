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

    facts = normalize_envelope(conn, payload["envelope_id"])
    get_logger("pipeline.normalize").info(
        "normalize_done", envelope_id=payload["envelope_id"], facts=len(facts)
    )
