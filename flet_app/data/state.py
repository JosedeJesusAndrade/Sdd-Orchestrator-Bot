"""State primitives shared by every Flet data-layer controller.

``LoadState`` is the single asynchronous state machine (FD-02): a controller
starts ``idle`` and every load transitions ``loading`` -> ``ready | empty |
error``. ``Loadable`` is the mixin that encodes that cycle once, so a controller
only supplies its ``_fetch()`` (the domain). ``SidebarFailure`` is the typed,
non-localized failure carried by an error state; the UI maps ``kind`` to copy,
so no user-facing string is produced here.

This module imports nothing from ``flet``/``telegram``/``frontends`` and has no
dependency on the core services (it maps failures by exception class name to
stay a leaf).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")
P = TypeVar("P")

#: Stable failure kinds. These are data, not copy: ``flet-ui`` owns the strings.
FAILURE_CONVERSATION_HISTORY = "conversation_history"
FAILURE_INVALID_SESSION_ID = "invalid_session_id"
FAILURE_SESSION_NOT_FOUND = "session_not_found"
FAILURE_SESSION_EXISTS = "session_exists"
FAILURE_WORKDIR = "workdir"
FAILURE_UNEXPECTED = "unexpected"

#: Exception class name -> stable kind. Matching by name keeps this leaf module
#: free of imports from ``services`` (and therefore trivially headless).
_KIND_BY_EXCEPTION_NAME: dict[str, str] = {
    "ConversationHistoryError": FAILURE_CONVERSATION_HISTORY,
    "InvalidSessionIdError": FAILURE_INVALID_SESSION_ID,
    "SessionNotFoundError": FAILURE_SESSION_NOT_FOUND,
    "SessionExistsError": FAILURE_SESSION_EXISTS,
    "WorkdirError": FAILURE_WORKDIR,
}


@dataclass(frozen=True)
class SidebarFailure:
    """A typed failure: a stable ``kind`` plus optional diagnostic ``detail``.

    ``detail`` is never user-facing copy; it exists to aid logs/tests.
    """

    kind: str
    detail: str | None = None


def failure_from(exc: BaseException) -> SidebarFailure:
    """Translate an exception into a typed failure (first known base class wins)."""
    kind = FAILURE_UNEXPECTED
    for base in type(exc).__mro__:
        mapped = _KIND_BY_EXCEPTION_NAME.get(base.__name__)
        if mapped is not None:
            kind = mapped
            break
    detail = str(exc).strip() or None
    return SidebarFailure(kind=kind, detail=detail)


class LoadState(Generic[T]):
    """Marker base for the closed load-state union (idle/loading/ready/empty/error)."""


@dataclass(frozen=True)
class LoadIdle(LoadState[T]):
    """Nothing has been requested yet; the initial state of every controller."""


@dataclass(frozen=True)
class LoadLoading(LoadState[T]):
    """A load is in flight."""


@dataclass(frozen=True)
class LoadReady(LoadState[T]):
    """The load succeeded and produced a value."""

    value: T


@dataclass(frozen=True)
class LoadEmpty(LoadState[T]):
    """The load succeeded but produced no items (a valid, renderable state)."""


@dataclass(frozen=True)
class LoadError(LoadState[T]):
    """The load failed; ``failure`` is a typed failure, never localized copy."""

    failure: SidebarFailure


class Loadable(Generic[T]):
    """Mixin encoding the uniform load cycle for a data controller (FD-02).

    A subclass supplies ``_fetch()``, which returns ``(payload, settled_state)``
    where ``settled_state`` is its domain's ``ready``/``empty`` decision.
    ``_load`` owns the ``loading`` transition, the exception-to-``error`` mapping
    and the publication of the resulting state, so the only per-controller logic
    left is the fetch itself.

    Override ``_publish`` when the state is cached somewhere other than
    ``self._state`` (the sidebar keeps it inside its immutable snapshot).
    """

    def __init__(self) -> None:
        self._state: LoadState[T] = LoadIdle[T]()

    @property
    def state(self) -> LoadState[T]:
        """The controller's current load state."""
        return self._state

    async def _load(
        self,
        fetch: Callable[[], Awaitable[tuple[P, LoadState[T]]]],
    ) -> tuple[P | None, LoadState[T]]:
        """Run ``fetch`` as ``loading`` -> ``ready``/``empty`` or ``error``.

        Returns the payload (``None`` when the fetch raised) and the state that
        was published, so callers apply the payload only on success.
        """
        self._publish(LoadLoading[T]())
        try:
            payload, settled = await fetch()
        except Exception as exc:
            return None, self._fail(exc)
        self._publish(settled)
        return payload, settled

    def _fail(self, exc: BaseException) -> LoadState[T]:
        """Publish a typed ``error`` state (for a failed discrete mutation)."""
        failed = LoadError[T](failure=failure_from(exc))
        self._publish(failed)
        return failed

    def _publish(self, state: LoadState[T]) -> None:
        """Store a state; subclasses caching elsewhere override this sink."""
        self._state = state
