"""Source adapters.

An adapter turns one external source into Envelopes. Registered by name in
ADAPTERS; a source row's `adapter` column selects one. New sources = new
module + registry entry, zero core changes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from toast.core.envelope import Envelope


class SourceAdapter(Protocol):
    def poll(self, config: dict[str, Any], cursor: str | None) -> tuple[list[Envelope], str | None]:
        """Fetch once. Returns (envelopes, next_cursor)."""
        ...


_REGISTRY: dict[str, Callable[[], SourceAdapter]] = {}


def register(name: str) -> Callable:
    def deco(factory: Callable[[], SourceAdapter]) -> Callable[[], SourceAdapter]:
        _REGISTRY[name] = factory
        return factory

    return deco


def get_adapter(name: str) -> SourceAdapter | None:
    factory = _REGISTRY.get(name)
    return factory() if factory else None


def load_builtin_adapters() -> None:
    """Import bundled adapter modules so their @register decorators run."""
    from toast.sources import firms, nws_cap, wfigs  # noqa: F401
