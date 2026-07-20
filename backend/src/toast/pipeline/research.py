"""Active research layer — Local Deep Research (LDR) client + draft landing.

LDR runs agentic multi-engine research (via SearXNG) on our Ollama and returns
a cited report. LEDGER BOUNDARY: LDR output is research material for the
operator — it lands as a `kind='analysis'` DRAFT (reviewed like any draft),
never as ledger facts. Facts enter the ledger only via the validated
point-at-URL path over LDR's cited sources.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

import httpx

from toast.core.config import Settings
from toast.core.db import utcnow
from toast.core.ids import ulid


class ResearchError(Exception):
    pass


class LDRClient:
    """Minimal client for LDR's session/CSRF web API.

    Use get_client() — one instance per worker process, so the session cookie
    is reused and we log in once, not once per job (LDR's login rate-limiter
    punishes repeated logins hard)."""

    def __init__(self, settings: Settings | None = None) -> None:
        s = settings or Settings()
        self.base = s.ldr_url.rstrip("/")
        self.username = s.ldr_username
        self.password = s.ldr_password
        self.http = httpx.Client(base_url=self.base, follow_redirects=True, timeout=60)
        self._authed = False

    def _form_csrf(self, path: str) -> str:
        r = self.http.get(path)
        if r.status_code != 200:
            raise ResearchError(f"LDR {path} unavailable: HTTP {r.status_code}")
        m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r.text)
        if not m:
            raise ResearchError(f"no csrf token on {path}")
        return m.group(1)

    def _logged_in(self) -> bool:
        r = self.http.get("/auth/csrf-token")
        if r.status_code != 200:
            return False
        tok = r.json().get("csrf_token", "")
        probe = self.http.get("/history/api", headers={"X-CSRF-Token": tok})
        return probe.status_code == 200

    def login_or_register(self) -> None:
        """Idempotently ensure the service account exists and is logged in.
        Reuses the existing session when still valid — no extra logins."""
        if self._authed and self._logged_in():
            return
        csrf = self._form_csrf("/auth/login")
        r = self.http.post(
            "/auth/login",
            data={"csrf_token": csrf, "username": self.username, "password": self.password},
        )
        if r.status_code == 429:
            raise ResearchError("LDR rate-limited; try again in a few minutes")
        if self._logged_in():
            self._authed = True
            return
        # first run: register the service account
        csrf = self._form_csrf("/auth/register")
        r = self.http.post(
            "/auth/register",
            data={
                "csrf_token": csrf,
                "username": self.username,
                "password": self.password,
                "confirm_password": self.password,
                "acknowledge": "on",
            },
        )
        if r.status_code == 429:
            raise ResearchError("LDR rate-limited; try again in a few minutes")
        if not self._logged_in():
            raise ResearchError(f"LDR login/register failed (HTTP {r.status_code})")
        self._authed = True

    def _api_token(self) -> str:
        return self.http.get("/auth/csrf-token").json()["csrf_token"]

    def start(self, query: str) -> str:
        s = Settings()
        r = self.http.post(
            "/api/start_research",
            json={
                "query": query,
                "search_engines": ["searxng"],
                # pin to our local model — LDR's own default may not be pulled
                "model": s.llm_model,
                "model_provider": "OLLAMA",
            },
            headers={"X-CSRF-Token": self._api_token()},
        )
        if r.status_code != 200:
            raise ResearchError(f"start_research failed: HTTP {r.status_code} {r.text[:150]}")
        data = r.json()
        research_id = data.get("research_id") or data.get("id")
        if not research_id:
            raise ResearchError(f"no research_id in response: {str(data)[:150]}")
        return str(research_id)

    def status(self, research_id: str) -> dict[str, Any]:
        r = self.http.get(
            f"/api/research/{research_id}/status", headers={"X-CSRF-Token": self._api_token()}
        )
        if r.status_code != 200:
            raise ResearchError(f"status failed: HTTP {r.status_code}")
        return r.json()

    def report(self, research_id: str) -> dict[str, Any]:
        r = self.http.get(f"/api/report/{research_id}", headers={"X-CSRF-Token": self._api_token()})
        if r.status_code != 200:
            raise ResearchError(f"report failed: HTTP {r.status_code}")
        return r.json()


_client: LDRClient | None = None


def get_client() -> LDRClient:
    """Process-level singleton — one login per worker lifetime, cookies reused."""
    global _client
    if _client is None:
        _client = LDRClient()
    return _client


def land_analysis_draft(
    conn: sqlite3.Connection,
    *,
    query: str,
    report: dict[str, Any],
    incident_id: str | None,
) -> str:
    """Store a completed LDR report as an analysis draft for operator review."""
    content = str(report.get("content") or report.get("summary") or "").strip()
    sources = report.get("sources") or []
    lines = [content, "", "---", "", "**Research sources** (auto-gathered — verify before use)", ""]
    for i, s in enumerate(sources, start=1):
        title = (s.get("title") or s.get("url") or "source").strip()
        url = s.get("url") or ""
        lines.append(f"{i}. {title}" + (f" — {url}" if url else ""))
    body = "\n".join(lines).strip()

    draft_id = ulid()
    now = utcnow()
    conn.execute(
        """INSERT INTO drafts (id, incident_id, kind, status, title, body_md,
                               cited_fact_ids, created_at, updated_at)
           VALUES (?, ?, 'analysis', 'draft', ?, ?, '[]', ?, ?)""",
        (draft_id, incident_id, f"RESEARCH — {query[:80]}", body, now, now),
    )
    return draft_id
