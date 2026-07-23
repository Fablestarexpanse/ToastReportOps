"""Seed the official Phase 1 sources (idempotent).

    python -m toast.seed

FIRMS is created disabled until FIRMS_MAP_KEY is set in .env — enable it in
the SOURCES UI (or flip sources.enabled) once the free key is registered.
"""

from __future__ import annotations

import json
import os
from typing import Any

from toast.core.config import Settings
from toast.core.db import apply_migrations, connect, utcnow
from toast.core.ids import ulid

SEEDS: list[dict[str, Any]] = [
    {
        "name": "CAL FIRE active incidents",
        "type": "api",
        "adapter": "calfire",
        "tier": 1,
        "config": {"inactive": False},
        "schedule": "120",  # seconds
        "enabled": 1,
        "tos_notes": "incidents.fire.ca.gov public API; authoritative CA active list. "
        "Reviewed 2026-07-19.",
    },
    {
        # Historical/federal catalog. Disabled as a poller — it lists the entire
        # current-year backlog with no reliable active flag. Kept available for
        # the ARCHIVE search/import flow.
        "name": "WFIGS / IRWIN incidents",
        "type": "api",
        "adapter": "wfigs",
        "tier": 1,
        "config": {"state": "US-CA"},
        "schedule": "120",  # seconds
        "enabled": 0,
        "tos_notes": "Public NIFC open-data service; attribution preserved. Search/import only. "
        "Reviewed 2026-07-19.",
    },
    {
        "name": "NWS CAP alerts (CA)",
        "type": "api",
        "adapter": "nws_cap",
        "tier": 1,
        "config": {},
        "schedule": "300",
        "enabled": 1,
        "tos_notes": "api.weather.gov public API. Reviewed 2026-07-19.",
    },
    {
        "name": "NASA FIRMS hotspots (VIIRS, CA)",
        "type": "satellite",
        "adapter": "firms",
        "tier": 1,
        "config": {"map_key_env": "FIRMS_MAP_KEY"},
        "schedule": "600",
        "enabled": 0,  # enable once FIRMS_MAP_KEY is registered
        "tos_notes": "Free map key required; rate limits apply. Reviewed 2026-07-19.",
    },
]

# Curated California wildfire media feeds. The extractor's California-relevance
# gate filters anything not about a CA fire, so broad feeds are safe. Aggregator
# feeds (Google News) are headline-level — the extract stage falls back to the
# feed title+summary when the article page can't be fetched. Operators add more
# via SOURCES → ADD SOURCE.
_GNEWS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def _gnews(query: str) -> str:
    from urllib.parse import quote

    return _GNEWS.format(q=quote(query))


MEDIA_SEEDS: list[dict[str, Any]] = [
    {
        "name": "InciWeb incidents (national)",
        "type": "api",
        "adapter": "rss",
        "tier": 2,
        "config": {"feed_url": "https://inciweb.wildfire.gov/incidents/rss.xml"},
        "schedule": "900",
        "enabled": 1,
        "tos_notes": "Official interagency incident feed (public). Reviewed 2026-07-20.",
    },
    {
        "name": "Google News: California wildfire",
        "type": "scrape",
        "adapter": "rss",
        "tier": 4,
        "config": {"feed_url": _gnews("california wildfire when:2d"), "max_entries": 15},
        "schedule": "900",
        "enabled": 1,
        "tos_notes": "Google News RSS (headline-level). Reviewed 2026-07-20.",
    },
    {
        "name": "Google News: CA fire evacuation",
        "type": "scrape",
        "adapter": "rss",
        "tier": 4,
        "config": {"feed_url": _gnews("california fire evacuation when:2d"), "max_entries": 12},
        "schedule": "900",
        "enabled": 1,
        "tos_notes": "Google News RSS (headline-level). Reviewed 2026-07-20.",
    },
    {
        "name": "Google News: Butte County fire",
        "type": "scrape",
        "adapter": "rss",
        "tier": 4,
        "config": {"feed_url": _gnews('"Butte County" fire when:7d'), "max_entries": 10},
        "schedule": "1800",
        "enabled": 1,
        "tos_notes": "Google News RSS, operator's home county. Reviewed 2026-07-20.",
    },
    {
        "name": "Wildfire Today",
        "type": "scrape",
        "adapter": "rss",
        "tier": 4,
        "config": {"feed_url": "https://wildfiretoday.com/feed/", "max_entries": 8},
        "schedule": "1800",
        "enabled": 1,
        "tos_notes": "Wildfire news blog; robots allows articles. Reviewed 2026-07-20.",
    },
    {
        "name": "KQED News (Bay Area)",
        "type": "scrape",
        "adapter": "rss",
        "tier": 4,
        "config": {"feed_url": "https://ww2.kqed.org/news/feed/", "max_entries": 12},
        "schedule": "1800",
        "enabled": 1,
        "tos_notes": "Public media RSS. Reviewed 2026-07-20.",
    },
    {
        "name": "ABC10 Sacramento (local)",
        "type": "scrape",
        "adapter": "rss",
        "tier": 4,
        "config": {
            "feed_url": "https://www.abc10.com/feeds/syndication/rss/news/local",
            "max_entries": 12,
        },
        "schedule": "1800",
        "enabled": 1,
        "tos_notes": "NorCal local news RSS. Reviewed 2026-07-20.",
    },
]

# General-purpose news + social feeds. These are NOT wildfire-specific, so they
# carry a fire keyword pre-filter (config keywords="fire") — only fire-related
# entries reach the LLM extractor; the California gate then filters relevance.
# Topic-scoped feeds (hashtags, search queries) need no keyword filter.
GENERAL_SEEDS: list[dict[str, Any]] = [
    {
        "name": "LA Times — California",
        "type": "scrape",
        "adapter": "rss",
        "tier": 4,
        "config": {
            "feed_url": "https://www.latimes.com/local/california/rss2.0.xml",
            "keywords": "fire",
            "max_entries": 15,
        },
        "schedule": "1800",
        "enabled": 1,
        "tos_notes": "Major CA daily, fire-filtered. Reviewed 2026-07-20.",
    },
    {
        "name": "The Guardian — California",
        "type": "scrape",
        "adapter": "rss",
        "tier": 4,
        "config": {
            "feed_url": "https://www.theguardian.com/us-news/california/rss",
            "keywords": "fire",
            "max_entries": 12,
        },
        "schedule": "1800",
        "enabled": 1,
        "tos_notes": "Guardian CA section, fire-filtered. Reviewed 2026-07-20.",
    },
    {
        "name": "ABC7 Bay Area",
        "type": "scrape",
        "adapter": "rss",
        "tier": 4,
        "config": {"feed_url": "https://abc7news.com/feed/", "keywords": "fire", "max_entries": 12},
        "schedule": "1800",
        "enabled": 1,
        "tos_notes": "Bay Area TV news, fire-filtered. Reviewed 2026-07-20.",
    },
    {
        "name": "KTLA (Los Angeles)",
        "type": "scrape",
        "adapter": "rss",
        "tier": 4,
        "config": {"feed_url": "https://ktla.com/feed/", "keywords": "fire", "max_entries": 12},
        "schedule": "1800",
        "enabled": 1,
        "tos_notes": "LA TV news, fire-filtered. Reviewed 2026-07-20.",
    },
    {
        "name": "Times of San Diego",
        "type": "scrape",
        "adapter": "rss",
        "tier": 4,
        "config": {
            "feed_url": "https://timesofsandiego.com/feed/",
            "keywords": "fire",
            "max_entries": 10,
        },
        "schedule": "1800",
        "enabled": 1,
        "tos_notes": "San Diego local, fire-filtered. Reviewed 2026-07-20.",
    },
    # ---- social (topic-scoped feeds — no keyword filter needed) ----
    {
        "name": "Mastodon #wildfire",
        "type": "social",
        "adapter": "rss",
        "tier": 5,
        "config": {"feed_url": "https://mastodon.social/tags/wildfire.rss", "max_entries": 15},
        "schedule": "1800",
        "enabled": 1,
        "tos_notes": "Public Mastodon hashtag feed (no auth). Reviewed 2026-07-20.",
    },
    {
        "name": "Mastodon #CaliforniaFire",
        "type": "social",
        "adapter": "rss",
        "tier": 5,
        "config": {
            "feed_url": "https://mastodon.social/tags/CaliforniaFire.rss",
            "max_entries": 15,
        },
        "schedule": "1800",
        "enabled": 1,
        "tos_notes": "Public Mastodon hashtag feed (no auth). Reviewed 2026-07-20.",
    },
    {
        "name": "Reddit r/California (fire search)",
        "type": "social",
        "adapter": "rss",
        "tier": 5,
        "config": {
            "feed_url": "https://www.reddit.com/r/California/search.rss"
            "?q=fire&restrict_sr=1&sort=new",
            "max_entries": 12,
        },
        "schedule": "1800",
        "enabled": 1,
        "tos_notes": "Reddit search RSS; rate-limited, polled sparingly. Reviewed 2026-07-20.",
    },
]

SEEDS = SEEDS + MEDIA_SEEDS + GENERAL_SEEDS


# Starter scanner channels — statewide reference listening posts, all link-outs
# (the operator upgrades any of them to an in-app stream by pasting a stream
# URL, or adds OpenMHz systems from the RADIO tab's picker). Broadcastify stays
# a link-out by operator decision: their terms prohibit third-party embedding.
RADIO_CHANNEL_SEEDS = [
    {
        "name": "Broadcastify — California directory",
        "kind": "link",
        "url": "https://www.broadcastify.com/listen/stid/6",
        "notes": "Every CA volunteer feed (CAL FIRE units, county dispatch). Link-out per TOS.",
    },
    {
        "name": "ScanCal — CA fire live streams",
        "kind": "link",
        "url": "https://www.scancal.org/live.html",
        "notes": "Independent CAL FIRE + air tactics streams, openly published.",
    },
    {
        "name": "CA Fire Fighters — live dispatch",
        "kind": "link",
        "url": "https://www.cafirefighters.com/live.htm",
        "notes": "Statewide fire/EMS dispatch listening page.",
    },
    # The operator's own antenna, live. rtl_airband publishes CAL FIRE VHF to
    # the in-stack icecast (port 8100); this channel plays it the moment the
    # SDR host starts streaming — see docs/RADIO-SETUP.md. Free forever, no
    # third-party terms, works through a PSPS.
    {
        "name": "My SDR — LIVE (Butte CAL FIRE VHF)",
        "kind": "stream",
        "county": "Butte",
        "url": "http://localhost:8100/calfire-btu",
        "notes": "Own RTL-SDR via rtl_airband -> in-stack icecast. Silent until the "
        "SDR host streams (docs/RADIO-SETUP.md, 'live listening').",
    },
]


def seed_radio_channels(conn) -> list[str]:
    created = []
    for spec in RADIO_CHANNEL_SEEDS:
        if conn.execute(
            "SELECT 1 FROM radio_channels WHERE name = ?", (spec["name"],)
        ).fetchone():
            continue
        now = utcnow()
        conn.execute(
            """INSERT INTO radio_channels (id, name, kind, county, system, talkgroups,
                                           url, notes, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, 1, ?, ?)""",
            (
                ulid(),
                spec["name"],
                spec["kind"],
                spec.get("county"),
                spec["url"],
                spec["notes"],
                now,
                now,
            ),
        )
        created.append(spec["name"])
    return created


def seed(conn) -> list[str]:
    created = seed_radio_channels(conn)
    for spec in SEEDS:
        exists = conn.execute(
            "SELECT id, enabled FROM sources WHERE adapter = ? AND name = ?",
            (spec["adapter"], spec["name"]),
        ).fetchone()
        if exists:
            continue
        enabled = spec["enabled"]
        if spec["adapter"] == "firms" and os.environ.get("FIRMS_MAP_KEY"):
            enabled = 1
        now = utcnow()
        conn.execute(
            """INSERT INTO sources (id, name, type, adapter, tier, config, schedule, enabled,
                                    tos_notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ulid(),
                spec["name"],
                spec["type"],
                spec["adapter"],
                spec["tier"],
                json.dumps(spec["config"]),
                spec["schedule"],
                enabled,
                spec["tos_notes"],
                now,
                now,
            ),
        )
        created.append(spec["name"])
    return created


def main() -> None:
    settings = Settings()
    conn = connect(settings.toast_db_path)
    apply_migrations(conn)
    created = seed(conn)
    print(f"seeded: {created or 'nothing (all present)'}")


if __name__ == "__main__":
    main()
