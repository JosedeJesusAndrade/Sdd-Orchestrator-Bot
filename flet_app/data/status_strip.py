"""Status strip data: on-demand ``StatusSnapshot`` with a ``LoadState`` (FD-06).

The strip never polls and never auto-refreshes: ``load()`` computes the current
snapshot exactly when the view asks for it and exposes the resulting
:class:`~flet_app.data.state.LoadState`. No copy is rendered here.
"""

from __future__ import annotations

from typing import Protocol

from flet_app.data.state import (
    LoadError,
    LoadIdle,
    LoadLoading,
    LoadReady,
    LoadState,
    failure_from,
)
from services.conversation import ConversationKey
from services.status_service import StatusSnapshot


class StatusPort(Protocol):
    """The subset of ``StatusService`` the strip reads."""

    async def snapshot(self, key: ConversationKey) -> StatusSnapshot: ...


class StatusStripController:
    """Compute the status strip's data on demand (no background work)."""

    def __init__(self, status: StatusPort, key: ConversationKey) -> None:
        self._status = status
        self._key = key
        self._state: LoadState[StatusSnapshot] = LoadIdle[StatusSnapshot]()

    @property
    def state(self) -> LoadState[StatusSnapshot]:
        """The last load state (starts ``idle``; no polling is scheduled)."""
        return self._state

    async def load(self) -> StatusSnapshot | None:
        """Return the current snapshot, or ``None`` when the load failed."""
        self._state = LoadLoading[StatusSnapshot]()
        try:
            snapshot = await self._status.snapshot(self._key)
        except Exception as exc:
            self._state = LoadError[StatusSnapshot](failure=failure_from(exc))
            return None
        self._state = LoadReady[StatusSnapshot](snapshot)
        return snapshot
