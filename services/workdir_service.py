"""WorkdirService: project discovery and switching, frontend-agnostic.

Extracted from ``handlers/ci.py::wdir_command`` (core-isolation item 6). This
module owns the ``/wdir`` business logic: discovering project folders (a
``.git`` directory OR file), generating aliases, resolving exact / partial /
ambiguous queries, reading the current-workdir marker, and persisting the
``workdir`` chat setting.

Design guarantees:
  - **PTB-free**: ``services/`` never imports a frontend. No UI copy lives here;
    the handler maps the DTOs/errors to human strings (the view).
  - **Cross-platform**: ``pathlib`` only, no ``os.path`` Windows-isms. Errors
    such as ``PermissionError`` are handled the same way on Linux and Windows.
  - **Injectable**: the root defaults to ``core_config.OPENCODE_WORKDIR`` and
    the store is injected, so tests use ``tmp_path`` with no global patching.

Case sensitivity: the alias/query lookup stays case-insensitive on every
platform (the original lowercases the key before matching). The *current-workdir
marker* deliberately compares ``str(entry) == str(current)`` and therefore stays
exact/case-sensitive — this pins the Phase 0 characterization behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import core_config

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


# ── Domain errors ──────────────────────────────────────────────────────


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


# ── DTOs ───────────────────────────────────────────────────────────────


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


# ── Service ────────────────────────────────────────────────────────────


class WorkdirService:
    """Discover and switch OpenCode project folders for a conversation."""

    def __init__(self, store: WorkdirStore, *, root: Path | None = None) -> None:
        self._store = store
        # Read the module attribute lazily so patching ``core_config`` (as the
        # characterization tests do) is honored by direct construction.
        self._root = Path(core_config.OPENCODE_WORKDIR) if root is None else root

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

        await self._store.set_chat_setting(conversation_key, WORKDIR_SETTING, str(entry.path))
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
