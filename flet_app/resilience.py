"""Resilience helpers for the Flet frontend (UI-copy decisions only).

Principle: the app must NEVER be stuck, partial or busy-misreported after a
disconnect, restart or crash. The state is derived from the SERVER, never from
the lost in-memory UI state; the user must always see a clear outcome instead of
a frozen screen.

The heavy lifting (reconcile, abort, history sync) lives in ``PromptService`` so
it stays frontend-agnostic; this module only turns the reconciled state into the
copy the Flet UI shows.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from services.prompt_service import ReconcileResult

#: Shown when a prompt cannot be started because the server is still busy.
BUSY_NOTICE = (
    "⏳ El agente sigue trabajando en esta sesión. Espera un momento o pulsa "
    "detener para cancelar el turno."
)

#: Grace window for a dropped connection. A transient network blip (phone loses
#: Wi-Fi for a few seconds) reconnects well within this window; a real disconnect
#: does not, and only then is the abandoned turn aborted.
DISCONNECT_GRACE_S = 45.0


class DisconnectGuard:
    """Decide WHEN a dropped connection should abort the in-flight turn (F5).

    A Flet ``on_disconnect`` fires on ANY lost websocket, including a transient
    network blip. Aborting there would kill a legitimately running turn — bad UX.
    So a disconnect only ARMS a grace timer: if the connection comes back
    (``disarm`` from ``on_connect``) the timer is cancelled and the turn keeps
    running; if the grace elapses the turn really is orphaned and gets aborted.
    A ``close`` (the user closed the app) aborts immediately.
    """

    def __init__(
        self,
        abort: Callable[[], Awaitable[bool]],
        *,
        grace_s: float = DISCONNECT_GRACE_S,
    ) -> None:
        self._abort = abort
        self._grace_s = grace_s
        self._timer: asyncio.Task[None] | None = None

    @property
    def armed(self) -> bool:
        """True while a disconnect grace timer is still pending."""
        return self._timer is not None and not self._timer.done()

    def disarm(self) -> None:
        """Cancel a pending abort; the reconnect proved it was only a blip."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    async def on_disconnect(self) -> None:
        """Arm the grace timer; abort only if no reconnect arrives in time."""
        self.disarm()
        self._timer = asyncio.create_task(self._abort_after_grace())

    async def on_close(self) -> bool:
        """Abort immediately: closing the app is not a blip."""
        self.disarm()
        return await self._abort()

    async def _abort_after_grace(self) -> None:
        await asyncio.sleep(self._grace_s)
        self._timer = None
        await self._abort()


def busy_notice(result: ReconcileResult) -> str | None:
    """Return the user-facing notice when sending must be blocked, else None.

    A stale turn was already aborted by ``reconcile`` (so ``busy`` is False and
    the send proceeds); only a genuinely busy session (including one whose stale
    turn could NOT be aborted) yields a notice.
    """
    return BUSY_NOTICE if result.busy else None
