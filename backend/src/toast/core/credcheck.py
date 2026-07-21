"""Live credential tests — does this key actually work?

"Configured" and "working" are different things, and the difference matters at
3am. Each probe makes the cheapest real call the provider offers and maps the
result to ok / rejected / error:

  ok         — the provider accepted the credentials
  rejected   — the provider answered, and said no (401/403/bad key)
  error      — we could not reach the provider (network, DNS, outage)

That distinction is the useful one: 'rejected' means fix your key, 'error'
means try again later. Probes are read-only — nothing is posted, uploaded, or
published while testing.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import httpx

from toast.core.credentials import get_credential

TIMEOUT = 20.0


def _result(status: str, message: str) -> dict[str, str]:
    return {"status": status, "message": message}


def _http_verdict(code: int, ok_message: str, body: str = "") -> dict[str, str]:
    if code in (200, 201):
        return _result("ok", ok_message)
    if code in (400, 401, 403):
        return _result("rejected", f"provider rejected the credentials (HTTP {code}) {body[:80]}")
    return _result("error", f"unexpected response (HTTP {code})")


def check_firms(conn: sqlite3.Connection, client: httpx.Client) -> dict[str, str]:
    key = get_credential(conn, "FIRMS_MAP_KEY")
    if not key:
        return _result("unconfigured", "no map key set")
    # FIRMS' own key-status endpoint: no data pulled, no quota burned
    r = client.get(f"https://firms.modaps.eosdis.nasa.gov/mapserver/mapkey_status/?MAP_KEY={key}")
    if r.status_code != 200:
        return _http_verdict(r.status_code, "", r.text)
    body = r.text.lower()
    if "invalid" in body or "not found" in body:
        return _result("rejected", "FIRMS says this map key is invalid")
    return _result("ok", "map key valid")


def check_bluesky(conn: sqlite3.Connection, client: httpx.Client) -> dict[str, str]:
    handle = get_credential(conn, "BLUESKY_HANDLE")
    password = get_credential(conn, "BLUESKY_APP_PASSWORD")
    if not (handle and password):
        return _result("unconfigured", "handle and app password required")
    r = client.post(
        "https://bsky.social/xrpc/com.atproto.server.createSession",
        json={"identifier": handle, "password": password},
    )
    if r.status_code == 200:
        did = r.json().get("did", "")
        return _result("ok", f"signed in as {handle} ({did[:20]}…)")
    return _http_verdict(r.status_code, "", r.text)


def check_backblaze(conn: sqlite3.Connection, client: httpx.Client) -> dict[str, str]:
    key_id = get_credential(conn, "B2_KEY_ID")
    app_key = get_credential(conn, "B2_APPLICATION_KEY")
    bucket = get_credential(conn, "B2_BUCKET")
    if not (key_id and app_key and bucket):
        return _result("unconfigured", "key id, application key and bucket required")
    r = client.get(
        "https://api.backblazeb2.com/b2api/v3/b2_authorize_account", auth=(key_id, app_key)
    )
    if r.status_code != 200:
        return _http_verdict(r.status_code, "", r.text)
    auth = r.json()
    api = auth["apiInfo"]["storageApi"]
    listing = client.post(
        f"{api['apiUrl']}/b2api/v3/b2_list_buckets",
        headers={"Authorization": auth["authorizationToken"]},
        json={"accountId": auth["accountId"], "bucketName": bucket},
    )
    if listing.status_code != 200:
        return _http_verdict(listing.status_code, "", listing.text)
    if not listing.json().get("buckets"):
        return _result("rejected", f"authenticated, but bucket {bucket!r} was not found")
    return _result("ok", f"bucket {bucket!r} reachable — offsite backup ready")


def check_x(conn: sqlite3.Connection, client: httpx.Client) -> dict[str, str]:
    from toast.publishers.x import oauth1_header

    creds = {
        n: get_credential(conn, n)
        for n in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")
    }
    if not all(creds.values()):
        return _result("unconfigured", "all four X credentials required")
    url = "https://api.twitter.com/2/users/me"
    header = oauth1_header(
        "GET",
        url,
        consumer_key=creds["X_API_KEY"],
        consumer_secret=creds["X_API_SECRET"],
        token=creds["X_ACCESS_TOKEN"],
        token_secret=creds["X_ACCESS_SECRET"],
    )
    r = client.get(url, headers={"Authorization": header})
    if r.status_code == 200:
        username = r.json().get("data", {}).get("username", "")
        return _result("ok", f"authenticated as @{username}" if username else "authenticated")
    return _http_verdict(r.status_code, "", r.text)


def check_facebook(conn: sqlite3.Connection, client: httpx.Client) -> dict[str, str]:
    page_id = get_credential(conn, "FB_PAGE_ID")
    token = get_credential(conn, "FB_PAGE_TOKEN")
    if not (page_id and token):
        return _result("unconfigured", "page id and token required")
    r = client.get(
        f"https://graph.facebook.com/v21.0/{page_id}",
        params={"fields": "name", "access_token": token},
    )
    if r.status_code == 200:
        return _result("ok", f"page: {r.json().get('name', page_id)}")
    return _http_verdict(r.status_code, "", r.text)


def check_airnow(conn: sqlite3.Connection, client: httpx.Client) -> dict[str, str]:
    key = get_credential(conn, "AIRNOW_API_KEY")
    if not key:
        return _result("unconfigured", "API key required")
    r = client.get(
        "https://www.airnowapi.org/aq/observation/zipCode/current/",
        params={"format": "application/json", "zipCode": "95969", "API_KEY": key},
    )
    return _http_verdict(r.status_code, "key valid", r.text)


def check_telegram(conn: sqlite3.Connection, client: httpx.Client) -> dict[str, str]:
    token = get_credential(conn, "TELEGRAM_BOT_TOKEN")
    if not token:
        return _result("unconfigured", "bot token required")
    r = client.get(f"https://api.telegram.org/bot{token}/getMe")
    if r.status_code == 200:
        name = (r.json().get("result") or {}).get("username", "bot")
        chan = get_credential(conn, "TELEGRAM_CHANNEL_ID")
        ops = get_credential(conn, "TELEGRAM_OPS_CHAT_ID")
        missing = [n for n, v in (("channel id", chan), ("ops chat id", ops)) if not v]
        note = f" (still needed: {', '.join(missing)})" if missing else ""
        return _result("ok", f"@{name} connected{note}")
    return _http_verdict(r.status_code, "", r.text)


CHECKS = {
    "firms": check_firms,
    "bluesky": check_bluesky,
    "backblaze": check_backblaze,
    "x": check_x,
    "facebook": check_facebook,
    "airnow": check_airnow,
    "telegram": check_telegram,
}


def run_check(
    conn: sqlite3.Connection, service: str, *, client: httpx.Client | None = None
) -> dict[str, Any]:
    """Probe one service and persist the verdict. `client` is for tests."""
    from toast.core.credentials import record_check

    fn = CHECKS.get(service)
    if fn is None:
        raise ValueError(f"unknown service {service!r}")
    owned = client is None
    client = client or httpx.Client(timeout=TIMEOUT, follow_redirects=True)
    try:
        result = fn(conn, client)
    except httpx.HTTPError as exc:
        # unreachable provider is NOT a bad credential — keep them distinct
        result = _result("error", f"could not reach the provider: {type(exc).__name__}")
    finally:
        if owned:
            client.close()
    return record_check(conn, service, result["status"], result["message"])
