"""Operator API — FastAPI, localhost-bound by default.

Phase 0 surface: /healthz plus a read-only queue snapshot so the operator UI
health dots have something real to show. Incident/ledger routes arrive in
Phase 1.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from toast import __version__
from toast.core.config import Settings
from toast.core.db import ThreadConnections, apply_migrations, connect
from toast.core.logging import configure_logging, get_logger
from toast.queue.jobs import counts_by_state


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    configure_logging(settings.log_level, service="api")
    conn = connect(settings.toast_db_path, check_same_thread=False)
    apply_migrations(conn)
    # populate the source-adapter registry so /sources (create) can validate
    from toast.sources import load_builtin_adapters

    load_builtin_adapters()
    app.state.conn = conn
    app.state.connections = ThreadConnections(settings.toast_db_path)
    app.state.settings = settings
    get_logger("api").info("api_started", db=str(settings.toast_db_path))
    yield
    conn.close()


app = FastAPI(title="Toast Report OPs API", version=__version__, lifespan=lifespan)

from toast.api.guard import install_origin_guard  # noqa: E402
from toast.api.routes import router  # noqa: E402

install_origin_guard(
    app, frozenset(h.strip().lower() for h in Settings().api_allowed_hosts.split(",") if h.strip())
)
app.include_router(router)

# Serve the generated static site for local preview. There is no production
# host: publishing is manual by operator decision (see DECISIONS.md 2026-07-20).
# Pages). The dir is created so the mount succeeds before the first build.
from fastapi.staticfiles import StaticFiles  # noqa: E402

_site = Settings().site_dir
_site.mkdir(parents=True, exist_ok=True)
app.mount("/site", StaticFiles(directory=str(_site), html=True), name="site")


@app.get("/healthz")
def healthz() -> dict:
    return {"service": "api", "ok": True, "version": __version__}


@app.get("/queue")
def queue() -> dict:
    return {"jobs": counts_by_state(app.state.connections.get())}


def main() -> None:
    import uvicorn

    settings = Settings()
    uvicorn.run(
        "toast.api.app:app", host=settings.api_bind, port=settings.api_port, log_level="info"
    )


if __name__ == "__main__":
    main()
