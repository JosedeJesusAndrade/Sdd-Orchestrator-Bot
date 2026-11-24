"""Sidebar data: the immutable snapshot and its controller (FD-03..FD-05).

``SidebarSnapshot`` is the sidebar's ENTIRE data contract — exactly eight
fields. The search pill, gap badge, permissions badge and avatar are
``flet-ui`` concerns and deliberately absent here.

``SidebarController`` drives the data with four async operations (``load``,
``select``, ``new_chat``, ``rename``) and one SYNCHRONOUS, I/O-free ``toggle``
that only flips the cached snapshot so a drawer opens in well under 100 ms.

Titles normally come from OpenCode history; ``_overlay_stored_titles`` swaps in
a bot-stored title when the user renamed, so a rename is actually VISIBLE.

No widget or ``flet`` import lives in this module.
"""

from __future__ import annotations

import getpass
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol

from flet_app.data.state import (
    Loadable,
    LoadEmpty,
    LoadError,
    LoadReady,
    LoadState,
    SidebarFailure,
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

    async def rename(
        self, key: ConversationKey, real_id: str, title: str
    ) -> SessionInfo | None: ...


class WorkdirPort(Protocol):
    """The subset of ``EffectiveWorkdir`` the sidebar reads."""

    async def resolve(self, key: ConversationKey) -> WorkdirResolution: ...


class SessionStatePort(Protocol):
    """The subset of ``SessionStore`` the sidebar reads."""

    async def get_active_session(self, key: ConversationKey) -> SessionInfo | None: ...

    async def get_model(self, key: ConversationKey) -> str: ...

    async def list_sessions(self, key: ConversationKey) -> list[SessionInfo]: ...


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


# ── Stored-title overlay (D2 — the rename must be VISIBLE) ─────────────


def _overlay_stored_titles(
    sessions: list[ConversationSummary],
    stored: list[SessionInfo],
) -> list[ConversationSummary]:
    """Swap OpenCode titles for the bot-stored ones when a user renamed.

    ``ConversationsService`` builds every ``ConversationSummary.title`` from
    OpenCode history, so a rename that only touches ``sessions.json`` would
    persist but never render. This pure UI-layer shim keeps a ``name -> title``
    overlay and applies it to the adopted rows. A stored title equal to the
    adopted name (a fresh ``chat-N`` whose title is its own name) is NOT
    overlaid, so brand-new sessions keep their OpenCode title; orphans (no
    ``adopted_name``) are untouched.
    """
    stored_titles = {
        session.name: session.title for session in stored if session.real_id and session.title
    }
    overlaid: list[ConversationSummary] = []
    for summary in sessions:
        adopted = summary.adopted_name
        stored_title = stored_titles.get(adopted) if adopted else None
        if stored_title and stored_title != adopted:
            overlaid.append(replace(summary, title=stored_title))
        else:
            overlaid.append(summary)
    return overlaid


# ── Controller ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _SidebarData:
    """The raw services' payload a successful load folds into a snapshot."""

    sessions: list[ConversationSummary]
    active: SessionInfo | None
    workdir: WorkdirResolution
    model: str


class SidebarController(Loadable[None]):
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
        super().__init__()
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
            state=self._state,
            error=None,
            profile_name=default_profile_name() if profile_name is None else profile_name,
        )

    @property
    def snapshot(self) -> SidebarSnapshot:
        """The cached snapshot (the sidebar's current data)."""
        return self._snapshot

    def _publish(self, state: LoadState[None]) -> None:
        """Cache the state inside the snapshot (the sidebar's single source)."""
        self._state = state
        error: SidebarFailure | None = state.failure if isinstance(state, LoadError) else None
        self._snapshot = replace(self._snapshot, state=state, error=error)

    async def _fetch(self) -> tuple[_SidebarData, LoadState[None]]:
        sessions = await self._conversations.list_conversations(self._key)
        stored = await self._session_state.list_sessions(self._key)
        sessions = _overlay_stored_titles(sessions, stored)
        active = await self._session_state.get_active_session(self._key)
        resolved = await self._workdir.resolve(self._key)
        model = await self._session_state.get_model(self._key)
        data = _SidebarData(sessions=sessions, active=active, workdir=resolved, model=model)
        state: LoadState[None] = LoadEmpty[None]() if not sessions else LoadReady[None](None)
        return data, state

    async def load(self) -> SidebarSnapshot:
        """Read conversations, active real id, workdir and model (FD-04)."""
        data, _ = await self._load(self._fetch)
        if data is not None:
            self._snapshot = replace(
                self._snapshot,
                sessions=tuple(data.sessions),
                active_real_id=data.active.real_id if data.active is not None else None,
                workdir=str(data.workdir.path),
                model=data.model,
            )
        return self._snapshot

    async def select(self, real_id: str) -> SidebarSnapshot:
        """Activate ``real_id`` (adopting it when needed) WITHOUT re-listing.

        Switching sessions used to call :meth:`load`, which re-queries the agent
        history (a ~2 s CLI boot). The active highlight and the (possibly new)
        adopted name are patched into the cached snapshot instead, so a switch
        costs no history read at all (FD-04).
        """
        title = next(
            (session.title for session in self._snapshot.sessions if session.real_id == real_id),
            real_id,
        )
        try:
            name = await self._conversations.open(self._key, real_id, title)
        except Exception as exc:
            self._fail(exc)
            return self._snapshot
        self._snapshot = replace(
            self._snapshot,
            active_real_id=real_id,
            sessions=tuple(
                replace(
                    session,
                    is_active=session.real_id == real_id,
                    adopted_name=(
                        name
                        if session.real_id == real_id and session.adopted_name is None
                        else session.adopted_name
                    ),
                )
                for session in self._snapshot.sessions
            ),
        )
        return self._snapshot

    async def new_chat(self) -> SidebarSnapshot:
        """Create/activate the next ``chat-N`` session and reload (FD-04)."""
        try:
            await self._session_writer.new_chat(self._key)
        except Exception as exc:
            self._fail(exc)
            return self._snapshot
        return await self.load()

    async def rename(self, real_id: str, title: str) -> bool:
        """Persist a new title for a bot-bound session, then reload (EM-03).

        The service resolves the bot name from ``real_id`` and returns ``None``
        for an unbound/blank rename (a silent no-op, no error state). Any
        exception publishes a typed failure. On success the reload re-applies
        the stored-title overlay so the new title becomes visible.
        """
        try:
            result = await self._session_writer.rename(self._key, real_id, title)
        except Exception as exc:
            self._fail(exc)
            return False
        if result is None:
            return False
        await self.load()
        return True

    def toggle(self) -> SidebarSnapshot:
        """Flip ``expanded`` on the cached snapshot: sync, zero I/O (FD-05).

        Not a coroutine, never touches a service, never awaits.
        """
        self._snapshot = replace(self._snapshot, expanded=not self._snapshot.expanded)
        return self._snapshot
