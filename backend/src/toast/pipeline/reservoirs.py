"""California reservoir levels from DWR's CDEC conditions report.

One public page (cdec.water.ca.gov/reportapp/javareports?name=RES) lists every
major reservoir with capacity, storage, % of capacity, % of the historical
average, inflow/outflow and the same date last year. We keep a daily snapshot of
each, so the board can show a trend and the phone can hear when one nears full.

Doctrine, as everywhere: official numbers relayed as published. We do not model
flood-control space (it follows a seasonal rule curve we do not hold), forecast
spills, or advise; "near capacity" is a plain threshold on the official
percentage. A `---` in the report is unknown, never zero, and the report is the
last published *daily* value — the board says so.
"""

from __future__ import annotations

import html as html_lib
import sqlite3
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from toast.core.appsettings import get_setting
from toast.core.config import Settings
from toast.core.db import utcnow
from toast.core.fetch import fetch
from toast.core.logging import get_logger

REPORT_URL = "https://cdec.water.ca.gov/reportapp/javareports?name=RES"
STATE_KEY = "reservoir:notify:state"
REFRESH_AFTER_S = 3 * 3600
MIN_RESERVOIRS = 20  # a page yielding fewer than this has changed shape — don't trust it
NEAR_FULL_PCT = 90.0
NEAR_FULL_CLEAR_PCT = 85.0  # hysteresis, so 89↔91 doesn't alert every refresh
LOW_VS_AVERAGE_PCT = 70.0
TIMEZONE = ZoneInfo("America/Los_Angeles")

_COLUMNS = (
    "name",
    "station",
    "capacity_af",
    "elevation_ft",
    "storage_af",
    "storage_change_af",
    "pct_capacity",
    "avg_storage_af",
    "pct_average",
    "outflow_cfs",
    "inflow_cfs",
    "year_ago_af",
)


def _num(text: str) -> float | None:
    t = text.replace(",", "").strip()
    if not t or t.startswith("-") and not t[1:2].isdigit():
        return None  # '---' / blank: no reading
    try:
        return float(t)
    except ValueError:
        return None


def _title(text: str) -> str:
    return text.title().replace("'S", "'s")


def parse_report(page: str) -> list[dict[str, Any]]:
    """Reservoirs from the report HTML. Basin header rows have one cell; data
    rows have twelve. Anything else is ignored rather than guessed at."""
    soup = BeautifulSoup(page, "html.parser")
    table = soup.find("table")
    if table is None:
        return []
    basin: str | None = None
    out: list[dict[str, Any]] = []
    for row in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
        if len(cells) == 1:
            if cells[0] != "Water Storage":
                basin = _title(cells[0])
            continue
        if len(cells) != len(_COLUMNS) or cells[1] == "StaID":
            continue
        rec: dict[str, Any] = {
            "name": _title(cells[0]),
            "station": cells[1].upper(),
            "basin": basin,
        }
        for col, raw in zip(_COLUMNS[2:], cells[2:], strict=True):
            rec[col] = _num(raw)
        out.append(rec)
    return out


def _today() -> str:
    return datetime.now(TIMEZONE).strftime("%Y-%m-%d")


def refresh_reservoirs(conn: sqlite3.Connection) -> int:
    """Fetch the report and upsert today's snapshot. Raises if the page no longer
    parses — an old good reading beats a fresh empty one."""
    body, _ctype = fetch(REPORT_URL, accept_types=("text/html",), max_bytes=2 * 1024 * 1024)
    rows = parse_report(body.decode("utf-8", errors="replace"))
    if len(rows) < MIN_RESERVOIRS:
        raise RuntimeError(f"reservoir report parsed {len(rows)} rows — page layout changed?")
    day, now = _today(), utcnow()
    conn.executemany(
        """INSERT INTO reservoir_readings
             (station, day, name, basin, capacity_af, elevation_ft, storage_af,
              storage_change_af, pct_capacity, avg_storage_af, pct_average,
              outflow_cfs, inflow_cfs, captured_at)
           VALUES (:station, :day, :name, :basin, :capacity_af, :elevation_ft, :storage_af,
                   :storage_change_af, :pct_capacity, :avg_storage_af, :pct_average,
                   :outflow_cfs, :inflow_cfs, :captured_at)
           ON CONFLICT(station, day) DO UPDATE SET
             name = excluded.name, basin = excluded.basin,
             capacity_af = excluded.capacity_af, elevation_ft = excluded.elevation_ft,
             storage_af = excluded.storage_af, storage_change_af = excluded.storage_change_af,
             pct_capacity = excluded.pct_capacity, avg_storage_af = excluded.avg_storage_af,
             pct_average = excluded.pct_average, outflow_cfs = excluded.outflow_cfs,
             inflow_cfs = excluded.inflow_cfs, captured_at = excluded.captured_at""",
        [{**r, "day": day, "captured_at": now} for r in rows],
    )
    conn.commit()
    get_logger("reservoirs").info("reservoirs_refreshed", count=len(rows), day=day)
    return len(rows)


def _pinned(conn: sqlite3.Connection) -> set[str]:
    raw = get_setting(conn, "water_watch_lids") or ""
    return {t.strip().upper() for t in raw.replace(";", ",").split(",") if t.strip()}


def latest(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Newest snapshot of each reservoir, with the change in % of capacity over
    the last ~7 days (None until we hold that much history) and status flags."""
    pinned = _pinned(conn)
    rows = conn.execute(
        """SELECT r.*, (SELECT p.pct_capacity FROM reservoir_readings p
                        WHERE p.station = r.station
                          AND p.day <= date(r.day, '-6 day') AND p.pct_capacity IS NOT NULL
                        ORDER BY p.day DESC LIMIT 1) AS pct_capacity_week_ago
           FROM reservoir_readings r
           WHERE r.day = (SELECT MAX(day) FROM reservoir_readings m WHERE m.station = r.station)
           ORDER BY r.basin, r.name"""
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        wk = d.pop("pct_capacity_week_ago")
        pct, avg = d["pct_capacity"], d["pct_average"]
        d["change_7d_pts"] = None if wk is None or pct is None else round(pct - wk, 1)
        d["near_full"] = pct is not None and pct >= NEAR_FULL_PCT
        d["below_average"] = avg is not None and avg < LOW_VS_AVERAGE_PCT
        d["pinned"] = d["station"] in pinned
        out.append(d)
    return out


def get_reservoirs(conn: sqlite3.Connection, *, refresh: bool = False) -> dict[str, Any]:
    """The RESERVOIRS board. Refreshes when stale or asked; if the fetch fails it
    serves the last stored snapshot and says so."""
    newest = conn.execute(
        "SELECT MAX(captured_at) AS t, "
        "CAST(unixepoch('now') - unixepoch(MAX(captured_at)) AS INTEGER) AS age "
        "FROM reservoir_readings"
    ).fetchone()
    error: str | None = None
    if refresh or newest["t"] is None or (newest["age"] or 0) > REFRESH_AFTER_S:
        try:
            refresh_reservoirs(conn)
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:120]}"
            get_logger("reservoirs").warning("reservoir_refresh_failed", error=error)
    items = latest(conn)
    fetched_at = conn.execute("SELECT MAX(captured_at) AS t FROM reservoir_readings").fetchone()[
        "t"
    ]
    total_storage = sum(r["storage_af"] or 0 for r in items)
    total_capacity = sum(r["capacity_af"] or 0 for r in items if r["storage_af"] is not None)
    return {
        "reservoirs": items,
        "fetched_at": fetched_at,
        "error": error,
        "statewide_pct_capacity": round(100 * total_storage / total_capacity, 1)
        if total_capacity
        else None,
        "note": "DWR/CDEC daily values, relayed as published. Capacity percentages are "
        "of total capacity, not of flood-control limits. '—' means no reading.",
    }


def notify_reservoirs(
    conn: sqlite3.Connection,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Phone alert the first time a reservoir reaches NEAR_FULL_PCT, and again only
    after it has dropped below NEAR_FULL_CLEAR_PCT. First run baselines silently."""
    from toast.pipeline import telegram_ops

    state = telegram_ops._state(conn, STATE_KEY)
    first_run = state is None
    high: set[str] = set((state or {}).get("high", []))
    messages: list[str] = []
    for r in latest(conn):
        pct = r["pct_capacity"]
        if pct is None:
            continue
        if pct >= NEAR_FULL_PCT and r["station"] not in high:
            high.add(r["station"])
            messages.append(
                f"💧 <b>{html_lib.escape(r['name'])}</b> at {pct:.0f}% of capacity "
                f"({r['storage_af']:,.0f} AF). Official figures — see the workstation."
            )
        elif pct < NEAR_FULL_CLEAR_PCT:
            high.discard(r["station"])
    sent = 0
    cfg = telegram_ops._cfg(settings)
    if cfg and messages and not first_run:
        token, chat = cfg
        own = client is None
        client = client or httpx.Client(timeout=30)
        try:
            for m in messages:
                if telegram_ops._send(client, token, chat, m):
                    sent += 1
        finally:
            if own:
                client.close()
    telegram_ops._save(conn, STATE_KEY, {"high": sorted(high)})
    return {"sent": sent, "baselined": first_run}


__all__ = ["get_reservoirs", "latest", "notify_reservoirs", "parse_report", "refresh_reservoirs"]
