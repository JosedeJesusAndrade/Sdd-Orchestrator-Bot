"""Session switching: skip the currently loaded one and cache per-session history.

Tapping a session in the drawer has two jobs: make it active and (re)render its
history. Tapping the session the chat is ALREADY showing must do neither — it
only closes the drawer, so the user gets back to the chat with zero reload
(BUG #2).

The skip compares against the LOADED session (``current_real_id``), never the
sidebar's ``active_real_id``: at startup the newest session is active while the
chat still shows the welcome, so tapping it must LOAD it. Only once its messages
are rendered does a repeat tap skip.

The first visit to a session reads the agent history once; a per-``real_id``
cache serves every later visit with zero I/O until the cache is invalidated (a
new prompt or a new chat, see :meth:`SessionSwitcher.invalidate`).

This controller is flet-free and headless: it drives two ports and holds only the
cache plus the currently loaded id.
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
    """Skip the currently loaded session and cache each session's history (BUG #2)."""

    def __init__(self, sidebar: SidebarPort, load_history: HistoryLoader) -> None:
        self._sidebar = sidebar
        self._load_history = load_history
        self._cache: dict[str, tuple[Message, ...]] = {}
        #: The ``real_id`` the chat is currently showing (``None`` until a load).
        self._current_real_id: str | None = None

    @property
    def current_real_id(self) -> str | None:
        """The session the chat is currently showing (``None`` before any load)."""
        return self._current_real_id

    async def switch(self, real_id: str) -> SwitchResult | None:
        """Activate ``real_id`` and return its history.

        Returns ``None`` ONLY when ``real_id`` is the session the chat is
        already showing (``current_real_id``): the caller then just closes the
        drawer, touching no service (0 reload, BUG #2). The ``active_real_id``
        is deliberately NOT used — at startup the newest session is active yet
        the chat still shows the welcome, so tapping it must LOAD it.
        """
        if real_id == self._current_real_id:
            return None

        snapshot = await self._sidebar.select(real_id)
        cached = self._cache.get(real_id)
        if cached is None:
            cached = tuple(await self._load_history(real_id))
            self._cache[real_id] = cached
        # These messages are now what the chat renders: a repeat tap skips.
        self._current_real_id = real_id
        return SwitchResult(snapshot=snapshot, messages=cached)

    def reset_current(self) -> None:
        """Forget the loaded session so the next tap loads (new chat/reset)."""
        self._current_real_id = None

    def cached(self, real_id: str) -> tuple[Message, ...] | None:
        """Return the cached history for ``real_id`` (``None`` when not cached)."""
        return self._cache.get(real_id)

    def invalidate(self, real_id: str | None = None) -> None:
        """Drop one session's cache entry, or the whole cache when ``None``."""
        if real_id is None:
            self._cache.clear()
        else:
            self._cache.pop(real_id, None)
