"""WorkdirMutator: the WRITE half of the workdir domain (orthogonalization F2).

Extracted from the ``WorkdirService`` god-class. This module owns every write:

  - ``create_child`` — new child directory, optional ``git init`` (WD-04);
  - ``rename`` — rename the last segment only (WD-06);
  - ``delete`` — remove an EMPTY directory, never follow a symlink (WD-07);
  - ``select`` — validate + persist the effective workdir (WD-05);
  - ``persist_workdir`` — THE single writer of ``settings["workdir"]`` (WD-02),
    also used by the legacy ``/wdir`` switch in the facade.

The security policy is the INJECTED :class:`services.workdir_paths.PathGuard`
(shared with the browser, not duplicated here). Blocking filesystem work runs in
``asyncio.to_thread``; ``git`` is argv-only and opt-in.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from services.workdir_contract import WORKDIR_SETTING, DirectoryEntry, WorkdirStore
from services.workdir_failures import WorkdirFailure, WorkdirFailureKind
from services.workdir_paths import PathGuard

if TYPE_CHECKING:
    from services.conversation import ConversationKey


def _best_effort_rmdir(path: Path) -> None:
    """Remove an empty directory, ignoring failures (flag #4 cleanup)."""
    with contextlib.suppress(OSError):
        path.rmdir()


def _is_empty(path: Path) -> bool:
    with os.scandir(path) as entries:
        return next(entries, None) is None


class WorkdirMutator:
    """Create, rename, delete and select directories; the single workdir writer."""

    def __init__(self, store: WorkdirStore, *, guard: PathGuard) -> None:
        self._store = store
        self._guard = guard

    async def create_child(
        self,
        parent: Path | str,
        name: str,
        *,
        init_git: bool = False,
    ) -> DirectoryEntry | WorkdirFailure:
        """Create a child directory, optionally ``git init``-ed (WD-04)."""
        if not self._guard.validate_name(name):
            return WorkdirFailure(
                WorkdirFailureKind.INVALID_NAME,
                Path(str(parent)) / name,
                "name must be a single path segment",
            )

        validated = self._guard.resolve_within(parent)
        if isinstance(validated, WorkdirFailure):
            return validated
        target = validated / name

        if os.path.lexists(str(target)):
            return WorkdirFailure(WorkdirFailureKind.TARGET_EXISTS, target, "already exists")

        # ``git`` opt-in only: precheck BEFORE creating so a missing binary
        # leaves no empty directory behind (WD-04).
        if init_git and shutil.which("git") is None:
            return WorkdirFailure(
                WorkdirFailureKind.GIT_UNAVAILABLE, target, "git is not installed"
            )

        try:
            await asyncio.to_thread(target.mkdir)
        except FileExistsError:
            return WorkdirFailure(WorkdirFailureKind.TARGET_EXISTS, target, "already exists")
        except PermissionError as error:
            return WorkdirFailure(WorkdirFailureKind.PERMISSION_DENIED, target, str(error))
        except OSError as error:
            return WorkdirFailure(WorkdirFailureKind.PERMISSION_DENIED, target, str(error))

        if init_git and not await self._git_init(target):
            # Flag #4: ``git init`` failed after the dir exists → best-effort
            # remove the empty directory and report git unavailable.
            await asyncio.to_thread(_best_effort_rmdir, target)
            return WorkdirFailure(WorkdirFailureKind.GIT_UNAVAILABLE, target, "git init failed")

        return DirectoryEntry(
            name=name,
            path=target,
            is_dir=True,
            is_git=init_git and (target / ".git").exists(),
            is_symlink=False,
        )

    async def rename(self, path: Path | str, new_name: str) -> DirectoryEntry | WorkdirFailure:
        """Rename a directory's last segment only (WD-06)."""
        lexical = self._guard.normalize(path)
        if isinstance(lexical, WorkdirFailure):
            return lexical

        if not self._guard.validate_name(new_name):
            return WorkdirFailure(
                WorkdirFailureKind.INVALID_NAME,
                lexical.parent / new_name,
                "name must be a single path segment",
            )

        if self._guard.is_forbidden(lexical):
            return WorkdirFailure(WorkdirFailureKind.FORBIDDEN, lexical, "forbidden target")

        validated = self._guard.resolve_within(lexical)
        if isinstance(validated, WorkdirFailure):
            return validated
        real = validated

        if self._guard.is_forbidden(real):
            return WorkdirFailure(WorkdirFailureKind.FORBIDDEN, real, "forbidden target")

        target = real.parent / new_name
        if os.path.lexists(str(target)):
            return WorkdirFailure(WorkdirFailureKind.TARGET_EXISTS, target, "already exists")

        try:
            await asyncio.to_thread(real.rename, target)
        except PermissionError as error:
            return WorkdirFailure(WorkdirFailureKind.PERMISSION_DENIED, real, str(error))
        except OSError as error:
            return WorkdirFailure(WorkdirFailureKind.PERMISSION_DENIED, real, str(error))

        return DirectoryEntry(
            name=new_name,
            path=target,
            is_dir=True,
            is_git=(target / ".git").exists(),
            is_symlink=False,
        )

    async def delete(self, path: Path | str) -> DirectoryEntry | WorkdirFailure:
        """Delete an EMPTY directory permanently; never follow a symlink (WD-07)."""
        lexical = self._guard.normalize(path)
        if isinstance(lexical, WorkdirFailure):
            return lexical

        # A symlink argument is never followed: the target stays untouched.
        if lexical.is_symlink():
            return WorkdirFailure(
                WorkdirFailureKind.NOT_A_DIRECTORY, lexical, "symlink is not a directory"
            )

        if self._guard.is_forbidden(lexical):
            return WorkdirFailure(WorkdirFailureKind.FORBIDDEN, lexical, "forbidden target")

        validated = self._guard.resolve_within(lexical)
        if isinstance(validated, WorkdirFailure):
            return validated
        real = validated

        if self._guard.is_forbidden(real):
            return WorkdirFailure(WorkdirFailureKind.FORBIDDEN, real, "forbidden target")

        try:
            empty = await asyncio.to_thread(_is_empty, real)
        except PermissionError as error:
            return WorkdirFailure(WorkdirFailureKind.PERMISSION_DENIED, real, str(error))
        except OSError as error:
            return WorkdirFailure(WorkdirFailureKind.PERMISSION_DENIED, real, str(error))

        if not empty:
            return WorkdirFailure(
                WorkdirFailureKind.DIRECTORY_NOT_EMPTY, real, "directory is not empty"
            )

        try:
            await asyncio.to_thread(real.rmdir)
        except PermissionError as error:
            return WorkdirFailure(WorkdirFailureKind.PERMISSION_DENIED, real, str(error))
        except OSError as error:
            if error.errno in (errno.ENOTEMPTY, errno.EEXIST):
                return WorkdirFailure(WorkdirFailureKind.DIRECTORY_NOT_EMPTY, real, str(error))
            return WorkdirFailure(WorkdirFailureKind.PERMISSION_DENIED, real, str(error))

        return DirectoryEntry(
            name=real.name, path=real, is_dir=True, is_git=False, is_symlink=False
        )

    async def select(
        self, conversation_key: ConversationKey, path: Path | str
    ) -> DirectoryEntry | WorkdirFailure:
        """Validate ``path`` and persist it as the effective workdir (WD-05)."""
        validated = self._guard.resolve_within(path)
        if isinstance(validated, WorkdirFailure):
            return validated
        real = validated

        await self.persist_workdir(conversation_key, real)
        return DirectoryEntry(
            name=real.name,
            path=real,
            is_dir=True,
            is_git=(real / ".git").exists(),
            is_symlink=False,
        )

    # ── Single writer ──────────────────────────────────────────────────

    async def persist_workdir(self, conversation_key: ConversationKey, path: Path) -> None:
        """The ONE writer of ``settings["workdir"]`` (flag #1: WD-02).

        Public because the legacy ``/wdir`` switch (in the facade) resolves
        projects against the legacy root — not the v2 ``home`` perimeter — and
        must still write through this single path.
        """
        await self._store.set_chat_setting(conversation_key, WORKDIR_SETTING, str(path))

    # ── Internals ──────────────────────────────────────────────────────

    async def _git_init(self, target: Path) -> bool:
        """Run ``git init`` (argv only, no shell); return success."""
        try:
            process = await asyncio.create_subprocess_exec(
                "git",
                "init",
                str(target),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError:
            return False
        try:
            return_code = await process.wait()
        except OSError:
            return False
        return return_code == 0
