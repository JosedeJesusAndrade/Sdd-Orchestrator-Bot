"""Path policy for the workdir explorer (the security perimeter).

Extracted from ``WorkdirService`` (orthogonalization F1) so the traversal,
symlink-escape, forbidden and invalid-name rules are testable in isolation and
reusable by any future surface. The behavior is IDENTICAL to the inline policy
it replaces: lexical normalization first, then the ``is_relative_to`` check
(case-insensitive on Windows via ``normcase``), then ``resolve()`` and a second
containment check to catch symlink escapes.

:class:`PathGuard` never raises for expected filesystem conditions; every failure
is RETURNED as a :class:`services.workdir_failures.WorkdirFailure`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from services.workdir_failures import WorkdirFailure, WorkdirFailureKind

#: A validated real path, or the typed failure that prevented it.
Resolution = Path | WorkdirFailure


def same_path(left: Path, right: Path) -> bool:
    """Compare paths case-insensitively on Windows, exactly on POSIX."""
    return os.path.normcase(os.path.normpath(str(left))) == os.path.normcase(
        os.path.normpath(str(right))
    )


def _within(child: Path, parent: Path) -> bool:
    """True when ``child`` is ``parent`` or lies beneath it (normcased)."""
    child_norm = Path(os.path.normcase(os.path.normpath(str(child))))
    parent_norm = Path(os.path.normcase(os.path.normpath(str(parent))))
    return child_norm == parent_norm or child_norm.is_relative_to(parent_norm)


@dataclass(frozen=True)
class PathGuard:
    """Enforce the workdir policy for one injectable perimeter (``home``)."""

    home: Path

    def normalize(self, path: Path | str) -> Path | WorkdirFailure:
        """Normalize ``path`` to an absolute lexical path (no ``resolve``)."""
        normalized = Path(os.path.normpath(str(path)))
        if not normalized.is_absolute():
            normalized = Path(os.path.abspath(str(path)))
        if not normalized.is_absolute():
            return WorkdirFailure(WorkdirFailureKind.OUTSIDE_ROOT, normalized, "relative path")
        return normalized

    def resolve_within(self, path: Path | str) -> Resolution:
        """Validate an existing directory inside ``home``; return its real path.

        Security-first ordering (deliberate deviation from the design's literal
        list, see the P3 report): lexical ``OUTSIDE_ROOT`` is checked before
        existence so ``../../etc`` is always rejected even when it does not
        exist on the host. Inside home the order is ``NOT_FOUND`` →
        ``NOT_A_DIRECTORY`` → ``SYMLINK_ESCAPE``.
        """
        lexical = self.normalize(path)
        if isinstance(lexical, WorkdirFailure):
            return lexical

        if not _within(lexical, self.home):
            return WorkdirFailure(WorkdirFailureKind.OUTSIDE_ROOT, lexical, "outside home")

        if not os.path.lexists(str(lexical)):
            return WorkdirFailure(WorkdirFailureKind.NOT_FOUND, lexical, "does not exist")

        try:
            is_dir = lexical.is_dir()
        except OSError:
            is_dir = False
        if not is_dir:
            return WorkdirFailure(WorkdirFailureKind.NOT_A_DIRECTORY, lexical, "not a directory")

        real = lexical.resolve()
        if not _within(real, self.home.resolve()):
            return WorkdirFailure(WorkdirFailureKind.SYMLINK_ESCAPE, real, "symlink escapes home")
        return real

    def is_forbidden(self, path: Path) -> bool:
        """True for home, the filesystem root or a known system directory."""
        normalized = Path(os.path.abspath(os.path.normpath(str(path))))
        if same_path(normalized, self.home):
            return True
        if normalized.parent == normalized:
            return True
        return any(same_path(normalized, candidate) for candidate in self._system_dirs())

    def validate_name(self, name: str) -> bool:
        """True when ``name`` is a single, non-dot, non-empty segment (WD-04/WD-06)."""
        if name in ("", ".", ".."):
            return False
        if not name.strip():
            return False
        if "/" in name or "\\" in name:
            return False
        return not (os.sep in name or (os.altsep is not None and os.altsep in name))

    @staticmethod
    def _system_dirs() -> tuple[Path, ...]:
        """Platform system directories that destructive ops must never touch."""
        candidates: list[Path] = []
        for name in ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
            raw = os.environ.get(name)
            if raw:
                candidates.append(Path(raw))
        if os.name != "nt":
            candidates.extend(
                Path(entry) for entry in ("/bin", "/etc", "/usr", "/var", "/tmp", "/home", "/root")
            )
        return tuple(candidates)
