"""StatusService: raw /status data, frontend-agnostic (Fase B).

Moves the ``/status`` computation out of ``handlers/commands.py`` so a second
frontend (Flet) can reuse the SAME data without duplicating logic. This module
is PTB-free and returns a :class:`StatusSnapshot` with raw values (strings,
ints, datetimes); formatting stays in the handler.

Inventory (design `sdd/flet-preparation/design` #426):
  - B1: git-branch lookup (workdir -> ``git rev-parse``).
  - B2: active session / model / timestamps / prompt count / uptime assembly.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from services.session_store import SessionStore
from utils.logging import get_module_logger, log_exception

if TYPE_CHECKING:
    from services.agent_sessions import AgentSessions
    from services.conversation import ConversationKey
    from services.effective_workdir import EffectiveWorkdir

logger = get_module_logger(__name__)

#: Hard cap on the git lookup (ST-01). A 5 s ``subprocess.run`` used to freeze
#: Flet's single event loop; 2 s is the accepted budget.
GIT_TIMEOUT_SECONDS = 2.0

#: Fallback provider when the chat has no ``provider`` setting (ST-02).
DEFAULT_PROVIDER = "opencode"


@dataclass(frozen=True)
class StatusSnapshot:
    """Raw status data. ``session_name is None`` means "no active session".

    ``created``/``last_used`` are only set when the stored ISO string parses to
    a timezone-aware datetime. ``*_fallback`` carries the raw string when it
    does not, so the Telegram view can keep its historical fallback rendering.
    """

    session_name: str | None
    session_real_id: str | None
    model: str
    branch: str
    workdir: str
    provider: str
    session_count: int
    created: datetime | None
    last_used: datetime | None
    prompt_count: int
    uptime_seconds: int | None
    created_fallback: str | None = None
    last_used_fallback: str | None = None


class StatusService:
    """Compute the data behind /status without any frontend dependency."""

    def __init__(
        self,
        session_store: SessionStore,
        start_time: float | None = None,
        *,
        effective_workdir: EffectiveWorkdir | None = None,
        agent_sessions: AgentSessions | None = None,
    ) -> None:
        self._store = session_store
        self._start_time = start_time
        # Injected by ``build_prompt_stack``; production always passes it so the
        # git branch is computed in the SAME workdir prompts run in (WD-02).
        self._effective_workdir = effective_workdir
        # Exact, uncapped session count comes from a dedicated protocol method
        # (``list_sessions`` pages at 200; flag #5).
        self._agent_sessions = agent_sessions

    async def snapshot(self, conversation_id: ConversationKey) -> StatusSnapshot:
        """Return a raw status snapshot for a conversation (on demand only)."""
        session = await self._store.get_active_session(conversation_id)
        model = await self._store.get_model(conversation_id)
        settings = await self._store.get_all_chat_settings(conversation_id)
        provider = self._provider(settings)
        if self._effective_workdir is not None:
            resolved = await self._effective_workdir.resolve(conversation_id)
            workdir = str(resolved.path)
        else:
            workdir = settings.get("workdir", "")
        # The git lookup and the count are independent: run them concurrently so
        # a slow git can never delay the count (and vice versa).
        branch, session_count = await asyncio.gather(
            self._git_branch(workdir),
            self._count_sessions(workdir),
        )
        uptime_seconds = self._uptime_seconds()

        if session is None:
            return StatusSnapshot(
                session_name=None,
                session_real_id=None,
                model=model,
                branch=branch,
                workdir=workdir,
                provider=provider,
                session_count=session_count,
                created=None,
                last_used=None,
                prompt_count=0,
                uptime_seconds=uptime_seconds,
            )

        created = self._parse_timestamp(session.created)
        last_used = self._parse_timestamp(session.last_used)

        return StatusSnapshot(
            session_name=session.name,
            session_real_id=session.real_id,
            model=model,
            branch=branch,
            workdir=workdir,
            provider=provider,
            session_count=session_count,
            created=created,
            last_used=last_used,
            prompt_count=session.prompt_count,
            uptime_seconds=uptime_seconds,
            created_fallback=(session.created if session.created and created is None else None),
            last_used_fallback=(
                session.last_used if session.last_used and last_used is None else None
            ),
        )

    @staticmethod
    def _provider(settings: dict[str, Any]) -> str:
        """Return the configured provider, defaulting to ``"opencode"``."""
        value = settings.get("provider")
        return value if isinstance(value, str) and value else DEFAULT_PROVIDER

    async def _count_sessions(self, workdir: str) -> int:
        """Exact count of OpenCode sessions in ``workdir`` (failure -> 0)."""
        if not workdir or self._agent_sessions is None:
            return 0
        try:
            return await self._agent_sessions.count_sessions(workdir)
        except Exception:
            log_exception(
                "status_session_count_failed",
                module=__name__,
                level=logging.WARNING,
                workdir=workdir,
            )
            return 0

    def _uptime_seconds(self) -> int | None:
        if self._start_time is None:
            return None
        return int(time.time() - self._start_time)

    async def _git_branch(self, workdir: str) -> str:
        """Return the current git branch of ``workdir``, or ``"?"``.

        Uses ``create_subprocess_exec`` (never blocking) with a
        :data:`GIT_TIMEOUT_SECONDS` budget (ST-01). Any failure or timeout
        degrades to ``"?"`` and emits an event; it never raises.
        """
        if not workdir:
            return "?"
        try:
            process = await asyncio.create_subprocess_exec(
                "git",
                "rev-parse",
                "--abbrev-ref",
                "HEAD",
                cwd=workdir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception:
            log_exception(
                "git_branch_lookup_failed",
                module=__name__,
                level=logging.DEBUG,
                workdir=workdir,
            )
            return "?"

        try:
            stdout, _stderr = await asyncio.wait_for(
                process.communicate(), timeout=GIT_TIMEOUT_SECONDS
            )
        except TimeoutError:
            await self._discard_process(process)
            logger.warning(
                "git branch lookup timed out",
                extra={"event": "git_branch_timeout", "workdir": workdir},
            )
            return "?"
        except Exception:
            await self._discard_process(process)
            log_exception(
                "git_branch_lookup_failed",
                module=__name__,
                level=logging.DEBUG,
                workdir=workdir,
            )
            return "?"

        if process.returncode != 0:
            logger.debug(
                "git branch lookup failed",
                extra={
                    "event": "git_branch_lookup_failed",
                    "workdir": workdir,
                    "returncode": process.returncode,
                },
            )
            return "?"
        return stdout.decode("utf-8", errors="replace").strip()

    @staticmethod
    async def _discard_process(process: asyncio.subprocess.Process) -> None:
        """Kill and reap a subprocess we no longer need, swallowing cleanup errors."""
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        with contextlib.suppress(Exception):
            await process.wait()

    @staticmethod
    def _parse_timestamp(value: str | None) -> datetime | None:
        """Parse an ISO timestamp, returning None when absent or unusable.

        Naive datetimes are deliberately rejected: ``relative_time`` requires a
        timezone-aware value, which is exactly why the old handler rendered the
        raw string instead.
        """
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed.tzinfo is not None else None
