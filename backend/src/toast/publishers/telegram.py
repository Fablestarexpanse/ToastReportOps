"""Telegram publisher — Bot API sendMessage/sendPhoto to a public channel.

The friendliest publishing target: no review process, no length anxiety
(4,096 chars), native image support, and the same bot doubles as the private
ops hub (pipeline/telegram_ops.py). Only the channel id below is public-facing;
the ops chat is a different room entirely.
"""

from __future__ import annotations

from typing import Any

import httpx

from toast.core.config import Settings

API = "https://api.telegram.org"
MAX_CHARS = 4000  # API cap is 4096; leave headroom for safety


class TelegramPublisher:
    channel = "telegram"

    def __init__(self, settings: Settings, *, base_url: str = API) -> None:
        self.token = settings.telegram_bot_token
        self.chat = settings.telegram_channel_id
        self.http = httpx.Client(base_url=base_url, timeout=30)

    def publish(
        self, text: str, image: bytes | None = None, image_alt: str = ""
    ) -> dict[str, Any]:
        from toast.publishers import PublishError

        if len(text) > MAX_CHARS:
            text = text[: MAX_CHARS - 1] + "…"

        if image is not None:
            # caption cap is 1024 — a longer post sends as photo, then text
            caption = text if len(text) <= 1024 else None
            r = self.http.post(
                f"/bot{self.token}/sendPhoto",
                data={"chat_id": self.chat, **({"caption": caption} if caption else {})},
                files={"photo": ("card.png", image, "image/png")},
            )
            if r.status_code != 200:
                # the card is a bonus — fall through to a text post
                image = None
            elif caption is not None:
                return self._receipt(r.json())

        r = self.http.post(
            f"/bot{self.token}/sendMessage",
            json={"chat_id": self.chat, "text": text, "disable_web_page_preview": True},
        )
        if r.status_code != 200:
            raise PublishError(f"telegram post failed: HTTP {r.status_code} {r.text[:120]}")
        return self._receipt(r.json())

    def _receipt(self, body: dict[str, Any]) -> dict[str, Any]:
        msg = body.get("result") or {}
        chat = msg.get("chat") or {}
        username, mid = chat.get("username"), msg.get("message_id")
        return {
            "message_id": mid,
            "url": f"https://t.me/{username}/{mid}" if username and mid else None,
        }
