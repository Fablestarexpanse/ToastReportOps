"""YouTube channel adapter — via the public per-channel RSS feed.

CAL FIRE units, county OES offices, and local TV stations put footage and press
conferences on YouTube long before anything is written down. The channel feed at
`/feeds/videos.xml` is public, keyless, and quota-free, so this costs nothing and
cannot be rate-limited away.

Deliberately shallow: we take the title and the description the uploader wrote,
and we never fetch or transcribe the video page. That means a claim in the
ledger traces to text a person actually published, not to something we inferred
from pixels. The video URL rides along so the operator can watch it themselves.

**Robots**: youtube.com/robots.txt carries `Disallow: /feeds/videos.xml` for all
crawlers. The feed is public and unauthenticated, but that line is an explicit
"no automated fetching", so this adapter refuses to poll unless the operator has
set `robots_override` on the source. That is a decision with legal weight and it
belongs to the human, not to a default in a seed file.
"""

from __future__ import annotations

from typing import Any

import feedparser

from toast.core.envelope import Envelope
from toast.core.fetch import robots_allows
from toast.sources import register
from toast.sources.capture import RawCapture
from toast.sources.rss import DEFAULT_FIRE_KEYWORDS, _entry_time, _keyword_regex, _matches_keywords


class RobotsDisallowed(RuntimeError):
    """Raised instead of fetching a path the site's robots.txt forbids."""


FEED_URL = "https://www.youtube.com/feeds/videos.xml"


def feed_url_for(config: dict[str, Any]) -> str:
    """Channel, playlist, or an explicit feed URL — whichever the operator gave."""
    if config.get("feed_url"):
        return str(config["feed_url"])
    if config.get("channel_id"):
        return f"{FEED_URL}?channel_id={config['channel_id']}"
    if config.get("playlist_id"):
        return f"{FEED_URL}?playlist_id={config['playlist_id']}"
    raise ValueError("youtube source needs channel_id, playlist_id, or feed_url")


def _video_id(entry: Any) -> str | None:
    vid = entry.get("yt_videoid")
    if vid:
        return str(vid)
    eid = entry.get("id") or ""
    return eid.rsplit(":", 1)[-1] if eid.startswith("yt:video:") else None


def _description(entry: Any) -> str:
    # feedparser puts media:description in media_description for YouTube feeds;
    # summary is the same text on most, but not all, entries.
    for key in ("media_description", "summary"):
        text = entry.get(key)
        if text:
            return str(text)
    return ""


def _thumbnail(entry: Any) -> str | None:
    thumbs = entry.get("media_thumbnail") or []
    if thumbs and isinstance(thumbs, list):
        return str(thumbs[0].get("url")) if thumbs[0].get("url") else None
    return None


@register("youtube")
class YoutubeAdapter(RawCapture):
    """One `video` envelope per upload. Extraction runs on the uploader's own
    title + description; the page is never fetched."""

    def poll(self, config: dict[str, Any], cursor: str | None) -> tuple[list[Envelope], str | None]:
        url = feed_url_for(config)
        if not config.get("robots_override") and not robots_allows(url):
            raise RobotsDisallowed(
                "youtube.com/robots.txt disallows /feeds/videos.xml. Enable "
                "robots_override on this source only if you have decided that "
                "is acceptable — see docs/API-KEYS.md."
            )
        # A station channel posts far more sports than fire. Filtering here keeps
        # the LLM (and the ledger) off everything that isn't ours; a channel that
        # is nothing but fire can set keywords: [] and pass everything through.
        raw_kw = config.get("keywords", "fire")
        keywords = DEFAULT_FIRE_KEYWORDS if raw_kw == "fire" else raw_kw
        body, _ctype = self.fetch_captured(url)
        parsed = feedparser.parse(body)

        entries = parsed.entries
        if keywords:
            pattern = _keyword_regex(keywords)
            entries = [e for e in entries if _matches_keywords(e, pattern)]
        max_entries = config.get("max_entries")
        entries = entries[:max_entries] if max_entries else entries

        channel = parsed.feed.get("title") if parsed.feed else None
        envelopes: list[Envelope] = []
        for entry in entries:
            link = entry.get("link")
            vid = _video_id(entry)
            if not link:
                continue
            envelopes.append(
                Envelope(
                    source_id=config["source_id"],
                    external_id=entry.get("id") or link,
                    kind="video",
                    payload={
                        "url": link,
                        "video_id": vid,
                        "title": entry.get("title"),
                        "summary": _description(entry),
                        "author": entry.get("author") or channel,
                        "channel": channel,
                        "thumbnail": _thumbnail(entry),
                        "published_at": _entry_time(entry),
                        # extraction reads this: the video page is never fetched
                        "text_only": True,
                    },
                    observed_hint=_entry_time(entry),
                    url=link,
                )
            )
        return envelopes, cursor  # snapshot feed; envelope dedup handles re-polls
