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

from services.prompt_service import ReconcileResult

#: Shown when a prompt cannot be started because the server is still busy.
BUSY_NOTICE = (
    "⏳ El agente sigue trabajando en esta sesión. Espera un momento o pulsa "
    "detener para cancelar el turno."
)


def busy_notice(result: ReconcileResult) -> str | None:
    """Return the user-facing notice when sending must be blocked, else None.

    A stale turn was already aborted by ``reconcile`` (so ``busy`` is False and
    the send proceeds); only a genuinely busy session yields a notice.
    """
    return BUSY_NOTICE if result.busy else None
