"""WorkdirService: project discovery, switching and filesystem CRUD.

Extracted from ``handlers/ci.py::wdir_command`` (core-isolation item 6). This
module owns the ``/wdir`` business logic **and** the v2 explorer's browse +
CRUD surface (``flet-data-layer`` phase P3):

Legacy (kept, behaviorally identical — the Telegram characterization pins it):
  - ``list`` / ``switch`` / ``current``; ``.git``-only listing of the legacy
    root, alias/partial/ambiguous query resolution, ``is_current`` marker.

v2 (browse + CRUD + security):
  - ``roots`` / ``ancestors`` / ``list_dir``;
  - ``create_child`` (optional ``git init``) / ``rename`` / ``delete`` /
    ``select`` (the only writer of ``settings["workdir"]``).

Design guarantees:
  - **PTB-free**: ``services/`` never imports a frontend. No UI copy lives here;
    the view maps the DTOs/failures to human strings.
  - **Injectable**: the legacy ``root=`` seam stays for ``/wdir``; v2 browses
    ``home`` (injectable, defaults ``Path.home()``) so tests use ``tmp_path``.
  - **Typed failures, not exceptions**: every expected filesystem failure is
    RETURNED as a :class:`WorkdirFailure` (never raised, no localized copy).
  - **Secure**: the perimeter is ``home``; the path policy lives in
    :class:`services.workdir_paths.PathGuard` — paths are validated lexically
    (``normpath`` + ``is_relative_to`` with ``normcase`` on Windows) AND then
    resolved (symlink escape check). No shell; subprocesses are argv-only;
    blocking filesystem work runs in ``asyncio.to_thread``.

Case sensitivity: the alias/query lookup stays case-insensitive on every
platform. The legacy *current-workdir marker* deliberately compares
``str(entry) == str(current)`` and therefore stays exact/case-sensitive — this
pins the Phase 0 characterization behavior.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import os
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from services.effective_workdir import env_fallback_workdir
from services.workdir_failures import WorkdirFailure as WorkdirFailure
from services.workdir_failures import WorkdirFailureKind as WorkdirFailureKind
from services.workdir_paths import PathGuard, same_path

if TYPE_CHECKING:
    from collections.abc import Mapping

    from services.conversation import ConversationKey

#: Chat setting that stores the per-conversation project path.
WORKDIR_SETTING = "workdir"


# ── Structural port ────────────────────────────────────────────────────


class WorkdirStore(Protocol):
    """Minimal chat-settings contract ``WorkdirService`` needs.

    ``SessionStore`` satisfies it; tests can pass a small in-memory double
    without inheriting from the concrete production class.
    """

    async def get_chat_setting(
        self, conversation_key: ConversationKey, key: str, default: Any = None
    ) -> Any: ...

    async def set_chat_setting(
        self, conversation_key: ConversationKey, key: str, value: object
    ) -> None: ...


# ── Domain errors (legacy ``/wdir``) ───────────────────────────────────


class WorkdirError(Exception):
    """Base class for workdir domain errors."""


class WorkdirRootNotFoundError(WorkdirError):
    """The configured projects root does not exist."""

    def __init__(self, root: Path) -> None:
        self.root = root
        super().__init__(f"Workdir root not found: {root}")


class WorkdirPermissionDeniedError(WorkdirError):
    """The root exists but could not be listed (permission denied)."""


class WorkdirNoProjectsError(WorkdirError):
    """The root exists but contains no project (no ``.git`` entry)."""


class WorkdirNotFoundError(WorkdirError):
    """The query matched no project (and no partial candidates)."""

    def __init__(self, query: str) -> None:
        self.query = query
        super().__init__(f"Workdir not found: {query}")


class AmbiguousWorkdirError(WorkdirError):
    """The query matched more than one project prefix."""

    def __init__(self, candidates: tuple[str, ...]) -> None:
        self.candidates = candidates
        super().__init__(f"Ambiguous workdir query: {', '.join(candidates)}")


# ── v2 DTOs (typed failures + browse results) ──────────────────────────


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


@dataclass(frozen=True)
class _Scan:
    """Internal: the result of one ``iterdir`` scan (runs in a thread)."""

    entries: tuple[DirectoryEntry, ...]
    is_git: bool
    is_writable: bool


# ── Legacy DTOs ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProjectEntry:
    """A single discovered project.

    ``alias`` is the short display key (first dash-separated segment of the
    lowercased, space-to-dash name) — the same value the Telegram listing shows.
    """

    name: str
    alias: str
    path: Path
    is_current: bool = False


@dataclass(frozen=True)
class ProjectsSnapshot:
    """The projects root observed for one conversation.

    ``error`` is set when the root is missing / unreadable / empty; the view
    decides how to render each case. ``current`` mirrors the ``workdir``
    setting converted to a ``Path``.
    """

    root: Path
    current: Path
    entries: tuple[ProjectEntry, ...]
    error: WorkdirError | None = None


@dataclass(frozen=True)
class ResolveResult:
    """Outcome of a switch request.

    ``entry`` is the resolved target (already persisted). When resolution
    fails, ``error`` carries one of the typed errors; ambiguity is represented
    by :class:`AmbiguousWorkdirError`, whose ``candidates`` are exposed here.
    """

    query: str
    entry: ProjectEntry | None = None
    error: WorkdirError | None = None

    @property
    def found(self) -> bool:
        return self.entry is not None

    @property
    def candidates(self) -> tuple[str, ...]:
        if isinstance(self.error, AmbiguousWorkdirError):
            return self.error.candidates
        return ()


@dataclass(frozen=True)
class _Discovery:
    """Internal: the resolution map plus the ordered project entries."""

    projects: Mapping[str, Path]
    entries: tuple[ProjectEntry, ...]


# ── Helpers ────────────────────────────────────────────────────────────


def _normalize(query: str) -> str:
    """Normalize a user query the way ``/wdir`` always has.

    Lowercases and turns spaces into dashes. It intentionally does NOT strip
    (the original did ``" ".join(args).lower().replace(" ", "-")``).
    """
    return query.lower().replace(" ", "-")


def _short_alias(name: str) -> str:
    return name.lower().replace(" ", "-").split("-")[0]


def _entry_for(path: Path) -> ProjectEntry:
    return ProjectEntry(name=path.name, alias=_short_alias(path.name), path=path)


def _resolve(projects: Mapping[str, Path], query: str) -> ProjectEntry:
    """Resolve ``query`` against the discovery map (exact before partial)."""
    target = projects.get(query)
    if target is None:
        matches = [key for key in projects if key.startswith(query)]
        if len(matches) > 1:
            raise AmbiguousWorkdirError(tuple(matches[:5]))
        if not matches:
            raise WorkdirNotFoundError(query)
        target = projects[matches[0]]
    return _entry_for(target)


def _best_effort_rmdir(path: Path) -> None:
    """Remove an empty directory, ignoring failures (flag #4 cleanup)."""
    with contextlib.suppress(OSError):
        path.rmdir()


def _is_empty(path: Path) -> bool:
    with os.scandir(path) as entries:
        return next(entries, None) is None


# ── Service ────────────────────────────────────────────────────────────


class WorkdirService:
    """Discover/switch project folders (legacy) and browse/CRUD (v2)."""

    def __init__(
        self,
        store: WorkdirStore,
        *,
        root: Path | None = None,
        home: Path | None = None,
    ) -> None:
        self._store = store
        # Legacy ``/wdir`` root. Read lazily so the characterization tests that
        # monkeypatch ``core_config`` (via the facade) are honored.
        self._root = env_fallback_workdir() if root is None else root
        # v2 perimeter: navigation above home is forbidden (WD-03/WD-08).
        self._home = Path.home() if home is None else Path(home)
        self._guard = PathGuard(self._home)

    # ── Legacy ``/wdir`` ───────────────────────────────────────────────

    async def list(self, conversation_key: ConversationKey) -> ProjectsSnapshot:
        """List projects, marking the current one (exact ``str()`` match)."""
        try:
            discovery = self._discover()
        except WorkdirError as error:
            return ProjectsSnapshot(
                root=self._root,
                current=self._root,
                entries=(),
                error=error,
            )

        if not discovery.entries:
            return ProjectsSnapshot(
                root=self._root,
                current=self._root,
                entries=(),
                error=WorkdirNoProjectsError(),
            )

        raw_current = await self._store.get_chat_setting(
            conversation_key, WORKDIR_SETTING, str(self._root)
        )
        current = Path(raw_current)
        entries = tuple(
            replace(entry, is_current=str(entry.path) == str(current))
            for entry in discovery.entries
        )
        return ProjectsSnapshot(root=self._root, current=current, entries=entries)

    async def switch(self, conversation_key: ConversationKey, query: str) -> ResolveResult:
        """Resolve a query and persist the target on success."""
        normalized = _normalize(query)
        try:
            discovery = self._discover()
        except WorkdirError as error:
            return ResolveResult(query=normalized, error=error)

        if not discovery.entries:
            return ResolveResult(query=normalized, error=WorkdirNoProjectsError())

        try:
            entry = _resolve(discovery.projects, normalized)
        except WorkdirError as error:
            return ResolveResult(query=normalized, error=error)

        await self._persist_workdir(conversation_key, entry.path)
        return ResolveResult(query=normalized, entry=entry)

    async def current(self, conversation_key: ConversationKey) -> Path:
        """Return the conversation's current workdir (defaults to the root)."""
        raw = await self._store.get_chat_setting(conversation_key, WORKDIR_SETTING, str(self._root))
        return Path(raw)

    def _discover(self) -> _Discovery:
        """Scan the root for project folders; raises typed errors on failure."""
        if not self._root.exists():
            raise WorkdirRootNotFoundError(self._root)

        projects: dict[str, Path] = {}
        entries: list[ProjectEntry] = []
        try:
            for entry in sorted(self._root.iterdir()):
                if not (entry.is_dir() and (entry / ".git").exists()):
                    continue
                name = entry.name
                alias_key = name.lower().replace(" ", "-")
                short = alias_key.split("-")[0]
                projects[name] = entry
                projects[alias_key] = entry
                if short not in projects:
                    projects[short] = entry
                entries.append(_entry_for(entry))
        except PermissionError as error:
            raise WorkdirPermissionDeniedError() from error

        return _Discovery(projects=projects, entries=tuple(entries))

    # ── v2 browse ──────────────────────────────────────────────────────

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

    # ── v2 CRUD ────────────────────────────────────────────────────────

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

        await self._persist_workdir(conversation_key, real)
        return DirectoryEntry(
            name=real.name,
            path=real,
            is_dir=True,
            is_git=(real / ".git").exists(),
            is_symlink=False,
        )

    # ── Single writer + validation internals ───────────────────────────

    async def _persist_workdir(self, conversation_key: ConversationKey, path: Path) -> None:
        """The ONE writer of ``settings["workdir"]`` (flag #1: WD-02)."""
        await self._store.set_chat_setting(conversation_key, WORKDIR_SETTING, str(path))

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
