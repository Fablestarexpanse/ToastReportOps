"""Browser-origin guard for the unauthenticated, loopback-only operator API.

The API has no login — the trust boundary is "only the operator's own browser
tab can reach it". That boundary leaks two ways, and both work from any web
page the operator happens to have open:

* CSRF — a page on evil.example POSTs to http://127.0.0.1:8400/... (a "simple"
  request needs no preflight, and several action routes take no body at all).
* DNS rebinding — evil.example re-resolves to 127.0.0.1 and the browser then
  treats the API as same-origin.

So: unsafe methods must not carry a foreign Origin, and every request must
carry a Host we recognise. Requests with no Origin (curl, the UI's server-side
proxy, other containers) are not a browser-CSRF vector and pass.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})


def _host_of(netloc: str) -> str:
    """'127.0.0.1:8400' -> '127.0.0.1'; '[::1]:8400' -> '[::1]'."""
    if netloc.startswith("["):
        return netloc.split("]", 1)[0].lower() + "]"
    return netloc.rsplit(":", 1)[0].lower() if netloc.count(":") == 1 else netloc.lower()


def origin_allowed(origin: str, extra_hosts: frozenset[str] = frozenset()) -> bool:
    """True for an Origin header whose host is loopback (any port) or allow-listed."""
    if not origin or origin == "null":
        return False
    host = _host_of(urlsplit(origin).netloc)
    return host in LOOPBACK_HOSTS or host in extra_hosts


def install_origin_guard(app: FastAPI, extra_hosts: frozenset[str] = frozenset()) -> None:
    """`extra_hosts`: additional bare hostnames (e.g. the compose service name
    `api`) accepted in Host / Origin."""
    allowed = LOOPBACK_HOSTS | extra_hosts

    @app.middleware("http")
    async def _guard(request: Request, call_next):
        host = _host_of(request.headers.get("host", ""))
        if host not in allowed:
            return JSONResponse({"detail": "host not allowed"}, status_code=403)
        origin = request.headers.get("origin")
        if (
            origin is not None
            and request.method not in SAFE_METHODS
            and not origin_allowed(origin, extra_hosts)
        ):
            return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        return await call_next(request)
