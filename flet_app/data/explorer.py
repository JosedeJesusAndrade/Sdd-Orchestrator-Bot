"""Explorer data: browse + CRUD over ``WorkdirService`` v2 (FD-08).

Every operation returns the core DTO/failure unchanged (typed failures pass
through untouched; the view maps ``kind`` to copy). After a successful mutation
the controller re-lists the affected directory so no stale listing survives —
``create_child``/``rename``/``delete`` refresh the parent, ``select`` refreshes
the selected directory itself.

No widgets, no copy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from services.conversation import ConversationKey
from services.workdir_service import (
    DirectoryEntry,
    DirectoryListing,
    WorkdirFailure,
)


class WorkdirExplorerPort(Protocol):
    """The subset of ``WorkdirService`` v2 the explorer drives."""

    def roots(self) -> tuple[Path, ...]: ...

    async def list_dir(self, path: Path | str) -> DirectoryListing | WorkdirFailure: ...

    async def create_child(
        self, parent: Path | str, name: str, *, init_git: bool = False
    ) -> DirectoryEntry | WorkdirFailure: ...

    async def rename(self, path: Path | str, new_name: str) -> DirectoryEntry | WorkdirFailure: ...

    async def delete(self, path: Path | str) -> DirectoryEntry | WorkdirFailure: ...

    async def select(
        self, conversation_key: ConversationKey, path: Path | str
    ) -> DirectoryEntry | WorkdirFailure: ...


class ExplorerController:
    """Drive directory browsing and CRUD without widgets."""

    def __init__(self, workdir: WorkdirExplorerPort, key: ConversationKey) -> None:
        self._workdir = workdir
        self._key = key
        self._listing: DirectoryListing | WorkdirFailure | None = None

    @property
    def listing(self) -> DirectoryListing | WorkdirFailure | None:
        """The last listing produced by ``list_dir`` or a mutation refresh."""
        return self._listing

    def roots(self) -> tuple[Path, ...]:
        """The explorer's roots (home on every platform)."""
        return self._workdir.roots()

    async def list_dir(self, path: Path | str) -> DirectoryListing | WorkdirFailure:
        """List ``path`` and remember the result (typed failure passthrough)."""
        listing = await self._workdir.list_dir(path)
        self._listing = listing
        return listing

    async def create_child(
        self,
        parent: Path | str,
        name: str,
        *,
        init_git: bool = False,
    ) -> DirectoryEntry | WorkdirFailure:
        """Create a child directory, then refresh ``parent`` on success."""
        result = await self._workdir.create_child(parent, name, init_git=init_git)
        if isinstance(result, WorkdirFailure):
            return result
        await self._refresh(parent)
        return result

    async def rename(self, path: Path | str, new_name: str) -> DirectoryEntry | WorkdirFailure:
        """Rename a leaf directory, then refresh its parent on success."""
        result = await self._workdir.rename(path, new_name)
        if isinstance(result, WorkdirFailure):
            return result
        await self._refresh(result.path.parent)
        return result

    async def delete(self, path: Path | str) -> DirectoryEntry | WorkdirFailure:
        """Delete an empty directory, then refresh its parent on success."""
        result = await self._workdir.delete(path)
        if isinstance(result, WorkdirFailure):
            return result
        await self._refresh(result.path.parent)
        return result

    async def select(self, path: Path | str) -> DirectoryEntry | WorkdirFailure:
        """Select ``path`` as the workdir, then refresh the selected directory."""
        result = await self._workdir.select(self._key, path)
        if isinstance(result, WorkdirFailure):
            return result
        await self._refresh(result.path)
        return result

    async def _refresh(self, path: Path | str) -> None:
        self._listing = await self._workdir.list_dir(path)
