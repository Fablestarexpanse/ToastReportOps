"""Minimal /healthz endpoint for non-API services (workers, scheduler).

Stdlib HTTP server on a background thread; binds localhost only. The service
updates its HealthState; /healthz returns 200 while the main loop is beating.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STALE_AFTER_S = 30.0


class HealthState:
    def __init__(self, service: str) -> None:
        self.service = service
        self._beat = time.monotonic()

    def beat(self) -> None:
        self._beat = time.monotonic()

    def healthy(self) -> bool:
        return (time.monotonic() - self._beat) < STALE_AFTER_S


def serve_health(state: HealthState, port: int, bind: str = "127.0.0.1") -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path != "/healthz":
                self.send_error(404)
                return
            ok = state.healthy()
            body = json.dumps({"service": state.service, "ok": ok}).encode()
            self.send_response(200 if ok else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:  # health probes stay out of logs
            pass

    server = ThreadingHTTPServer((bind, port), Handler)
    thread = threading.Thread(target=server.serve_forever, name="healthz", daemon=True)
    thread.start()
    return server
