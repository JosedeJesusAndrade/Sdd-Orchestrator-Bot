"""Single source of truth for the *effective* workdir of a conversation.

Before this module the workdir was triple-sourced: ``core_config.OPENCODE_WORKDIR``
(the env default), the per-chat ``settings["workdir"]`` mirror and
``WorkdirService``'s exploration root. That made switching workdir cosmetic for
history, prompts and status.

:class:`EffectiveWorkdir` resolves ONE path per conversation using a strict
precedence and is injected into the four consumers (``PromptService``,
``ConversationsService``, ``StatusService`` and ``SessionService``). It never
writes anything: ``SettingsService.set("workdir")`` -> ``WorkdirService.select``
remains the only writer.

The module is also the FACADE for the two environment tokens: it is the only
module under ``services/`` allowed to reference
``core_config.OPENCODE_WORKDIR``/``core_config.BASE_DIR``. Everyone else imports
:data:`ENV_FALLBACK_WORKDIR` / :data:`PROJECT_ROOT` from here.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import core_config
from utils.logging import get_module_logger, log_exception

if TYPE_CHECKING:
    from services.agent_sessions import SessionCatalog
    from services.conversation import ConversationKey
    from services.session_store import SessionInfo

logger = get_module_logger(__name__)

#: Env-derived default workdir (``OPENCODE_WORKDIR``, which defaults to
#: ``BASE_DIR``). Exposed as ``Path`` so no consumer re-parses the env string.
ENV_FALLBACK_WORKDIR: Path = Path(core_config.OPENCODE_WORKDIR)

#: The project root (the old ``core_config.BASE_DIR``). Consumers that only need
#: ``pyproject.toml`` / the repo directory import this instead of the token.
PROJECT_ROOT: Path = Path(core_config.BASE_DIR)


class WorkdirSource(StrEnum):
    """Where the resolved workdir came from (diagnostics for the UI)."""

    SETTING = "setting"
    ACTIVE_SESSION = "active_session"
    ENV_FALLBACK = "env_fallback"


@dataclass(frozen=True)
class WorkdirResolution:
    """One resolved workdir plus its provenance.

    ``mismatch`` is true only when a ``settings["workdir"]`` and a *different*
    active-session directory both exist (the setting wins).
    """

    path: Path
    source: WorkdirSource
    mismatch: bool


class WorkdirStore(Protocol):
    """Minimal port the resolver needs (``SessionStore`` satisfies it)."""

    async def get_chat_setting(
        self, conversation_id: ConversationKey, key: str, default: Any = None
    ) -> Any: ...

    async def get_active_session(self, conversation_id: ConversationKey) -> SessionInfo | None: ...


class EffectiveWorkdir:
    """Resolve the workdir for a conversation by precedence.

    Order (first non-empty wins):
      1. ``settings["workdir"]`` (read RAW — never through a defaulted accessor,
         which would inject the env fallback and hide the other levels).
      2. The active bot session's bound OpenCode session ``directory``.
      3. ``ENV_FALLBACK_WORKDIR`` (the env-derived default).

    A relative or empty setting is ignored. Paths are normalized (``..``
    collapsed) and must be absolute.
    """

    def __init__(
        self,
        store: WorkdirStore,
        catalog: SessionCatalog,
        fallback: Path,
    ) -> None:
        self._store = store
        self._catalog = catalog
        self._fallback = fallback

    @property
    def fallback(self) -> Path:
        """The env-derived default (so composition never touches the token)."""
        return self._fallback

    async def resolve(self, key: ConversationKey) -> WorkdirResolution:
        """Return the effective workdir for ``key`` (never writes state)."""
        active_directory = await self._active_directory(key)

        raw_setting = await self._store.get_chat_setting(key, "workdir", None)
        setting_path = self._normalize(raw_setting)
        if setting_path is not None:
            mismatch = active_directory is not None and not self._same_path(
                setting_path, active_directory
            )
            if mismatch:
                # Exactly one WARNING per resolve, only on a real divergence.
                logger.warning(
                    "Workdir setting diverges from the active session directory",
                    extra={
                        "event": "workdir_mismatch",
                        "setting_workdir": str(setting_path),
                        "active_session_workdir": str(active_directory),
                    },
                )
            return WorkdirResolution(
                path=setting_path,
                source=WorkdirSource.SETTING,
                mismatch=mismatch,
            )

        if active_directory is not None:
            return WorkdirResolution(
                path=active_directory,
                source=WorkdirSource.ACTIVE_SESSION,
                mismatch=False,
            )

        return WorkdirResolution(
            path=self._fallback,
            source=WorkdirSource.ENV_FALLBACK,
            mismatch=False,
        )

    async def _active_directory(self, key: ConversationKey) -> Path | None:
        """Return the active session's bound directory, or ``None``."""
        active = await self._store.get_active_session(key)
        if active is None or not active.real_id:
            return None
        try:
            summary = await self._catalog.get_session(active.real_id)
        except Exception:
            # A history-catalog failure must not break prompts/config: the
            # resolver simply skips level 2 and falls through.
            log_exception(
                "workdir_active_session_lookup_failed",
                module=__name__,
                level=logging.DEBUG,
                real_id=active.real_id,
            )
            return None
        if summary is None:
            return None
        return self._normalize(summary.directory)

    @staticmethod
    def _normalize(raw: object) -> Path | None:
        """Normalize an absolute path string, or return ``None`` when unusable."""
        if not isinstance(raw, str):
            return None
        text = raw.strip()
        if not text:
            return None
        path = Path(os.path.normpath(text))
        return path if path.is_absolute() else None

    @staticmethod
    def _same_path(left: Path, right: Path) -> bool:
        """Compare paths case-insensitively on Windows, exactly on POSIX."""
        return os.path.normcase(str(left)) == os.path.normcase(str(right))
