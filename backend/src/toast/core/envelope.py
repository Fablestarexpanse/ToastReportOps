"""Envelope — the universal normalized unit every source adapter emits.

Raw source material is archived to the CAS (raw_blobs) BEFORE the Envelope
enters the pipeline; envelope.raw_ref points at it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Literal

from pydantic import BaseModel, Field

from toast.core.db import utcnow
from toast.core.ids import ulid

Kind = Literal[
    "report",
    "hotspot",
    "article",
    "post",
    "audio",
    "video",
    "image",
    "aircraft",
    "weather",
    "alert",
]


class Envelope(BaseModel):
    source_id: str
    external_id: str | None = None
    kind: Kind
    payload: dict[str, Any] = Field(default_factory=dict)
    raw_ref: str | None = None  # raw_blobs.sha256
    observed_hint: str | None = None  # source-claimed event time, ISO UTC
    url: str | None = None

    def content_hash(self) -> str:
        canonical = json.dumps(self.payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


def insert_envelope(conn: sqlite3.Connection, env: Envelope) -> str | None:
    """Insert with dedup on (source_id, external_id, content_hash).

    Returns the new envelope id, or None if this exact content was already seen.
    """
    env_id = ulid()
    cur = conn.execute(
        """INSERT OR IGNORE INTO envelopes
           (id, source_id, external_id, kind, payload, raw_ref, content_hash, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            env_id,
            env.source_id,
            env.external_id,
            env.kind,
            json.dumps(env.payload),
            env.raw_ref,
            env.content_hash(),
            utcnow(),
        ),
    )
    return env_id if cur.rowcount else None
