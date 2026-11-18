"""Typed workdir failures shared by the explorer and its path guard.

The kinds and the failure record moved here (orthogonalization F1) so both
:class:`services.workdir_paths.PathGuard` and :class:`services.workdir_service.
WorkdirService` can return them without an import cycle. ``workdir_service``
re-exports both names for backward compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class WorkdirFailureKind(StrEnum):
    """Every expected filesystem failure kind (WD-09)."""

    NOT_FOUND = "not_found"
    NOT_A_DIRECTORY = "not_a_directory"
    PERMISSION_DENIED = "permission_denied"
    OUTSIDE_ROOT = "outside_root"
    SYMLINK_ESCAPE = "symlink_escape"
    INVALID_NAME = "invalid_name"
    CONFLICT = "conflict"
    FORBIDDEN = "forbidden"
    DIRECTORY_NOT_EMPTY = "directory_not_empty"
    TARGET_EXISTS = "target_exists"
    GIT_UNAVAILABLE = "git_unavailable"


@dataclass(frozen=True)
class WorkdirFailure:
    """A typed, non-raising workdir failure.

    ``detail`` is diagnostic (never localized/user-facing copy); the view maps
    ``kind`` to its own strings.
    """

    kind: WorkdirFailureKind
    path: Path
    detail: str | None = None
