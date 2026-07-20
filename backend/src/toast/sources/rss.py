"""RSS/Atom feed adapter (news + official feeds).

Parses a feed into one `article` Envelope per entry. Envelopes carry the entry
metadata + link; the article-extract pipeline stage fetches the full page and
runs the LLM extractor. Dedup keys on the entry link/id, so re-polling a feed
only surfaces new entries.
"""

from __future__ import annotations

import re
from typing import Any

import feedparser

from toast.core.envelope import Envelope
from toast.sources import register
from toast.sources.capture import RawCapture


def _entry_time(entry: Any) -> str | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            # struct_time (UTC) → ISO
            return (
                f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"
                f"T{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}.000Z"
            )
    return None


# Default fire keywords for general-purpose feeds — only entries mentioning one
# of these reach the (costly) LLM extractor. Topic-scoped feeds (hashtags,
# search queries, wildfire blogs) set no keywords and pass everything through.
DEFAULT_FIRE_KEYWORDS = [
    "fire",
    "wildfire",
    "blaze",
    "flames",
    "evacuat",
    "cal fire",
    "calfire",
    "wildland",
    "brush fire",
    "smoke",
    "acres",
    "containment",
    "red flag",
    "burn",
]


def _keyword_regex(keywords: list[str]) -> re.Pattern[str]:
    # word-START boundary: "\bfire" matches "fire"/"firefighter" but not
    # "backfire"/"gunfire"/"campfire"; "\bevacuat" matches evacuate/evacuation.
    return re.compile(r"\b(" + "|".join(re.escape(k) for k in keywords) + r")", re.IGNORECASE)


def _matches_keywords(entry: Any, pattern: re.Pattern[str]) -> bool:
    haystack = f"{entry.get('title') or ''} {entry.get('summary') or ''}"
    return pattern.search(haystack) is not None


@register("rss")
class RssAdapter(RawCapture):
    def poll(self, config: dict[str, Any], cursor: str | None) -> tuple[list[Envelope], str | None]:
        feed_url = config["feed_url"]
        max_entries = config.get("max_entries")  # cap per poll; None = all
        # keywords: [] or omitted → no filter; "fire" → default fire set; list → custom
        raw_kw = config.get("keywords")
        keywords = DEFAULT_FIRE_KEYWORDS if raw_kw == "fire" else raw_kw
        body, _ctype = self.fetch_captured(feed_url)
        parsed = feedparser.parse(body)

        entries = parsed.entries
        if keywords:
            pattern = _keyword_regex(keywords)
            entries = [e for e in entries if _matches_keywords(e, pattern)]
        entries = entries[:max_entries] if max_entries else entries
        envelopes: list[Envelope] = []
        for entry in entries:
            link = entry.get("link")
            if not link:
                continue
            envelopes.append(
                Envelope(
                    source_id=config["source_id"],
                    external_id=entry.get("id") or link,
                    kind="article",
                    payload={
                        "url": link,
                        "title": entry.get("title"),
                        "summary": entry.get("summary"),
                        "author": entry.get("author"),
                        "published_at": _entry_time(entry),
                    },
                    observed_hint=_entry_time(entry),
                    url=link,
                )
            )
        return envelopes, cursor  # feed is a snapshot; dedup handles seen entries
