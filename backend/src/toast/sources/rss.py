"""RSS/Atom feed adapter (news + official feeds).

Parses a feed into one `article` Envelope per entry. Envelopes carry the entry
metadata + link; the article-extract pipeline stage fetches the full page and
runs the LLM extractor. Dedup keys on the entry link/id, so re-polling a feed
only surfaces new entries.
"""

from __future__ import annotations

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


@register("rss")
class RssAdapter(RawCapture):
    def poll(self, config: dict[str, Any], cursor: str | None) -> tuple[list[Envelope], str | None]:
        feed_url = config["feed_url"]
        max_entries = config.get("max_entries")  # cap per poll; None = all
        body, _ctype = self.fetch_captured(feed_url)
        parsed = feedparser.parse(body)

        entries = parsed.entries[:max_entries] if max_entries else parsed.entries
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
