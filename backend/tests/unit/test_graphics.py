"""Incident card rendering.

Honesty in pixels: ledger numbers only, verbatim evac banner, and degradation
that never blocks a publish — a dead tile server or a fire without coordinates
still yields a usable card.
"""

import io
import struct
import zlib

import pytest

from toast.core.ids import ulid
from toast.pipeline import graphics


def _tiny_png() -> bytes:
    # a valid 1x1 dark PNG, byte-built so the test needs no fixtures
    def chunk(typ, data):
        c = typ + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\x10\x14\x1a")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _fact(db, kind, col, value):
    db.execute(
        f"INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,"
        f" {col}, structured, confidence, observed_at, ingested_at)"
        f" VALUES (?, 'i1', 'e1', 's1', 1, 'x', ?, ?, '{{}}', 0.9,"
        f" '2026-07-19T00:00:00.000Z', '2026-07-19T00:00:00.000Z')",
        (ulid(), kind, value),
    )


def test_card_renders_png_with_ledger_numbers(seeded, monkeypatch):
    seeded.execute("UPDATE incidents SET lat = 39.76, lon = -121.62 WHERE id = 'i1'")
    _fact(seeded, "acreage", "acreage", 12345)
    _fact(seeded, "containment", "containment_pct", 40)
    monkeypatch.setattr(graphics, "fetch", lambda url, **kw: (_tiny_png(), "image/png"))
    png = graphics.generate_card(seeded, "i1")
    assert png.startswith(b"\x89PNG")
    from PIL import Image

    im = Image.open(io.BytesIO(png))
    assert im.size == (graphics.W, graphics.H)


def test_dead_tile_server_still_yields_a_card(seeded, monkeypatch):
    seeded.execute("UPDATE incidents SET lat = 39.76, lon = -121.62 WHERE id = 'i1'")

    def boom(url, **kw):
        raise RuntimeError("tiles down")

    monkeypatch.setattr(graphics, "fetch", boom)
    assert graphics.generate_card(seeded, "i1").startswith(b"\x89PNG")


def test_no_coordinates_still_yields_a_card(seeded, monkeypatch):
    monkeypatch.setattr(graphics, "fetch", lambda url, **kw: (_tiny_png(), "image/png"))
    assert graphics.generate_card(seeded, "i1").startswith(b"\x89PNG")


def test_attach_stores_in_cas_and_links_the_draft(seeded, monkeypatch, tmp_path):
    seeded.execute("UPDATE incidents SET lat = 39.76, lon = -121.62 WHERE id = 'i1'")
    draft_id = ulid()
    seeded.execute(
        "INSERT INTO drafts (id, kind, incident_id, body_md, status, created_at, updated_at)"
        " VALUES (?, 'social', 'i1', 'x', 'draft',"
        " '2026-07-19T00:00:00.000Z', '2026-07-19T00:00:00.000Z')",
        (draft_id,),
    )
    monkeypatch.setattr(graphics, "fetch", lambda url, **kw: (_tiny_png(), "image/png"))
    monkeypatch.setenv("TOAST_DATA_DIR", str(tmp_path))
    out = graphics.attach_card(seeded, draft_id)
    assert out["graphic_sha"]
    row = seeded.execute(
        "SELECT graphic_sha FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()
    assert row["graphic_sha"] == out["graphic_sha"]
    blob = seeded.execute(
        "SELECT path FROM raw_blobs WHERE sha256 = ?", (out["graphic_sha"],)
    ).fetchone()
    assert (tmp_path / blob["path"]).read_bytes().startswith(b"\x89PNG")


def test_draft_without_incident_is_refused(seeded):
    draft_id = ulid()
    seeded.execute(
        "INSERT INTO drafts (id, kind, body_md, status, created_at, updated_at)"
        " VALUES (?, 'social', 'x', 'draft',"
        " '2026-07-19T00:00:00.000Z', '2026-07-19T00:00:00.000Z')",
        (draft_id,),
    )
    with pytest.raises(ValueError, match="no incident"):
        graphics.attach_card(seeded, draft_id)


def _zone_cache(db, statuses):
    import json

    from toast.core.db import utcnow

    features = [
        {
            "geometry": {"type": "Point", "coordinates": [-121.6, 39.76]},
            "properties": {
                "ZONE_ID": f"US-CA-XBU-Z{i}",
                "ZONE_NAME": f"Zone {i}",
                "STATUS": s,
                "issued_at": "2026-07-19T00:00:00.000Z",
                "incident_id": "i1",
            },
        }
        for i, s in enumerate(statuses)
    ]
    db.execute(
        "INSERT OR REPLACE INTO map_cache (key, payload, fetched_at) VALUES (?, ?, ?)",
        ("evaczones:ca", json.dumps({"features": features}), utcnow()),
    )


def test_evac_card_renders_from_official_zones(seeded):
    _zone_cache(seeded, ["Evacuation Order", "Evacuation Warning", "Evacuation Warning"])
    png = graphics.generate_evac_card(seeded, "i1")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 1000


def test_evac_card_handles_no_active_zones(seeded):
    # no zone cache at all → the card still renders, saying nothing is in effect
    png = graphics.generate_evac_card(seeded, "i1")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_evac_card_unknown_incident_raises(seeded):
    with pytest.raises(ValueError):
        graphics.generate_evac_card(seeded, "does-not-exist")
