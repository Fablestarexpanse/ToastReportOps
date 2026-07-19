"""RawCapture mixin — adapters fetch through this so the pipeline can archive
every raw response to the CAS before envelopes are processed."""

from __future__ import annotations

from toast.core.fetch import fetch


class RawCapture:
    def __init__(self) -> None:
        # (body, content_type, url-or-None) per fetch this poll
        self.fetches: list[tuple[bytes, str, str | None]] = []

    def fetch_captured(self, url: str, *, record_url: bool = True, **kwargs) -> tuple[bytes, str]:
        body, ctype = fetch(url, **kwargs)
        self.fetches.append((body, ctype, url if record_url else None))
        return body, ctype
