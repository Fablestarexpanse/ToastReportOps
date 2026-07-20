"""Facebook Page publisher — Graph API feed post with a page token.

Requires an app with pages_manage_posts through Meta app review (weeks of
lead time — docs/API-KEYS.md says start early). Gets the full-length post
text; Facebook has no meaningful length cap for our updates.
"""

from __future__ import annotations

from typing import Any

import httpx

from toast.core.config import Settings

GRAPH = "https://graph.facebook.com/v21.0"


class FacebookPublisher:
    channel = "facebook"

    def __init__(self, settings: Settings, *, base_url: str = GRAPH) -> None:
        self.page_id = settings.fb_page_id
        self.token = settings.fb_page_token
        self.http = httpx.Client(base_url=base_url, timeout=30)

    def publish(self, text: str) -> dict[str, Any]:
        from toast.publishers import PublishError

        r = self.http.post(
            f"/{self.page_id}/feed",
            data={"message": text, "access_token": self.token},
        )
        if r.status_code != 200:
            raise PublishError(f"facebook post failed: HTTP {r.status_code} {r.text[:120]}")
        post_id = r.json().get("id", "")
        return {
            "id": post_id,
            "url": f"https://www.facebook.com/{post_id}" if post_id else None,
        }
