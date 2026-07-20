"""Safe HTTP fetcher — every outbound request from ingest goes through here.

SSRF guard: resolve DNS first, reject private/link-local/loopback/reserved
addresses, and re-check on every redirect hop. Size and time caps on all
downloads. All ingested content is hostile; this is the first gate.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

MAX_BYTES = 20 * 1024 * 1024  # 20 MB cap for any single fetch
TIMEOUT_S = 30.0
USER_AGENT = "ToastReport/0.1 (+https://github.com/Fablestarexpanse/ToastReportOps)"


class FetchError(Exception):
    pass


class BlockedURLError(FetchError):
    pass


class RobotsDisallowedError(FetchError):
    pass


def robots_allows(url: str, *, user_agent: str = USER_AGENT) -> bool:
    """Check the target's robots.txt. Fails open (allow) if robots is
    unreachable — we never block ingest on a flaky robots fetch, only on an
    explicit Disallow. Callers honor this unless the source sets robots_override."""
    parsed = urlparse(url)
    robots_url = urljoin(f"{parsed.scheme}://{parsed.netloc}", "/robots.txt")
    try:
        _assert_public_host(robots_url)
        with httpx.Client(timeout=10.0, headers={"User-Agent": user_agent}) as client:
            resp = client.get(robots_url)
        if resp.status_code >= 400:
            return True  # no robots or error → allowed
        rp = RobotFileParser()
        rp.parse(resp.text.splitlines())
        return rp.can_fetch(user_agent, url)
    except Exception:
        return True


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


# Query parameter names that carry credentials in the wild. Values are masked
# before a URL can reach an exception message, a log line, or jobs.last_error.
SECRET_PARAMS = re.compile(r"^(api_?key|key|token|access_token|auth|password|secret|sig)$", re.I)


def redact_url(url: str, *, secrets: tuple[str, ...] = ()) -> str:
    """A URL safe to put in an error message.

    Two mechanisms, because credentials hide in two places. Query parameters are
    masked by name. Path segments cannot be recognised by shape — FIRMS puts the
    map key in the PATH (/api/area/csv/<key>/...) — so the caller passes the
    secret values it knows about and they are masked literally, wherever they
    appear.

    This matters because a FetchError string travels a long way: worker logs it,
    jobs.fail writes it into jobs.last_error, and the nightly snapshot of that
    table is uploaded offsite. One 401 would otherwise send a live key to B2.
    """
    out = url
    for secret in secrets:
        if secret:
            out = out.replace(secret, "[REDACTED]")
    try:
        parsed = httpx.URL(out)
    except Exception:
        return out
    params = parsed.params
    masked = {k: ("[REDACTED]" if SECRET_PARAMS.match(k) else v) for k, v in params.multi_items()}
    if masked != dict(params.multi_items()):
        out = str(parsed.copy_with(params=httpx.QueryParams(masked)))
    return out


def fetch(
    url: str,
    *,
    accept_types: tuple[str, ...] = (),
    max_bytes: int = MAX_BYTES,
    timeout_s: float = TIMEOUT_S,
    headers: dict[str, str] | None = None,
    secrets: tuple[str, ...] = (),
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
                    raise FetchError(
                        f"HTTP {resp.status_code} from {redact_url(current, secrets=secrets)}"
                    )
                content_type = resp.headers.get("content-type", "").split(";")[0].strip()
                if accept_types and content_type not in accept_types:
                    raise FetchError(
                        f"unexpected content-type {content_type!r} from "
                        f"{redact_url(current, secrets=secrets)}"
                    )
                declared = resp.headers.get("content-length")
                # a malformed Content-Length must not raise an unhandled
                # ValueError out of the fetcher
                try:
                    declared_n = int(declared) if declared else 0
                except ValueError:
                    declared_n = 0
                if declared_n > max_bytes:
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
