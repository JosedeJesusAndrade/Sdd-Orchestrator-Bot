"""State primitives shared by every Flet data-layer controller.

``LoadState`` is the single asynchronous state machine (FD-02): a controller
starts ``idle`` and every load transitions ``loading`` -> ``ready | empty |
error``. ``SidebarFailure`` is the typed, non-localized failure carried by an
error state; the UI maps ``kind`` to copy, so no user-facing string is produced
here.

This module imports nothing from ``flet``/``telegram``/``frontends`` and has no
dependency on the core services (it maps failures by exception class name to
stay a leaf).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")

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

    failure: object
