"""Add-source-by-URL probe.

Given a URL the operator wants to follow, detect what it is and propose an
adapter + config. Phase 3 uses deterministic detection (feed content-type/XML
sniffing, YouTube host match, else treat as a scrapeable article page); the
"AI probes it" upgrade (LLM classification of ambiguous pages) can layer on
later behind the same interface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import feedparser

from toast.core.fetch import fetch

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com"}


@dataclass
class ProbeResult:
    url: str
    detected: str  # 'rss' | 'youtube' | 'article' | 'unknown'
    adapter: str
    config: dict[str, Any]
    suggested_tier: int
    title: str | None = None
    note: str = ""
    supported: bool = True
    warnings: list[str] = field(default_factory=list)


def _looks_like_feed(body: bytes, content_type: str) -> bool:
    if any(t in content_type for t in ("xml", "rss", "atom")):
        return True
    head = body[:512].lstrip().lower()
    return head.startswith(b"<?xml") or b"<rss" in head or b"<feed" in head


FEED_LINK_RE = re.compile(r'<link[^>]+type="application/(?:rss|atom)\+xml"[^>]*>', re.IGNORECASE)
HREF_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)
COMMON_FEED_PATHS = ("/feed/", "/feed", "/rss", "/rss.xml", "/atom.xml", "/index.rss")


def discover_feed(site_url: str) -> str | None:
    """Find a site's RSS/Atom feed: the URL itself, an advertised
    <link rel=alternate>, or a common feed path. None when nothing works."""

    def _is_feed(u: str) -> bytes | None:
        try:
            body, content_type = fetch(u, accept_types=(), max_bytes=1024 * 1024)
        except Exception:
            return None
        return body if _looks_like_feed(body, content_type) else None

    try:
        body, content_type = fetch(site_url, accept_types=(), max_bytes=2 * 1024 * 1024)
    except Exception:
        return None
    if _looks_like_feed(body, content_type):
        return site_url

    html = body[:262_144].decode("utf-8", errors="replace")
    for tag in FEED_LINK_RE.findall(html):
        m = HREF_RE.search(tag)
        if m:
            candidate = urljoin(site_url, m.group(1))
            if _is_feed(candidate):
                return candidate
    base = f"{urlparse(site_url).scheme}://{urlparse(site_url).netloc}"
    for path in COMMON_FEED_PATHS:
        if _is_feed(base + path):
            return base + path
    return None


def probe_url(url: str) -> ProbeResult:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    if host in YOUTUBE_HOSTS:
        return ProbeResult(
            url=url,
            detected="youtube",
            adapter="youtube",
            config={"channel_url": url},
            suggested_tier=3,
            note="YouTube source — live-stream transcription lands in a later Phase 3 step.",
            supported=False,
            warnings=["YouTube adapter not yet built; saved as disabled."],
        )

    body, content_type = fetch(url, accept_types=(), max_bytes=2 * 1024 * 1024)
    if _looks_like_feed(body, content_type):
        parsed_feed = feedparser.parse(body)
        title = parsed_feed.feed.get("title") if parsed_feed.feed else None
        return ProbeResult(
            url=url,
            detected="rss",
            adapter="rss",
            config={"feed_url": url},
            suggested_tier=4,
            title=title,
            note=f"RSS/Atom feed with {len(parsed_feed.entries)} recent entries.",
        )

    # default: a scrapeable article/news page — wrap as a single-entry poll via
    # the manual analyze path is out of scope; treat as an article page the
    # operator can point-analyze. We still register it as an rss candidate only
    # if a feed is discoverable; otherwise flag article.
    return ProbeResult(
        url=url,
        detected="article",
        adapter="rss",
        config={"feed_url": url},
        suggested_tier=4,
        note="No feed detected. If this is a single article, use OPS → point-at-URL. "
        "If the site has an RSS feed, add that URL instead.",
        supported=False,
        warnings=["Not a feed — provide the site's RSS/Atom feed URL to auto-follow it."],
    )
