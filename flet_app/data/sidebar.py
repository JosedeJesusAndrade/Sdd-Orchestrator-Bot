"""Sidebar data: the immutable snapshot and its controller (FD-03..FD-05).

``SidebarSnapshot`` is the sidebar's ENTIRE data contract — exactly eight
fields. The search pill, gap badge, permissions badge and avatar are
``flet-ui`` concerns and deliberately absent here.

``SidebarController`` drives the data with three async operations (``load``,
``select``, ``new_chat``) and one SYNCHRONOUS, I/O-free ``toggle`` that only
flips the cached snapshot so a drawer opens in well under 100 ms.

No widget or ``flet`` import lives in this module.
"""

from __future__ import annotations

import getpass
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol

from flet_app.data.state import (
    LoadEmpty,
    LoadError,
    LoadIdle,
    LoadLoading,
    LoadReady,
    LoadState,
    SidebarFailure,
    failure_from,
)
from services.conversation import ConversationKey

if TYPE_CHECKING:
    from services.agent_sessions import ConversationSummary
    from services.effective_workdir import WorkdirResolution
    from services.session_store import SessionInfo

#: Fallback display name when the OS user cannot be read.
DEFAULT_PROFILE_NAME = "Local"


def default_profile_name() -> str:
    """Best-effort local profile name for the sidebar footer (data, not copy)."""
    try:
        return getpass.getuser() or DEFAULT_PROFILE_NAME
    except Exception:
        return DEFAULT_PROFILE_NAME


# ── Structural ports (satisfied by the core services) ──────────────────


class ConversationsPort(Protocol):
    """The subset of ``ConversationsService`` the sidebar reads/uses."""

    async def list_conversations(self, key: ConversationKey) -> list[ConversationSummary]: ...

    async def open(self, key: ConversationKey, real_id: str, title: str) -> str: ...


class SessionWriterPort(Protocol):
    """The subset of ``SessionService`` the sidebar mutates through."""

    async def new_chat(self, key: ConversationKey) -> SessionInfo: ...


class WorkdirPort(Protocol):
    """The subset of ``EffectiveWorkdir`` the sidebar reads."""

    async def resolve(self, key: ConversationKey) -> WorkdirResolution: ...


class SessionStatePort(Protocol):
    """The subset of ``SessionStore`` the sidebar reads."""

    async def get_active_session(self, key: ConversationKey) -> SessionInfo | None: ...

    async def get_model(self, key: ConversationKey) -> str: ...


# ── Snapshot (exactly eight fields — F-12 / FD-03) ─────────────────────


@dataclass(frozen=True)
class SidebarSnapshot:
    """Everything the sidebar renders, and nothing UI-specific."""

    expanded: bool
    sessions: tuple[ConversationSummary, ...]
    active_real_id: str | None
    workdir: str
    model: str
    state: LoadState[None]
    error: SidebarFailure | None
    profile_name: str


# ── Controller ─────────────────────────────────────────────────────────


class SidebarController:
    """Drive the sidebar's data; never renders and never imports widgets."""

    def __init__(
        self,
        conversations: ConversationsPort,
        session_writer: SessionWriterPort,
        workdir: WorkdirPort,
        session_state: SessionStatePort,
        key: ConversationKey,
        *,
        expanded: bool = True,
        profile_name: str | None = None,
    ) -> None:
        self._conversations = conversations
        self._session_writer = session_writer
        self._workdir = workdir
        self._session_state = session_state
        self._key = key
        self._snapshot = SidebarSnapshot(
            expanded=expanded,
            sessions=(),
            active_real_id=None,
            workdir="",
            model="",
            state=LoadIdle[None](),
            error=None,
            profile_name=default_profile_name() if profile_name is None else profile_name,
        )

    @property
    def snapshot(self) -> SidebarSnapshot:
        """The cached snapshot (the sidebar's current data)."""
        return self._snapshot

    async def load(self) -> SidebarSnapshot:
        """Read conversations, active real id, workdir and model (FD-04)."""
        self._snapshot = replace(self._snapshot, state=LoadLoading[None](), error=None)
        try:
            sessions = await self._conversations.list_conversations(self._key)
            active = await self._session_state.get_active_session(self._key)
            resolved = await self._workdir.resolve(self._key)
            model = await self._session_state.get_model(self._key)
        except Exception as exc:
            return self._fail(exc)

        state: LoadState[None] = LoadEmpty[None]() if not sessions else LoadReady[None](None)
        self._snapshot = SidebarSnapshot(
            expanded=self._snapshot.expanded,
            sessions=tuple(sessions),
            active_real_id=active.real_id if active is not None else None,
            workdir=str(resolved.path),
            model=model,
            state=state,
            error=None,
            profile_name=self._snapshot.profile_name,
        )
        return self._snapshot

    async def select(self, real_id: str) -> SidebarSnapshot:
        """Activate ``real_id`` (adopting it when needed) and reload (FD-04)."""
        title = next(
            (session.title for session in self._snapshot.sessions if session.real_id == real_id),
            real_id,
        )
        try:
            await self._conversations.open(self._key, real_id, title)
        except Exception as exc:
            return self._fail(exc)
        return await self.load()

    async def new_chat(self) -> SidebarSnapshot:
        """Create/activate the next ``chat-N`` session and reload (FD-04)."""
        try:
            await self._session_writer.new_chat(self._key)
        except Exception as exc:
            return self._fail(exc)
        return await self.load()

    def toggle(self) -> SidebarSnapshot:
        """Flip ``expanded`` on the cached snapshot: sync, zero I/O (FD-05).

        Not a coroutine, never touches a service, never awaits.
        """
        self._snapshot = replace(self._snapshot, expanded=not self._snapshot.expanded)
        return self._snapshot

    def _fail(self, exc: BaseException) -> SidebarSnapshot:
        failure = failure_from(exc)
        self._snapshot = replace(
            self._snapshot,
            state=LoadError[None](failure=failure),
            error=failure,
        )
        return self._snapshot
