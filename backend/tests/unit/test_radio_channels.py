"""The scanner channel directory + the county board.

Channels are operator configuration (like sources) — creatable, editable,
deletable. The board organizes them under active-fire counties, most urgent
first, with Broadcastify as computed link-outs (operator decision: their terms
prohibit embedding; OpenMHz and operator-supplied streams play in-app)."""

import json

from toast.api import routes

NOW = "2026-07-19T00:00:00.000Z"


class Req:
    def __init__(self, conn):
        self.app = type("A", (), {"state": type("S", (), {"conn": conn})()})()


def _mk(db, **over):
    body = {
        "name": "Sac Metro Fire",
        "kind": "openmhz",
        "system": "srrcsalt",
        "talkgroups": [1627, 1283],
        "county": "Sacramento",
    }
    body.update(over)
    return routes.radio_channel_create(Req(db), body)


def _fire(db, iid, name, county, acreage=None):
    db.execute(
        "INSERT INTO incidents (id, canonical_name, county, status, created_at, updated_at)"
        " VALUES (?, ?, ?, 'active', ?, ?)",
        (iid, name, county, NOW, NOW),
    )
    if acreage is not None:
        from toast.core.ids import ulid

        db.execute(
            "INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content,"
            " kind, acreage, confidence, observed_at, ingested_at)"
            " VALUES (?, ?, 'e1', 's1', 1, 'x', 'acreage', ?, 0.9, ?, ?)",
            (ulid(), iid, acreage, NOW, NOW),
        )


def test_create_and_list_channel(seeded):
    r = _mk(seeded)
    assert r["ok"]
    chans = routes.radio_channels_list(Req(seeded))["channels"]
    assert len(chans) == 1
    c = chans[0]
    assert c["kind"] == "openmhz" and c["system"] == "srrcsalt"
    assert c["talkgroups"] == [1627, 1283]  # JSON round-trips as a real list
    assert c["enabled"] is True


def test_stream_and_link_need_urls(seeded):
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        _mk(seeded, kind="stream", url=None)
    with pytest.raises(HTTPException):
        _mk(seeded, kind="link", url="not-a-url")
    r = _mk(seeded, kind="stream", url="https://example.org/live.mp3", system=None)
    assert r["ok"]


def test_update_upgrades_link_to_stream(seeded):
    """The operator pastes a stream URL onto a link channel — it becomes
    in-app playable. This is the Broadcastify upgrade path."""
    cid = _mk(seeded, kind="link", url="https://www.broadcastify.com/listen/feed/1")["id"]
    routes.radio_channel_update(
        Req(seeded), cid, {"kind": "stream", "url": "https://audio.example/feed1"}
    )
    c = routes.radio_channels_list(Req(seeded))["channels"][0]
    assert c["kind"] == "stream" and c["url"] == "https://audio.example/feed1"


def test_delete_channel(seeded):
    cid = _mk(seeded)["id"]
    routes.radio_channel_delete(Req(seeded), cid)
    assert routes.radio_channels_list(Req(seeded))["channels"] == []


def test_board_groups_by_active_fire_county_most_urgent_first(seeded):
    _fire(seeded, "i-lassen", "BISCAR", "Lassen", acreage=69000)
    _fire(seeded, "i-yuba", "Chute Fire", "Yuba", acreage=125)
    # an evac zone on the smaller fire outranks raw acreage
    seeded.execute(
        "INSERT OR REPLACE INTO map_cache (key, payload, fetched_at) VALUES (?, ?, ?)",
        (
            "evaczones:ca",
            json.dumps(
                {
                    "features": [
                        {
                            "geometry": {"type": "Point", "coordinates": [-121.4, 39.2]},
                            "properties": {
                                "ZONE_ID": "YUB-E098",
                                "STATUS": "Evacuation Order",
                                "incident_id": "i-yuba",
                            },
                        }
                    ]
                }
            ),
            NOW.replace("2026-07-19", "2100-01-01"),  # far future = fresh cache
        ),
    )
    _mk(seeded, county="Yuba", name="Yuba fire dispatch")
    board = routes.radio_board(Req(seeded))
    names = [c["county"] for c in board["counties"]]
    assert names[0] == "Yuba"  # evac beats acreage
    yuba = board["counties"][0]
    assert yuba["fires"][0]["name"] == "Chute Fire"
    assert yuba["channels"][0]["name"] == "Yuba fire dispatch"
    assert "broadcastify.com/search" in yuba["broadcastify_search"]


def test_board_keeps_quiet_county_channels_visible(seeded):
    _mk(seeded, county="Butte", name="Butte watch")  # no active Butte fire
    board = routes.radio_board(Req(seeded))
    butte = next(c for c in board["counties"] if c["county"] == "Butte")
    assert butte["fires"] == [] and butte["channels"][0]["name"] == "Butte watch"


def test_statewide_channels_ride_separately(seeded):
    _mk(seeded, county=None, name="ScanCal live", kind="link",
        url="https://www.scancal.org/live.html", system=None)
    board = routes.radio_board(Req(seeded))
    assert [c["name"] for c in board["statewide"]] == ["ScanCal live"]


def test_seed_radio_channels_idempotent(db):
    from toast.seed import seed_radio_channels

    first = seed_radio_channels(db)
    assert len(first) == 4
    assert seed_radio_channels(db) == []  # second run adds nothing
    kinds = {r["kind"] for r in db.execute("SELECT kind FROM radio_channels")}
    # link-outs (Broadcastify et al., per policy) + the operator's own live
    # SDR stream slot, waiting on hardware
    assert kinds == {"link", "stream"}
    sdr = db.execute(
        "SELECT county, url FROM radio_channels WHERE kind = 'stream'"
    ).fetchone()
    assert sdr["county"] == "Butte"
    assert sdr["url"] == "http://localhost:8100/calfire-btu"
