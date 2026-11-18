"""Security matrix for :class:`~services.workdir_paths.PathGuard`.

The path policy was extracted from ``WorkdirService`` (orthogonalization F1).
These tests pin the perimeter in isolation: lexical normalization, containment,
the symlink-escape re-check, forbidden targets and name validation. The
``WorkdirService`` suite keeps covering the same rules through the public
browse/CRUD surface.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from services.workdir_failures import WorkdirFailure, WorkdirFailureKind
from services.workdir_paths import PathGuard, same_path


@pytest.fixture
def home(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    return root


@pytest.fixture
def guard(home: Path) -> PathGuard:
    return PathGuard(home)


def _kind(result: object) -> WorkdirFailureKind:
    assert isinstance(result, WorkdirFailure), f"expected WorkdirFailure, got {result!r}"
    return result.kind


# ── normalize (lexical) ────────────────────────────────────────────────


def test_normalize_collapses_dotdot_lexically(home: Path, guard: PathGuard) -> None:
    result = guard.normalize(home / "a" / ".." / "b")

    assert isinstance(result, Path)
    assert result == home / "b"
    assert ".." not in result.parts


def test_normalize_makes_a_relative_path_absolute(home: Path, guard: PathGuard) -> None:
    result = guard.normalize("relative/dir")

    assert isinstance(result, Path)
    assert result.is_absolute()


# ── resolve_within (containment + existence + symlinks) ────────────────


def test_resolve_within_accepts_an_existing_directory(home: Path, guard: PathGuard) -> None:
    target = home / "proj"
    target.mkdir()

    result = guard.resolve_within(target)

    assert isinstance(result, Path)
    assert result == target.resolve()


def test_resolve_within_rejects_a_missing_path(home: Path, guard: PathGuard) -> None:
    assert _kind(guard.resolve_within(home / "nope")) is WorkdirFailureKind.NOT_FOUND


def test_resolve_within_rejects_a_file(home: Path, guard: PathGuard) -> None:
    target = home / "file.txt"
    target.write_text("x", encoding="utf-8")

    assert _kind(guard.resolve_within(target)) is WorkdirFailureKind.NOT_A_DIRECTORY


def test_resolve_within_rejects_the_parent_of_home(home: Path, guard: PathGuard) -> None:
    assert _kind(guard.resolve_within(home / "..")) is WorkdirFailureKind.OUTSIDE_ROOT


def test_resolve_within_rejects_traversal_even_when_missing(home: Path, guard: PathGuard) -> None:
    traversal = home / ".." / ".." / "etc"

    # Security-first: OUTSIDE_ROOT wins over NOT_FOUND.
    assert _kind(guard.resolve_within(traversal)) is WorkdirFailureKind.OUTSIDE_ROOT


def test_resolve_within_rejects_a_symlink_escape(tmp_path: Path, home: Path) -> None:
    guard = PathGuard(home)
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / "escape").symlink_to(outside, target_is_directory=True)

    assert _kind(guard.resolve_within(home / "escape")) is WorkdirFailureKind.SYMLINK_ESCAPE


def test_resolve_within_accepts_a_symlink_inside_home(home: Path, guard: PathGuard) -> None:
    real = home / "real"
    real.mkdir()
    (home / "link").symlink_to(real, target_is_directory=True)

    result = guard.resolve_within(home / "link")

    assert isinstance(result, Path)
    assert result == real.resolve()


# ── is_forbidden ───────────────────────────────────────────────────────


def test_is_forbidden_for_home(home: Path, guard: PathGuard) -> None:
    assert guard.is_forbidden(home) is True


def test_is_forbidden_for_the_filesystem_root(home: Path, guard: PathGuard) -> None:
    assert guard.is_forbidden(Path(home.anchor)) is True


def test_a_regular_child_is_not_forbidden(home: Path, guard: PathGuard) -> None:
    assert guard.is_forbidden(home / "proj") is False


@pytest.mark.skipif(os.name != "nt", reason="Windows system directories only")
def test_is_forbidden_for_a_windows_system_dir(home: Path, guard: PathGuard) -> None:
    system_root = os.environ.get("SYSTEMROOT")
    if not system_root:
        pytest.skip("no SystemRoot in the environment")

    assert guard.is_forbidden(Path(system_root)) is True


@pytest.mark.skipif(os.name == "nt", reason="POSIX system directories only")
def test_is_forbidden_for_a_posix_system_dir(home: Path, guard: PathGuard) -> None:
    assert guard.is_forbidden(Path("/etc")) is True


# ── validate_name ──────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["proj", "a b", "with.dots", "ñ-and-ü", "x" * 200])
def test_validate_name_accepts_single_segments(guard: PathGuard, name: str) -> None:
    assert guard.validate_name(name) is True


@pytest.mark.parametrize("name", ["", ".", "..", "   ", "x/y", "x\\y"])
def test_validate_name_rejects_unsafe_names(guard: PathGuard, name: str) -> None:
    assert guard.validate_name(name) is False


# ── same_path ──────────────────────────────────────────────────────────


def test_same_path_compares_normalized_paths(home: Path) -> None:
    assert same_path(home / "a" / ".." / "b", home / "b") is True
    assert same_path(home / "a", home / "b") is False
