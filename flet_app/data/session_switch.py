"""Session switching: skip the active one and cache per-session history.

Tapping a session in the drawer has two jobs: make it active and (re)render its
history. Tapping the ALREADY active session must do neither — it only closes the
drawer, so the user gets back to the chat with zero reload (BUG #2).

The first visit to a session reads the agent history once; a per-``real_id``
cache serves every later visit with zero I/O until the cache is invalidated (a
new prompt or a new chat, see :meth:`SessionSwitcher.invalidate`).

This controller is flet-free and headless: it drives two ports and holds nothing
but the cache.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from flet_app.data.sidebar import SidebarSnapshot
from services.agent_sessions import Message

HistoryLoader = Callable[[str], Awaitable[list[Message]]]


class SidebarPort(Protocol):
    """The subset of ``SidebarController`` the switcher drives."""

    @property
    def snapshot(self) -> SidebarSnapshot: ...

    async def select(self, real_id: str) -> SidebarSnapshot: ...


@dataclass(frozen=True)
class SwitchResult:
    """Outcome of a switch: the reloaded snapshot and the history to render."""

    snapshot: SidebarSnapshot
    messages: tuple[Message, ...]


class SessionSwitcher:
    """Skip the active session and cache each session's history (BUG #2)."""

    def __init__(self, sidebar: SidebarPort, load_history: HistoryLoader) -> None:
        self._sidebar = sidebar
        self._load_history = load_history
        self._cache: dict[str, tuple[Message, ...]] = {}

    async def switch(self, real_id: str) -> SwitchResult | None:
        """Activate ``real_id`` and return its history.

        Returns ``None`` when ``real_id`` is already the active session: the
        caller must only close the drawer, touching no service (0 reload).
        """
        if real_id == self._sidebar.snapshot.active_real_id:
            return None

        snapshot = await self._sidebar.select(real_id)
        cached = self._cache.get(real_id)
        if cached is None:
            cached = tuple(await self._load_history(real_id))
            self._cache[real_id] = cached
        return SwitchResult(snapshot=snapshot, messages=cached)

    def cached(self, real_id: str) -> tuple[Message, ...] | None:
        """Return the cached history for ``real_id`` (``None`` when not cached)."""
        return self._cache.get(real_id)

    def invalidate(self, real_id: str | None = None) -> None:
        """Drop one session's cache entry, or the whole cache when ``None``."""
        if real_id is None:
            self._cache.clear()
        else:
            self._cache.pop(real_id, None)
