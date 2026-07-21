"""Bluesky publisher — atproto XRPC with a handle + app password.

The easiest social channel: no app review, no OAuth dance. Posts are capped
at 300 graphemes, so the short (X-length) version of a social draft is used.
"""

from __future__ import annotations

from typing import Any

import httpx

from toast.core.config import Settings
from toast.core.db import utcnow

PDS = "https://bsky.social"
MAX_CHARS = 300


class BlueskyPublisher:
    channel = "bluesky"

    def __init__(self, settings: Settings, *, base_url: str = PDS) -> None:
        self.handle = settings.bluesky_handle
        self.password = settings.bluesky_app_password
        self.http = httpx.Client(base_url=base_url, timeout=30)

    def publish(
        self, text: str, image: bytes | None = None, image_alt: str = ""
    ) -> dict[str, Any]:
        from toast.publishers import PublishError

        if len(text) > MAX_CHARS:
            text = text[: MAX_CHARS - 1] + "…"
        r = self.http.post(
            "/xrpc/com.atproto.server.createSession",
            json={"identifier": self.handle, "password": self.password},
        )
        if r.status_code != 200:
            raise PublishError(f"bluesky login failed: HTTP {r.status_code} {r.text[:120]}")
        session = r.json()
        auth = {"Authorization": f"Bearer {session['accessJwt']}"}

        record: dict[str, Any] = {
            "$type": "app.bsky.feed.post",
            "text": text,
            "createdAt": utcnow(),
        }
        if image is not None:
            # the card is a bonus: an upload failure degrades to a text post,
            # never a failed publication
            up = self.http.post(
                "/xrpc/com.atproto.repo.uploadBlob",
                headers={**auth, "Content-Type": "image/png"},
                content=image,
            )
            if up.status_code == 200:
                record["embed"] = {
                    "$type": "app.bsky.embed.images",
                    "images": [
                        {"alt": image_alt or "incident map and statistics card",
                         "image": up.json()["blob"]}
                    ],
                }

        r = self.http.post(
            "/xrpc/com.atproto.repo.createRecord",
            headers=auth,
            json={
                "repo": session["did"],
                "collection": "app.bsky.feed.post",
                "record": record,
            },
        )
        if r.status_code != 200:
            raise PublishError(f"bluesky post failed: HTTP {r.status_code} {r.text[:120]}")
        uri = r.json().get("uri", "")  # at://did/app.bsky.feed.post/rkey
        rkey = uri.rsplit("/", 1)[-1] if uri else ""
        return {
            "uri": uri,
            "url": f"https://bsky.app/profile/{self.handle}/post/{rkey}" if rkey else None,
        }
