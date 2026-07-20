"""X (Twitter) publisher — POST /2/tweets with OAuth 1.0a user context.

Free write tier (~500 posts/month) is plenty for incident updates. OAuth 1.0a
signing is implemented directly (HMAC-SHA1) to avoid another dependency;
the signature covers only oauth_* params because the v2 endpoint takes a
JSON body (which is excluded from the OAuth 1.0a signature base string).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from typing import Any
from urllib.parse import quote

import httpx

from toast.core.config import Settings

API = "https://api.twitter.com"
MAX_CHARS = 280


def _pct(s: str) -> str:
    return quote(s, safe="~")


def oauth1_header(
    method: str,
    url: str,
    *,
    consumer_key: str,
    consumer_secret: str,
    token: str,
    token_secret: str,
    nonce: str | None = None,
    timestamp: str | None = None,
) -> str:
    """Build an OAuth 1.0a Authorization header for a JSON-body request."""
    params = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": timestamp or str(int(time.time())),
        "oauth_token": token,
        "oauth_version": "1.0",
    }
    param_str = "&".join(f"{_pct(k)}={_pct(v)}" for k, v in sorted(params.items()))
    base = "&".join([method.upper(), _pct(url), _pct(param_str)])
    key = f"{_pct(consumer_secret)}&{_pct(token_secret)}"
    sig = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    params["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(params.items()))


class XPublisher:
    channel = "x"

    def __init__(self, settings: Settings, *, base_url: str = API) -> None:
        self.s = settings
        self.base_url = base_url
        self.http = httpx.Client(base_url=base_url, timeout=30)

    def publish(self, text: str) -> dict[str, Any]:
        from toast.publishers import PublishError

        if len(text) > MAX_CHARS:
            text = text[: MAX_CHARS - 1] + "…"
        url = f"{self.base_url}/2/tweets"
        header = oauth1_header(
            "POST",
            url,
            consumer_key=self.s.x_api_key,
            consumer_secret=self.s.x_api_secret,
            token=self.s.x_access_token,
            token_secret=self.s.x_access_secret,
        )
        r = self.http.post("/2/tweets", headers={"Authorization": header}, json={"text": text})
        if r.status_code not in (200, 201):
            raise PublishError(f"x post failed: HTTP {r.status_code} {r.text[:120]}")
        tweet_id = r.json().get("data", {}).get("id", "")
        return {
            "id": tweet_id,
            "url": f"https://x.com/i/web/status/{tweet_id}" if tweet_id else None,
        }
