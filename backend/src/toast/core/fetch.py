"""Safe HTTP fetcher — every outbound request from ingest goes through here.

SSRF guard: resolve DNS first, reject private/link-local/loopback/reserved
addresses, and re-check on every redirect hop. Size and time caps on all
downloads. All ingested content is hostile; this is the first gate.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

MAX_BYTES = 20 * 1024 * 1024  # 20 MB cap for any single fetch
TIMEOUT_S = 30.0
USER_AGENT = "ToastReport/0.1 (+https://github.com/Fablestarexpanse/ToastReportOps)"


class FetchError(Exception):
    pass


class BlockedURLError(FetchError):
    pass


def _assert_public_host(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise BlockedURLError(f"scheme not allowed: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise BlockedURLError("no host in URL")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise FetchError(f"DNS resolution failed for {host!r}") from exc
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            raise BlockedURLError(f"{host!r} resolves to non-public address {addr}")


def fetch(
    url: str,
    *,
    accept_types: tuple[str, ...] = (),
    max_bytes: int = MAX_BYTES,
    timeout_s: float = TIMEOUT_S,
    headers: dict[str, str] | None = None,
) -> tuple[bytes, str]:
    """GET url with SSRF/size/time/content-type guards.

    Returns (body, content_type). Redirects are followed manually so every hop
    passes the public-host check. `headers` overlays the default User-Agent
    (some official APIs reject non-browser UAs).
    """
    body = b""
    content_type = ""
    merged_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    with httpx.Client(timeout=timeout_s, follow_redirects=False, headers=merged_headers) as client:
        current = url
        for _hop in range(5):
            _assert_public_host(current)
            with client.stream("GET", current) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        raise FetchError("redirect with no Location")
                    current = str(httpx.URL(current).join(location))
                    continue
                if resp.status_code >= 400:
                    raise FetchError(f"HTTP {resp.status_code} from {current}")
                content_type = resp.headers.get("content-type", "").split(";")[0].strip()
                if accept_types and content_type not in accept_types:
                    raise FetchError(f"unexpected content-type {content_type!r} from {current}")
                declared = resp.headers.get("content-length")
                if declared and int(declared) > max_bytes:
                    raise FetchError(f"content-length {declared} exceeds cap")
                chunks = []
                received = 0
                for chunk in resp.iter_bytes():
                    received += len(chunk)
                    if received > max_bytes:
                        raise FetchError(f"response exceeded {max_bytes} byte cap")
                    chunks.append(chunk)
                body = b"".join(chunks)
                return body, content_type
        raise FetchError("too many redirects")
