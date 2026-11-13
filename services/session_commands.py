"""Typed ``/session`` sub-commands and their dispatch outcomes (PTB-free).

Extracted from ``handlers/sessions.py`` (core-isolation item 9). The parser turns
the raw ``context.args`` into a typed :class:`SessionCommand`; the frontend then
calls ``SessionService.dispatch`` and maps the returned outcome to UI. UI copy
(usage strings, error text) and relative-time formatting stay in the view.

Design guarantees:
  - **PTB-free**: this module never imports a frontend.
  - **Typed**: every sub-command is a ``SessionCommandKind`` member, and every
    outcome is a frozen dataclass so the frontend never re-implements the chain.
  - **Case-insensitive**: the sub-command is lowercased before matching, exactly
    like the legacy ``if/elif`` router.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.session_service import (
        DiscoveredSession,
        SessionDetails,
        SessionListSnapshot,
    )
    from services.session_store import SessionInfo


class SessionCommandKind(StrEnum):
    """Every sub-command understood by ``/session``."""

    NEW = "new"
    LIST = "list"
    SWITCH = "switch"
    DELETE = "delete"
    INFO = "info"
    DISCOVER = "discover"
    ADOPT = "adopt"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SessionCommand:
    """A parsed, ready-to-dispatch ``/session`` sub-command."""

    kind: SessionCommandKind
    name: str | None = None
    real_id: str | None = None
    raw: str | None = None


@dataclass(frozen=True)
class SessionUsage:
    """The command is malformed or missing arguments.

    ``kind`` is ``None`` for the bare ``/session`` usage and set when a known
    sub-command lacks its argument. ``raw`` carries the unknown sub-command so
    the view can echo it back.
    """

    kind: SessionCommandKind | None = None
    raw: str | None = None


@dataclass(frozen=True)
class SessionCreated:
    session: SessionInfo


@dataclass(frozen=True)
class SessionListed:
    snapshot: SessionListSnapshot


@dataclass(frozen=True)
class SessionSwitched:
    session: SessionInfo


@dataclass(frozen=True)
class SessionDeleted:
    name: str
    real_id: str | None


@dataclass(frozen=True)
class SessionInfoResult:
    details: SessionDetails


@dataclass(frozen=True)
class SessionDiscovered:
    sessions: list[DiscoveredSession]


@dataclass(frozen=True)
class SessionAdopted:
    name: str
    real_id: str


SessionOutcome = (
    SessionCreated
    | SessionListed
    | SessionSwitched
    | SessionDeleted
    | SessionInfoResult
    | SessionDiscovered
    | SessionAdopted
    | SessionUsage
)


def parse_session_command(args: Sequence[str]) -> SessionCommand | SessionUsage:
    """Parse ``/session`` arguments into a typed command.

    Returns :class:`SessionUsage` for the empty invocation and for an unknown
    sub-command; argument validation (missing names) is left to
    ``SessionService.dispatch`` so the view can pick the matching usage string.
    """
    if not args:
        return SessionUsage()

    subcommand = args[0].lower()
    name = args[1] if len(args) > 1 else None

    if subcommand == SessionCommandKind.NEW:
        return SessionCommand(SessionCommandKind.NEW, name=name)
    if subcommand == SessionCommandKind.LIST:
        return SessionCommand(SessionCommandKind.LIST)
    if subcommand == SessionCommandKind.SWITCH:
        return SessionCommand(SessionCommandKind.SWITCH, name=name)
    if subcommand == SessionCommandKind.DELETE:
        return SessionCommand(SessionCommandKind.DELETE, name=name)
    if subcommand == SessionCommandKind.INFO:
        return SessionCommand(SessionCommandKind.INFO, name=name)
    if subcommand == SessionCommandKind.DISCOVER:
        return SessionCommand(SessionCommandKind.DISCOVER)
    if subcommand == SessionCommandKind.ADOPT:
        return SessionCommand(
            SessionCommandKind.ADOPT,
            name=args[2] if len(args) > 2 else None,
            real_id=name,
        )
    return SessionUsage(kind=SessionCommandKind.UNKNOWN, raw=subcommand)
