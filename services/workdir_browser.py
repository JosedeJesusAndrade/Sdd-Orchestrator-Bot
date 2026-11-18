"""WorkdirBrowser: the READ half of the workdir domain (orthogonalization F2).

Extracted from the ``WorkdirService`` god-class. This module owns every read:

  - ``roots`` — the explorer's single root (``home``);
  - ``list_dir`` — one directory observation (dirs first);
  - ``ancestors`` — the ``home`` → ``path`` chain;
  - ``current`` — the persisted ``workdir`` setting (defaults to the legacy
    root), the read side of the legacy ``/wdir`` surface.

It never writes and never spawns a subprocess. The security policy is the
INJECTED :class:`services.workdir_paths.PathGuard` (shared with the mutator, not
duplicated here). Blocking filesystem work runs in ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from services.workdir_contract import (
    WORKDIR_SETTING,
    DirectoryEntry,
    DirectoryListing,
    WorkdirStore,
)
from services.workdir_failures import WorkdirFailure, WorkdirFailureKind
from services.workdir_paths import PathGuard, same_path

if TYPE_CHECKING:
    from services.conversation import ConversationKey


@dataclass(frozen=True)
class _Scan:
    """Internal: the result of one ``iterdir`` scan (runs in a thread)."""

    entries: tuple[DirectoryEntry, ...]
    is_git: bool
    is_writable: bool


class WorkdirBrowser:
    """Read paths, listings, ancestor chains and the current workdir."""

    def __init__(
        self,
        store: WorkdirStore,
        *,
        home: Path,
        guard: PathGuard,
        default_workdir: Path,
    ) -> None:
        self._store = store
        self._home = home
        self._guard = guard
        self._default_workdir = default_workdir

    # ── Browse ─────────────────────────────────────────────────────────

    def roots(self) -> tuple[Path, ...]:
        """The explorer's single root: home (WD-03)."""
        return (self._home,)

    async def ancestors(self, path: Path | str) -> tuple[Path, ...] | WorkdirFailure:
        """Return home → ``path`` inclusive, or a typed failure."""
        validated = self._guard.resolve_within(path)
        if isinstance(validated, WorkdirFailure):
            return validated
        return self._chain(validated)

    async def list_dir(self, path: Path | str) -> DirectoryListing | WorkdirFailure:
        """List one directory (dirs+files, dirs first) inside home (WD-03)."""
        validated = self._guard.resolve_within(path)
        if isinstance(validated, WorkdirFailure):
            return validated
        real = validated

        scan = await asyncio.to_thread(self._scan, real)
        if isinstance(scan, WorkdirFailure):
            return scan

        real_home = self._home.resolve()
        parent = None if same_path(real, real_home) else real.parent
        return DirectoryListing(
            path=real,
            parent=parent,
            entries=scan.entries,
            ancestors=self._chain(real),
            is_git=scan.is_git,
            is_writable=scan.is_writable,
        )

    # ── Current (the read side of the legacy ``/wdir``) ────────────────

    async def current(self, conversation_key: ConversationKey) -> Path:
        """Return the conversation's current workdir (defaults to the legacy root)."""
        raw = await self._store.get_chat_setting(
            conversation_key, WORKDIR_SETTING, str(self._default_workdir)
        )
        return Path(raw)

    # ── Internals ──────────────────────────────────────────────────────

    def _chain(self, real: Path) -> tuple[Path, ...]:
        """Home → ``real`` inclusive, home first (WD-03)."""
        home = self._home.resolve()
        chain: list[Path] = [real]
        current = real
        while not same_path(current, home):
            parent = current.parent
            if parent == current:
                break
            current = parent
            chain.append(current)
        chain.reverse()
        return tuple(chain)

    def _scan(self, real: Path) -> _Scan | WorkdirFailure:
        """Blocking listing work executed via ``asyncio.to_thread``."""
        try:
            children = list(real.iterdir())
        except PermissionError as error:
            return WorkdirFailure(WorkdirFailureKind.PERMISSION_DENIED, real, str(error))
        except OSError as error:
            return WorkdirFailure(WorkdirFailureKind.PERMISSION_DENIED, real, str(error))

        entries: list[DirectoryEntry] = []
        for child in children:
            is_symlink = child.is_symlink()
            try:
                is_dir = child.is_dir()
            except OSError:
                is_dir = False
            is_git = False
            if is_dir:
                try:
                    is_git = (child / ".git").exists()
                except OSError:
                    is_git = False
            entries.append(
                DirectoryEntry(
                    name=child.name,
                    path=child,
                    is_dir=is_dir,
                    is_git=is_git,
                    is_symlink=is_symlink,
                )
            )

        entries.sort(key=lambda entry: (not entry.is_dir, entry.name.casefold(), entry.name))
        return _Scan(
            entries=tuple(entries),
            is_git=(real / ".git").exists(),
            is_writable=os.access(str(real), os.W_OK),
        )
