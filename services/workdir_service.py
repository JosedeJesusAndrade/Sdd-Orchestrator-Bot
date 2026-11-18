"""WorkdirService: the workdir facade (legacy ``/wdir`` + v2 browse/CRUD).

Orthogonalization F2 split the original god-class along the READ/WRITE axis:

  - :class:`services.workdir_browser.WorkdirBrowser` — every read (``roots``,
    ``list_dir``, ``ancestors``, the persisted ``current``);
  - :class:`services.workdir_mutator.WorkdirMutator` — every write
    (``create_child``, ``rename``, ``delete``, ``select``) plus THE single
    writer of ``settings["workdir"]`` (``persist_workdir``);
  - this module — the FACADE: it wires the browser + the mutator over ONE shared
    :class:`services.workdir_paths.PathGuard` and keeps the legacy ``list`` /
    ``switch`` / ``current`` surface the Telegram characterization pins.

Behavior is IDENTICAL to the pre-split class (a pure refactor). The DTOs, the
settings port and the setting key live in :mod:`services.workdir_contract` and
the typed failures in :mod:`services.workdir_failures`; both are re-exported
here for backward compatibility.

Legacy (kept, behaviorally identical — the Telegram characterization pins it):
  - ``list`` / ``switch`` / ``current``; ``.git``-only listing of the legacy
    root, alias/partial/ambiguous query resolution, ``is_current`` marker.

v2 (delegated):
  - ``roots`` / ``ancestors`` / ``list_dir`` → the browser;
  - ``create_child`` / ``rename`` / ``delete`` / ``select`` → the mutator.

Design guarantees:
  - **PTB-free**: ``services/`` never imports a frontend. No UI copy lives here;
    the view maps the DTOs/failures to human strings.
  - **Injectable**: the legacy ``root=`` seam stays for ``/wdir``; v2 browses
    ``home`` (injectable, defaults ``Path.home()``) so tests use ``tmp_path``.
  - **Typed failures, not exceptions**: every expected filesystem failure is
    RETURNED as a :class:`WorkdirFailure` (never raised, no localized copy).
  - **Secure**: the perimeter is ``home``; the path policy lives in
    :class:`services.workdir_paths.PathGuard` — ONE guard shared by the browser
    and the mutator, never duplicated. No shell; subprocesses are argv-only.

Case sensitivity: the alias/query lookup stays case-insensitive on every
platform. The legacy *current-workdir marker* deliberately compares
``str(entry) == str(current)`` and therefore stays exact/case-sensitive — this
pins the Phase 0 characterization behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from services.effective_workdir import env_fallback_workdir
from services.workdir_browser import WorkdirBrowser
from services.workdir_contract import WORKDIR_SETTING as WORKDIR_SETTING
from services.workdir_contract import DirectoryEntry as DirectoryEntry
from services.workdir_contract import DirectoryListing as DirectoryListing
from services.workdir_contract import WorkdirStore as WorkdirStore
from services.workdir_failures import WorkdirFailure as WorkdirFailure
from services.workdir_failures import WorkdirFailureKind as WorkdirFailureKind
from services.workdir_mutator import WorkdirMutator
from services.workdir_paths import PathGuard

if TYPE_CHECKING:
    from collections.abc import Mapping

    from services.conversation import ConversationKey


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


# ── Helpers (legacy ``/wdir``) ─────────────────────────────────────────


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


# ── Facade ─────────────────────────────────────────────────────────────


class WorkdirService:
    """Facade over the read (browser) and write (mutator) halves."""

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
        home_path = Path.home() if home is None else Path(home)
        # ONE guard for both halves: the security perimeter is not duplicated.
        guard = PathGuard(home_path)
        self._browser = WorkdirBrowser(
            store,
            home=home_path,
            guard=guard,
            default_workdir=self._root,
        )
        self._mutator = WorkdirMutator(store, guard=guard)

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

        await self._mutator.persist_workdir(conversation_key, entry.path)
        return ResolveResult(query=normalized, entry=entry)

    async def current(self, conversation_key: ConversationKey) -> Path:
        """Return the conversation's current workdir (defaults to the root)."""
        return await self._browser.current(conversation_key)

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

    # ── v2 browse (delegated to the browser) ───────────────────────────

    def roots(self) -> tuple[Path, ...]:
        """The explorer's single root: home (WD-03)."""
        return self._browser.roots()

    async def ancestors(self, path: Path | str) -> tuple[Path, ...] | WorkdirFailure:
        """Return home → ``path`` inclusive, or a typed failure."""
        return await self._browser.ancestors(path)

    async def list_dir(self, path: Path | str) -> DirectoryListing | WorkdirFailure:
        """List one directory (dirs+files, dirs first) inside home (WD-03)."""
        return await self._browser.list_dir(path)

    # ── v2 CRUD (delegated to the mutator) ─────────────────────────────

    async def create_child(
        self,
        parent: Path | str,
        name: str,
        *,
        init_git: bool = False,
    ) -> DirectoryEntry | WorkdirFailure:
        """Create a child directory, optionally ``git init``-ed (WD-04)."""
        return await self._mutator.create_child(parent, name, init_git=init_git)

    async def rename(self, path: Path | str, new_name: str) -> DirectoryEntry | WorkdirFailure:
        """Rename a directory's last segment only (WD-06)."""
        return await self._mutator.rename(path, new_name)

    async def delete(self, path: Path | str) -> DirectoryEntry | WorkdirFailure:
        """Delete an EMPTY directory permanently; never follow a symlink (WD-07)."""
        return await self._mutator.delete(path)

    async def select(
        self, conversation_key: ConversationKey, path: Path | str
    ) -> DirectoryEntry | WorkdirFailure:
        """Validate ``path`` and persist it as the effective workdir (WD-05)."""
        return await self._mutator.select(conversation_key, path)
