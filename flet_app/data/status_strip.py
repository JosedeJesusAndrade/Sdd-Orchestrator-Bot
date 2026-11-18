"""Status strip data: on-demand ``StatusSnapshot`` with a ``LoadState`` (FD-06).

The strip never polls and never auto-refreshes: ``load()`` computes the current
snapshot exactly when the view asks for it and exposes the resulting
:class:`~flet_app.data.state.LoadState`. No copy is rendered here.
"""

from __future__ import annotations

from typing import Protocol

from flet_app.data.state import Loadable, LoadReady, LoadState
from services.conversation import ConversationKey
from services.status_service import StatusSnapshot


class StatusPort(Protocol):
    """The subset of ``StatusService`` the strip reads."""

    async def snapshot(self, key: ConversationKey) -> StatusSnapshot: ...


class StatusStripController(Loadable[StatusSnapshot]):
    """Compute the status strip's data on demand (no background work)."""

    def __init__(self, status: StatusPort, key: ConversationKey) -> None:
        super().__init__()
        self._status = status
        self._key = key

    async def _fetch(self) -> tuple[StatusSnapshot, LoadState[StatusSnapshot]]:
        snapshot = await self._status.snapshot(self._key)
        return snapshot, LoadReady[StatusSnapshot](snapshot)

    async def load(self) -> StatusSnapshot | None:
        """Return the current snapshot, or ``None`` when the load failed."""
        snapshot, _ = await self._load(self._fetch)
        return snapshot
