"""Unit tests for ``WorkdirService`` (core-isolation item 6).

These exercise the headless service directly (no Telegram) and complement the
``/wdir`` characterization tests, which pin the handler + message output.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from services.conversation import make_conversation_key
from services.session_store import SessionStore
from services.workdir_service import (
    AmbiguousWorkdirError,
    DirectoryEntry,
    DirectoryListing,
    WorkdirFailure,
    WorkdirFailureKind,
    WorkdirNoProjectsError,
    WorkdirNotFoundError,
    WorkdirPermissionDeniedError,
    WorkdirRootNotFoundError,
    WorkdirService,
)
from tests.seam_support import probe_import

KEY = make_conversation_key("telegram", "1")


def _project(root: Path, name: str, *, git_file: bool = False) -> Path:
    """Create a project folder with a ``.git`` directory OR a ``.git`` file."""
    project = root / name
    project.mkdir(parents=True)
    if git_file:
        (project / ".git").write_text("gitdir: ../.git/worktrees/x", encoding="utf-8")
    else:
        (project / ".git").mkdir()
    return project


@pytest.fixture
def root(tmp_path: Path) -> Path:
    projects = tmp_path / "projects"
    projects.mkdir()
    return projects


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "sessions.json")


def _service(root: Path, store: SessionStore) -> WorkdirService:
    return WorkdirService(store, root=root)


# ── Listing ─────────────────────────────────────────────────────────────


async def test_list_discovers_git_dir_and_git_file(root: Path, store: SessionStore) -> None:
    _project(root, "Alpha Project")
    _project(root, "beta", git_file=True)
    (root / "notgit").mkdir()

    snapshot = await _service(root, store).list(KEY)

    assert snapshot.error is None
    assert {entry.name for entry in snapshot.entries} == {"Alpha Project", "beta"}
    aliases = {entry.name: entry.alias for entry in snapshot.entries}
    assert aliases["Alpha Project"] == "alpha"
    assert aliases["beta"] == "beta"


async def test_list_marks_current_with_exact_string(root: Path, store: SessionStore) -> None:
    beta = _project(root, "beta")
    await store.set_chat_setting(KEY, "workdir", str(beta))

    snapshot = await _service(root, store).list(KEY)

    [entry] = snapshot.entries
    assert entry.is_current is True
    assert snapshot.current == beta


async def test_current_marker_is_case_sensitive(root: Path, store: SessionStore) -> None:
    _project(root, "beta")
    await store.set_chat_setting(KEY, "workdir", str(root / "BETA"))

    snapshot = await _service(root, store).list(KEY)

    [entry] = snapshot.entries
    assert entry.is_current is False


async def test_list_without_projects_reports_no_projects(root: Path, store: SessionStore) -> None:
    (root / "notgit").mkdir()

    snapshot = await _service(root, store).list(KEY)

    assert isinstance(snapshot.error, WorkdirNoProjectsError)
    assert snapshot.entries == ()


# ── Switching ───────────────────────────────────────────────────────────


async def test_switch_by_alias_persists(root: Path, store: SessionStore) -> None:
    beta = _project(root, "beta")

    result = await _service(root, store).switch(KEY, "beta")

    assert result.found
    assert result.entry is not None
    assert result.entry.path == beta
    assert await store.get_chat_setting(KEY, "workdir") == str(beta)


async def test_switch_by_full_name_with_spaces(root: Path, store: SessionStore) -> None:
    alpha = _project(root, "Alpha Project")

    result = await _service(root, store).switch(KEY, "Alpha Project")

    assert result.entry is not None
    assert result.entry.path == alpha


async def test_switch_is_case_insensitive(root: Path, store: SessionStore) -> None:
    beta = _project(root, "beta")

    result = await _service(root, store).switch(KEY, "BETA")

    assert result.entry is not None
    assert result.entry.path == beta


async def test_switch_by_unique_partial_match(root: Path, store: SessionStore) -> None:
    solo = _project(root, "solo-project")

    result = await _service(root, store).switch(KEY, "solo-p")

    assert result.entry is not None
    assert result.entry.path == solo


async def test_switch_to_a_git_file_project(root: Path, store: SessionStore) -> None:
    worktree = _project(root, "worktree", git_file=True)

    result = await _service(root, store).switch(KEY, "worktree")

    assert result.entry is not None
    assert result.entry.path == worktree


async def test_ambiguous_partial_match_reports_candidates(root: Path, store: SessionStore) -> None:
    _project(root, "alpha-one")
    _project(root, "alpha-two")

    result = await _service(root, store).switch(KEY, "alpha-")

    assert not result.found
    assert isinstance(result.error, AmbiguousWorkdirError)
    assert result.candidates == ("alpha-one", "alpha-two")
    assert await store.get_chat_setting(KEY, "workdir") is None


async def test_short_alias_resolves_to_the_first_project(root: Path, store: SessionStore) -> None:
    first = _project(root, "alpha-one")
    _project(root, "alpha-two")

    result = await _service(root, store).switch(KEY, "alpha")

    assert result.entry is not None
    assert result.entry.path == first


async def test_unknown_project_reports_not_found(root: Path, store: SessionStore) -> None:
    _project(root, "beta")

    result = await _service(root, store).switch(KEY, "zzz")

    assert isinstance(result.error, WorkdirNotFoundError)
    assert result.query == "zzz"
    assert await store.get_chat_setting(KEY, "workdir") is None


async def test_switch_without_projects_reports_no_projects(root: Path, store: SessionStore) -> None:
    (root / "notgit").mkdir()

    result = await _service(root, store).switch(KEY, "anything")

    assert isinstance(result.error, WorkdirNoProjectsError)


# ── Failure modes ───────────────────────────────────────────────────────


async def test_missing_root_reports(tmp_path: Path, store: SessionStore) -> None:
    missing = tmp_path / "does-not-exist"
    service = WorkdirService(store, root=missing)

    snapshot = await service.list(KEY)
    result = await service.switch(KEY, "beta")

    assert isinstance(snapshot.error, WorkdirRootNotFoundError)
    assert snapshot.error.root == missing
    assert isinstance(result.error, WorkdirRootNotFoundError)
    assert await store.get_chat_setting(KEY, "workdir") is None


async def test_permission_error_reports(
    root: Path, store: SessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(root, "beta")

    def _deny(_self: Path) -> Any:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "iterdir", _deny)
    service = _service(root, store)

    snapshot = await service.list(KEY)
    result = await service.switch(KEY, "beta")

    assert isinstance(snapshot.error, WorkdirPermissionDeniedError)
    assert isinstance(result.error, WorkdirPermissionDeniedError)
    assert await store.get_chat_setting(KEY, "workdir") is None


# ── Current ─────────────────────────────────────────────────────────────


async def test_current_defaults_to_root(root: Path, store: SessionStore) -> None:
    assert await _service(root, store).current(KEY) == root


async def test_current_reads_the_setting(root: Path, store: SessionStore) -> None:
    beta = _project(root, "beta")
    await store.set_chat_setting(KEY, "workdir", str(beta))

    assert await _service(root, store).current(KEY) == beta


# ── Boundary ────────────────────────────────────────────────────────────


def test_workdir_service_is_ptb_free() -> None:
    returncode, loaded = probe_import("services.workdir_service")

    assert returncode == 0
    assert loaded == set()


# ── v2: browse + CRUD + security (flet-data-layer P3) ──────────────────


@pytest.fixture
def home(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    return root


def _v2(store: SessionStore, home: Path) -> WorkdirService:
    return WorkdirService(store, root=home, home=home)


def _kind(result: object) -> WorkdirFailureKind:
    assert isinstance(result, WorkdirFailure), f"expected WorkdirFailure, got {result!r}"
    return result.kind


# roots / ancestors / list_dir


async def test_v2_roots_is_home(home: Path, store: SessionStore) -> None:
    assert _v2(store, home).roots() == (home,)


async def test_list_dir_lists_dirs_before_files(home: Path, store: SessionStore) -> None:
    (home / "beta").mkdir()
    (home / "alpha").mkdir()
    (home / "zeta.txt").write_text("x", encoding="utf-8")

    listing = await _v2(store, home).list_dir(home)

    assert isinstance(listing, DirectoryListing)
    assert listing.parent is None
    assert listing.ancestors == (home,)
    assert [entry.name for entry in listing.entries] == ["alpha", "beta", "zeta.txt"]
    assert listing.is_writable is True


async def test_list_dir_child_reports_parent_and_ancestors(home: Path, store: SessionStore) -> None:
    sub = home / "proj" / "sub"
    sub.mkdir(parents=True)

    listing = await _v2(store, home).list_dir(sub)

    assert isinstance(listing, DirectoryListing)
    assert listing.parent == home / "proj"
    assert listing.ancestors == (home, home / "proj", sub)


async def test_list_dir_marks_git_directories(home: Path, store: SessionStore) -> None:
    (home / "proj").mkdir()
    (home / "proj" / ".git").mkdir()
    (home / "plain").mkdir()

    listing = await _v2(store, home).list_dir(home)

    assert isinstance(listing, DirectoryListing)
    by_name = {entry.name: entry for entry in listing.entries}
    assert by_name["proj"].is_git is True
    assert by_name["plain"].is_git is False
    assert listing.is_git is False


async def test_list_dir_missing_is_not_found(home: Path, store: SessionStore) -> None:
    assert _kind(await _v2(store, home).list_dir(home / "nope")) is (WorkdirFailureKind.NOT_FOUND)


async def test_list_dir_file_is_not_a_directory(home: Path, store: SessionStore) -> None:
    target = home / "file.txt"
    target.write_text("x", encoding="utf-8")

    assert _kind(await _v2(store, home).list_dir(target)) is (WorkdirFailureKind.NOT_A_DIRECTORY)


async def test_list_dir_parent_is_outside_root(home: Path, store: SessionStore) -> None:
    assert _kind(await _v2(store, home).list_dir(home / "..")) is (WorkdirFailureKind.OUTSIDE_ROOT)


async def test_traversal_etc_is_outside_root(home: Path, store: SessionStore) -> None:
    traversal = home / ".." / ".." / "etc"

    assert _kind(await _v2(store, home).list_dir(traversal)) is (WorkdirFailureKind.OUTSIDE_ROOT)


async def test_ancestors_chain_from_home(home: Path, store: SessionStore) -> None:
    sub = home / "a" / "b"
    sub.mkdir(parents=True)

    assert await _v2(store, home).ancestors(sub) == (home, home / "a", sub)


# create_child


async def test_create_child_without_git(home: Path, store: SessionStore) -> None:
    entry = await _v2(store, home).create_child(home, "new")

    assert isinstance(entry, DirectoryEntry)
    assert (home / "new").is_dir()
    assert entry.is_git is False


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
async def test_create_child_with_git_init(home: Path, store: SessionStore) -> None:
    entry = await _v2(store, home).create_child(home, "new", init_git=True)

    assert isinstance(entry, DirectoryEntry)
    assert (home / "new" / ".git").exists()
    assert entry.is_git is True


async def test_create_child_git_missing_does_not_create(
    home: Path, store: SessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("services.workdir_service.shutil.which", lambda _name: None)

    result = await _v2(store, home).create_child(home, "new", init_git=True)

    assert _kind(result) is WorkdirFailureKind.GIT_UNAVAILABLE
    assert not (home / "new").exists()


class _FailingProcess:
    async def wait(self) -> int:
        return 1


async def _failing_exec(*_args: object, **_kwargs: object) -> _FailingProcess:
    return _FailingProcess()


async def test_create_child_git_init_failure_removes_dir(
    home: Path, store: SessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("services.workdir_service.shutil.which", lambda _name: "git")
    monkeypatch.setattr("services.workdir_service.asyncio.create_subprocess_exec", _failing_exec)

    result = await _v2(store, home).create_child(home, "new", init_git=True)

    assert _kind(result) is WorkdirFailureKind.GIT_UNAVAILABLE
    assert not (home / "new").exists()


@pytest.mark.parametrize("name", ["", ".", "..", "x/y", "x\\y", "   "])
async def test_create_child_invalid_name(home: Path, store: SessionStore, name: str) -> None:
    assert _kind(await _v2(store, home).create_child(home, name)) is (
        WorkdirFailureKind.INVALID_NAME
    )


async def test_create_child_target_exists(home: Path, store: SessionStore) -> None:
    (home / "new").mkdir()

    assert _kind(await _v2(store, home).create_child(home, "new")) is (
        WorkdirFailureKind.TARGET_EXISTS
    )


# select


async def test_select_git_dir_persists(home: Path, store: SessionStore) -> None:
    proj = home / "proj"
    proj.mkdir()
    (proj / ".git").mkdir()

    entry = await _v2(store, home).select(KEY, proj)

    assert isinstance(entry, DirectoryEntry)
    assert entry.is_git is True
    assert await store.get_chat_setting(KEY, "workdir") == str(proj)


async def test_select_non_git_succeeds(home: Path, store: SessionStore) -> None:
    plain = home / "plain"
    plain.mkdir()

    entry = await _v2(store, home).select(KEY, plain)

    assert isinstance(entry, DirectoryEntry)
    assert entry.is_git is False
    assert await store.get_chat_setting(KEY, "workdir") == str(plain)


async def test_select_symlink_inside_home_persists_real(home: Path, store: SessionStore) -> None:
    real = home / "real"
    real.mkdir()
    link = home / "link"
    link.symlink_to(real, target_is_directory=True)

    entry = await _v2(store, home).select(KEY, link)

    assert isinstance(entry, DirectoryEntry)
    assert await store.get_chat_setting(KEY, "workdir") == str(real.resolve())


async def test_select_symlink_escape(tmp_path: Path, store: SessionStore) -> None:
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = home / "escape"
    link.symlink_to(outside, target_is_directory=True)

    result = await _v2(store, home).select(KEY, link)

    assert _kind(result) is WorkdirFailureKind.SYMLINK_ESCAPE
    assert await store.get_chat_setting(KEY, "workdir") is None


async def test_list_dir_symlink_escape(tmp_path: Path, store: SessionStore) -> None:
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = home / "escape"
    link.symlink_to(outside, target_is_directory=True)

    result = await _v2(store, home).list_dir(link)

    assert _kind(result) is WorkdirFailureKind.SYMLINK_ESCAPE


async def test_select_outside_root(home: Path, store: SessionStore) -> None:
    result = await _v2(store, home).select(KEY, home / ".." / ".." / "etc")

    assert _kind(result) is WorkdirFailureKind.OUTSIDE_ROOT
    assert await store.get_chat_setting(KEY, "workdir") is None


# rename


async def test_rename_leaf_preserves_git(home: Path, store: SessionStore) -> None:
    proj = home / "proj"
    proj.mkdir()
    (proj / ".git").mkdir()

    entry = await _v2(store, home).rename(proj, "proj2")

    assert isinstance(entry, DirectoryEntry)
    assert (home / "proj2").is_dir()
    assert not (home / "proj").exists()
    assert (home / "proj2" / ".git").is_dir()
    assert entry.is_git is True


async def test_rename_collision(home: Path, store: SessionStore) -> None:
    (home / "a").mkdir()
    (home / "b").mkdir()

    assert _kind(await _v2(store, home).rename(home / "a", "b")) is (
        WorkdirFailureKind.TARGET_EXISTS
    )


async def test_rename_not_leaf(home: Path, store: SessionStore) -> None:
    (home / "a").mkdir()

    assert _kind(await _v2(store, home).rename(home / "a", "x/y")) is (
        WorkdirFailureKind.INVALID_NAME
    )


async def test_rename_home_forbidden(home: Path, store: SessionStore) -> None:
    assert _kind(await _v2(store, home).rename(home, "nope")) is (WorkdirFailureKind.FORBIDDEN)
    assert home.exists()


async def test_rename_missing(home: Path, store: SessionStore) -> None:
    assert _kind(await _v2(store, home).rename(home / "nope", "x")) is (
        WorkdirFailureKind.NOT_FOUND
    )


# delete


async def test_delete_empty(home: Path, store: SessionStore) -> None:
    empty = home / "empty"
    empty.mkdir()

    entry = await _v2(store, home).delete(empty)

    assert isinstance(entry, DirectoryEntry)
    assert not empty.exists()


async def test_delete_non_empty(home: Path, store: SessionStore) -> None:
    full = home / "full"
    full.mkdir()
    (full / "file").write_text("x", encoding="utf-8")

    result = await _v2(store, home).delete(full)

    assert _kind(result) is WorkdirFailureKind.DIRECTORY_NOT_EMPTY
    assert full.exists()


async def test_delete_home_forbidden(home: Path, store: SessionStore) -> None:
    assert _kind(await _v2(store, home).delete(home)) is WorkdirFailureKind.FORBIDDEN
    assert home.exists()


async def test_delete_filesystem_root_forbidden(home: Path, store: SessionStore) -> None:
    root = Path(home.anchor)

    assert _kind(await _v2(store, home).delete(root)) is WorkdirFailureKind.FORBIDDEN


@pytest.mark.skipif(os.name != "nt", reason="Windows system directories only")
async def test_delete_windows_system_dir_forbidden(home: Path, store: SessionStore) -> None:
    system_root = os.environ.get("SYSTEMROOT")
    if not system_root:
        pytest.skip("no SystemRoot in the environment")

    assert _kind(await _v2(store, home).delete(Path(system_root))) is (WorkdirFailureKind.FORBIDDEN)


@pytest.mark.skipif(os.name == "nt", reason="POSIX system directories only")
async def test_delete_posix_system_dir_forbidden(home: Path, store: SessionStore) -> None:
    assert _kind(await _v2(store, home).delete(Path("/etc"))) is (WorkdirFailureKind.FORBIDDEN)


async def test_delete_symlink_argument_is_not_a_directory(
    tmp_path: Path, store: SessionStore
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    target = home / "target"
    target.mkdir()
    link = home / "link"
    link.symlink_to(target, target_is_directory=True)

    result = await _v2(store, home).delete(link)

    assert _kind(result) is WorkdirFailureKind.NOT_A_DIRECTORY
    assert target.exists()


async def test_delete_missing(home: Path, store: SessionStore) -> None:
    assert _kind(await _v2(store, home).delete(home / "nope")) is (WorkdirFailureKind.NOT_FOUND)


# P3 boundary gate: env tokens live only in the effective-workdir facade


def test_env_tokens_only_in_effective_workdir() -> None:
    services = Path(__file__).resolve().parent.parent / "services"
    pattern = re.compile(r"\bOPENCODE_WORKDIR\b|\bBASE_DIR\b")

    offenders = sorted(
        path.name
        for path in services.rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    )

    assert offenders == ["effective_workdir.py"]
