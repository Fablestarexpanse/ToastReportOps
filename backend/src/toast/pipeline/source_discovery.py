"""Source cross-reference — find where our information actually comes from.

Research dossiers and aggregator articles constantly cite outlets we don't
follow directly. This module mines those URLs, compares the domains against the
sources table, and surfaces the unknowns as suggestions the operator can
APPROVE (feed-probe + add as a real source) or DISMISS. Nothing is ever added
without approval — trust is earned, and it starts with a human yes.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any
from urllib.parse import urlparse

from toast.core.db import utcnow
from toast.core.ids import ulid

URL_RE = re.compile(r"https?://[^\s\)\]\"'>,]+")

# Platforms, utilities, and link-shorteners that can't be followed as feeds —
# never suggested. Matched on the registrable suffix (subdomains included).
IGNORE_DOMAINS = {
    "google.com",
    "news.google.com",
    "bing.com",
    "duckduckgo.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "t.co",
    "instagram.com",
    "youtube.com",
    "youtu.be",
    "tiktok.com",
    "linkedin.com",
    "reddit.com",
    "wikipedia.org",
    "wikimedia.org",
    "archive.org",
    "web.archive.org",
    "imgur.com",
    "bit.ly",
    "goo.gl",
    "tinyurl.com",
    "medium.com",
    "github.com",
    "doi.org",
    "maps.google.com",
    "fed.brid.gy",
    "brid.gy",
}

# Official endpoints hardcoded inside adapters (not visible in sources.config)
# — already followed, never suggested.
BUILTIN_KNOWN_DOMAINS = {
    "fire.ca.gov",
    "incidents.fire.ca.gov",
    "inciweb.wildfire.gov",
    "wildfire.gov",
    "firms.modaps.eosdis.nasa.gov",
    "api.weather.gov",
    "weather.gov",
    "services3.arcgis.com",
    "arcgis.com",
}


def _domain(url: str) -> str | None:
    try:
        host = (urlparse(url).hostname or "").lower().strip(".")
    except ValueError:
        return None
    if not host or "." not in host:
        return None
    return host.removeprefix("www.")


def _ignored(domain: str) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in IGNORE_DOMAINS)


def known_domains(conn: sqlite3.Connection) -> set[str]:
    """Domains we already follow — every URL-ish value in sources.config,
    plus adapters' hardcoded official endpoints."""
    known: set[str] = set(BUILTIN_KNOWN_DOMAINS)
    for row in conn.execute("SELECT config FROM sources").fetchall():
        try:
            cfg = json.loads(row["config"])
        except (json.JSONDecodeError, TypeError):
            continue
        for v in cfg.values():
            if isinstance(v, str) and v.startswith("http"):
                d = _domain(v)
                if d:
                    known.add(d)
    return known


def _harvest(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """domain -> {urls: set, contexts: list} from dossiers + article links."""
    found: dict[str, dict[str, Any]] = {}

    def note(url: str, context: str) -> None:
        d = _domain(url)
        if not d or _ignored(d):
            return
        slot = found.setdefault(d, {"urls": set(), "contexts": []})
        slot["urls"].add(url)
        if context and context not in slot["contexts"] and len(slot["contexts"]) < 3:
            slot["contexts"].append(context)

    for row in conn.execute("SELECT title, body_md FROM drafts WHERE kind = 'analysis'").fetchall():
        label = (row["title"] or "research dossier")[:60]
        for url in URL_RE.findall(row["body_md"] or ""):
            note(url, label)

    for row in conn.execute(
        """SELECT json_extract(payload, '$.url') AS url, s.name AS source_name
           FROM envelopes e JOIN sources s ON s.id = e.source_id
           WHERE e.kind IN ('article', 'post')"""
    ).fetchall():
        if row["url"]:
            note(str(row["url"]), f"via {row['source_name']}")

    return found


def scan_for_suggestions(conn: sqlite3.Connection) -> int:
    """Recompute suggestions from the current ledger. Idempotent: counts are
    derived fresh each scan; approved/dismissed status is always preserved."""
    known = known_domains(conn)
    # drop stale pending rows for domains that have since become known/ignored
    for row in conn.execute(
        "SELECT id, domain FROM source_suggestions WHERE status = 'pending'"
    ).fetchall():
        if row["domain"] in known or _ignored(row["domain"]):
            conn.execute("DELETE FROM source_suggestions WHERE id = ?", (row["id"],))
    now = utcnow()
    new_or_updated = 0
    for domain, info in _harvest(conn).items():
        if domain in known:
            continue
        sample = sorted(info["urls"])[0]
        cur = conn.execute(
            """INSERT INTO source_suggestions
                 (id, domain, sample_url, contexts, hit_count,
                  first_seen_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(domain) DO UPDATE SET
                 hit_count = excluded.hit_count,
                 sample_url = excluded.sample_url,
                 contexts = excluded.contexts,
                 last_seen_at = excluded.last_seen_at""",
            (
                ulid(),
                domain,
                sample,
                json.dumps(info["contexts"]),
                len(info["urls"]),
                now,
                now,
            ),
        )
        new_or_updated += cur.rowcount
    return new_or_updated


def pending_suggestions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT id, domain, sample_url, contexts, hit_count, first_seen_at
           FROM source_suggestions WHERE status = 'pending'
           ORDER BY hit_count DESC, domain"""
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["contexts"] = json.loads(d["contexts"] or "[]")
        out.append(d)
    return out
