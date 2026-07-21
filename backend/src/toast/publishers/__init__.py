"""Social publishers — post approved SOCIAL drafts to external channels.

Cred-gated: a channel exists only when its credentials are set in .env
(docs/API-KEYS.md walks through each registration). Only the publisher
service/worker holds outbound credentials, and only operator-approved
kind='social' drafts ever reach these adapters — publishing is always a
human decision enforced by the drafts state machine.
"""

from __future__ import annotations

from typing import Any, Protocol

from toast.core.config import Settings

SOCIAL_SPLIT = "\n\n---\n\n**Short version"


class PublishError(Exception):
    pass


class Publisher(Protocol):
    channel: str

    def publish(self, text: str) -> dict[str, Any]:
        """Post text; return a receipt dict including 'url' when known."""
        ...


def split_social_body(body_md: str) -> tuple[str, str]:
    """A social draft body is post_text + optional short (X-length) version.
    Returns (post_text, short_text); short falls back to a trimmed post."""
    if SOCIAL_SPLIT in body_md:
        post, _, rest = body_md.partition(SOCIAL_SPLIT)
        # the short text is the last paragraph of the marker block
        short = rest.strip().rsplit("\n\n", 1)[-1].strip()
    else:
        post, short = body_md, ""
    post = post.strip()
    if not short:
        short = post if len(post) <= 280 else post[:277] + "…"
    return post, short


def configured_publishers(settings: Settings | None = None) -> list[Publisher]:
    """Every channel whose credentials are present, in posting order."""
    s = settings or Settings()
    out: list[Publisher] = []
    if s.bluesky_handle and s.bluesky_app_password:
        from toast.publishers.bluesky import BlueskyPublisher

        out.append(BlueskyPublisher(s))
    if s.x_api_key and s.x_api_secret and s.x_access_token and s.x_access_secret:
        from toast.publishers.x import XPublisher

        out.append(XPublisher(s))
    if s.telegram_bot_token and s.telegram_channel_id:
        from toast.publishers.telegram import TelegramPublisher

        out.append(TelegramPublisher(s))
    if s.fb_page_id and s.fb_page_token:
        from toast.publishers.facebook import FacebookPublisher

        out.append(FacebookPublisher(s))
    return out


def get_publisher(channel: str, settings: Settings | None = None) -> Publisher:
    for p in configured_publishers(settings):
        if p.channel == channel:
            return p
    raise PublishError(f"channel {channel!r} not configured — see docs/API-KEYS.md")
