"""ALERTCalifornia camera metadata for the map.

The boundary under test is architectural: this module serves camera POSITIONS
and never touches imagery — the frame template rides along for the browser to
use, and the backend has no code path that fetches a frame.
"""

import json

from toast.pipeline import cameras


def _fake_index(monkeypatch, features):
    calls = []

    def fake_fetch(url, **kw):
        calls.append(url)
        return json.dumps({"features": features}).encode(), "application/json"

    monkeypatch.setattr(cameras, "fetch", fake_fetch)
    return calls


def _cam(cid, lat, lon, **props):
    return {
        "geometry": {"coordinates": [lon, lat, 1500]},
        "properties": {"id": cid, "name": cid, "az_current": 90.0, "fov": 65.0, **props},
    }


def test_near_returns_only_cameras_in_range_sorted_by_distance(seeded, monkeypatch):
    _fake_index(
        monkeypatch,
        [
            _cam("Axis-Far", 45.0, -121.0),  # ~450 km away — cannot see the fire
            _cam("Axis-Ridge", 39.94, -121.24),  # ~35 km
            _cam("Axis-Town", 39.77, -121.60),  # ~2 km
        ],
    )
    out = cameras.cameras_near(seeded, 39.76, -121.62)
    ids = [c["id"] for c in out["cameras"]]
    assert ids == ["Axis-Town", "Axis-Ridge"]  # nearest first, far one dropped
    assert out["cameras"][0]["distance_km"] < out["cameras"][1]["distance_km"]
    # the browser needs these two to show a frame with credit; we send both
    assert "{id}" in out["frame_url_template"]
    assert "ALERTCalifornia" in out["attribution"]


def test_index_is_cached_so_their_server_is_not_hammered(seeded, monkeypatch):
    calls = _fake_index(monkeypatch, [_cam("Axis-A", 39.77, -121.60)])
    cameras.cameras_near(seeded, 39.76, -121.62)
    cameras.cameras_near(seeded, 38.0, -120.0)  # different point, same index
    assert len(calls) == 1  # one metadata fetch serves every query for an hour


def test_backend_has_no_frame_fetch_path():
    """The TOS posture, asserted: nothing in the module downloads imagery."""
    import inspect

    src = inspect.getsource(cameras)
    assert "latest-frame" not in src.replace(cameras.FRAME_URL_TEMPLATE, "")
