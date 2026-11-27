"""Shared workdir contract: settings port, setting key and browse DTOs.

Extracted from ``services.workdir_service`` (orthogonalization F2) so the
browser, the mutator and the legacy facade can share the same types without an
import cycle. ``services.workdir_service`` re-exports every name here for
backward compatibility.

The typed failures live next door in :mod:`services.workdir_failures` (they had
to be split first, see F1) and the security policy in
:mod:`services.workdir_paths`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from services.conversation import ConversationKey

#: Chat setting that stores the per-conversation project path.
WORKDIR_SETTING = "workdir"

#: Chat setting that stores the recently selected workdirs (newest first).
RECENT_WORKDIRS_SETTING = "recent_workdirs"

#: How many recent workdirs the explorer keeps per conversation.
MAX_RECENT_WORKDIRS = 5


# ── Structural port ────────────────────────────────────────────────────


class WorkdirStore(Protocol):
    """Minimal chat-settings contract the workdir halves need.

    ``SessionStore`` satisfies it; tests can pass a small in-memory double
    without inheriting from the concrete production class.
    """

    async def get_chat_setting(
        self, conversation_key: ConversationKey, key: str, default: Any = None
    ) -> Any: ...

    async def set_chat_setting(
        self, conversation_key: ConversationKey, key: str, value: object
    ) -> None: ...


# ── v2 DTOs (browse results) ───────────────────────────────────────────


@dataclass(frozen=True)
class DirectoryEntry:
    """One immediate child of a listed directory."""

    name: str
    path: Path
    is_dir: bool
    is_git: bool
    is_symlink: bool


@dataclass(frozen=True)
class DirectoryListing:
    """One directory observation for the explorer."""

    path: Path
    parent: Path | None
    entries: tuple[DirectoryEntry, ...]
    ancestors: tuple[Path, ...]
    is_git: bool
    is_writable: bool
