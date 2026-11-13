"""Unit tests for ``WorkdirService`` (core-isolation item 6).

These exercise the headless service directly (no Telegram) and complement the
``/wdir`` characterization tests, which pin the handler + message output.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from services.conversation import make_conversation_key
from services.session_store import SessionStore
from services.workdir_service import (
    AmbiguousWorkdirError,
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
