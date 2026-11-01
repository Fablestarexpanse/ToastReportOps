"""The CAL FIRE adapter identifies itself honestly, like every other fetch."""

from __future__ import annotations

from typing import Any

from toast.sources.calfire import fetch_incidents


class FakeCapture:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def fetch_captured(self, url: str, **kwargs: Any) -> tuple[bytes, str]:
        self.kwargs = kwargs
        return b"[]", "application/json"


def test_requests_do_not_pose_as_a_browser():
    capture = FakeCapture()
    fetch_incidents(capture)  # type: ignore[arg-type]
    assert "User-Agent" not in (capture.kwargs.get("headers") or {})
