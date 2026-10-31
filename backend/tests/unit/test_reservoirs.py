"""Reservoir report parsing + trend + near-full alerts."""

import json

import httpx

from toast.core.config import Settings
from toast.pipeline import reservoirs

SET = Settings(telegram_bot_token="123:abc", telegram_ops_chat_id="777")

HEAD = (
    "<tr><th>Reservoir Name</th><th>StaID</th><th>Capacity (AF)</th><th>Elevation (FT)</th>"
    "<th>Storage (AF)</th><th>Storage Change (AF)</th><th>% of Capacity</th>"
    "<th>Average Storage (AF)</th><th>% of Average</th><th>Outflow (CFS)</th>"
    "<th>Inflow (CFS)</th><th>Storage-Year Ago This Date (AF)</th></tr>"
)


def row(name, sta, cap, elev, stor, chg, pct, avg, pavg, out, inn, ya):
    cells = [name, sta, cap, elev, stor, chg, pct, avg, pavg, out, inn, ya]
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def page(*rows, extra_basin=True):
    body = "<tr><td>Water Storage</td></tr>" + HEAD + "<tr><td>FEATHER RIVER</td></tr>"
    return "<html><body><table>" + body + "".join(rows) + "</table></body></html>"


ORO = row(
    "OROVILLE",
    "ORO",
    "3,424,753",
    "735.79",
    "1,471,501",
    "-1,209",
    "43",
    "1,813,115",
    "81",
    "3,026",
    "2,456",
    "1,839,138",
)
GAP = row(
    "INDIAN VALLEY",
    "INV",
    "300,000",
    "---",
    "---",
    "---",
    "---",
    "117,016",
    "---",
    "---",
    "---",
    "194,474",
)
DON = row(
    "DONNELL'S",
    "DON",
    "64,320",
    "---",
    "16,667",
    "-115",
    "26",
    "28,730",
    "58",
    "124",
    "---",
    "14,323",
)


def test_parses_values_basin_and_gaps():
    rows = {r["station"]: r for r in reservoirs.parse_report(page(ORO, GAP, DON))}
    assert rows["ORO"]["storage_af"] == 1471501 and rows["ORO"]["basin"] == "Feather River"
    assert rows["INV"]["storage_af"] is None and rows["INV"]["pct_capacity"] is None  # unknown != 0
    assert rows["DON"]["name"] == "Donnell's" and rows["DON"]["inflow_cfs"] is None


def test_header_and_title_rows_are_not_reservoirs():
    stations = [r["station"] for r in reservoirs.parse_report(page(ORO))]
    assert stations == ["ORO"]


def test_garbage_page_yields_nothing():
    assert reservoirs.parse_report("<html>maintenance</html>") == []


def _fetch(monkeypatch, html):
    monkeypatch.setattr(reservoirs, "fetch", lambda *a, **k: (html.encode(), "text/html"))
    monkeypatch.setattr(reservoirs, "MIN_RESERVOIRS", 1)


def test_refresh_stores_and_same_day_upserts(db, monkeypatch):
    _fetch(monkeypatch, page(ORO, DON))
    assert reservoirs.refresh_reservoirs(db) == 2
    reservoirs.refresh_reservoirs(db)
    assert db.execute("SELECT COUNT(*) AS n FROM reservoir_readings").fetchone()["n"] == 2


def test_changed_page_layout_is_refused_not_stored(db, monkeypatch):
    monkeypatch.setattr(
        reservoirs, "fetch", lambda *a, **k: (b"<html>new design</html>", "text/html")
    )
    try:
        reservoirs.refresh_reservoirs(db)
    except RuntimeError as exc:
        assert "layout" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
    assert db.execute("SELECT COUNT(*) AS n FROM reservoir_readings").fetchone()["n"] == 0


def _snap(db, day, pct, station="ORO", cap=1000.0):
    db.execute(
        "INSERT INTO reservoir_readings (station, day, name, basin, capacity_af, storage_af,"
        " pct_capacity, pct_average, captured_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            station,
            day,
            "Oroville",
            "Feather River",
            cap,
            cap * pct / 100,
            pct,
            80,
            "2026-01-01T00:00:00Z",
        ),
    )


def test_latest_reports_seven_day_change_and_flags(db):
    _snap(db, "2026-01-01", 40)
    _snap(db, "2026-01-08", 92)
    r = reservoirs.latest(db)[0]
    assert r["pct_capacity"] == 92 and r["change_7d_pts"] == 52 and r["near_full"]


def test_no_trend_until_a_week_of_history_exists(db):
    _snap(db, "2026-01-08", 50)
    assert reservoirs.latest(db)[0]["change_7d_pts"] is None


def test_below_average_flag(db):
    db.execute(
        "INSERT INTO reservoir_readings"
        " (station, day, name, pct_capacity, pct_average, captured_at)"
        " VALUES ('X','2026-01-01','X',30,55,'t')"
    )
    assert reservoirs.latest(db)[0]["below_average"]


def _recorder():
    sent = []

    def handler(request):
        sent.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True})

    return sent, httpx.Client(transport=httpx.MockTransport(handler))


def test_near_full_alert_once_with_hysteresis(db):
    sent, client = _recorder()
    _snap(db, "2026-01-01", 50)
    assert reservoirs.notify_reservoirs(db, SET, client)["baselined"]

    db.execute("UPDATE reservoir_readings SET pct_capacity = 91, storage_af = 910")
    assert reservoirs.notify_reservoirs(db, SET, client)["sent"] == 1
    assert reservoirs.notify_reservoirs(db, SET, client)["sent"] == 0

    db.execute("UPDATE reservoir_readings SET pct_capacity = 88")  # inside the hysteresis band
    reservoirs.notify_reservoirs(db, SET, client)
    db.execute("UPDATE reservoir_readings SET pct_capacity = 91")
    assert reservoirs.notify_reservoirs(db, SET, client)["sent"] == 0

    db.execute("UPDATE reservoir_readings SET pct_capacity = 70")  # really dropped
    reservoirs.notify_reservoirs(db, SET, client)
    db.execute("UPDATE reservoir_readings SET pct_capacity = 93, storage_af = 930")
    assert reservoirs.notify_reservoirs(db, SET, client)["sent"] == 1
    assert "Oroville" in sent[0]
