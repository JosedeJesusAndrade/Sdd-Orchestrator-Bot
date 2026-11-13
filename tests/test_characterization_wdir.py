"""Characterization tests for ``/wdir`` (``handlers/ci.py::wdir_command``).

The project discovery, alias generation, partial matching, ambiguity and the
current-workdir marker are about to move into ``WorkdirService``. These tests
pin the CURRENT behavior so the extraction cannot drift.

All filesystem work happens under ``tmp_path``; ``config.OPENCODE_WORKDIR`` is
patched because the handler re-imports it on every call. ``/wdir`` replies via
``MessageSender.send_plain`` (not ``Message.reply_text``), so the fake sender
is the observation point.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import core_config
from handlers.ci import wdir_command
from tests.handler_fakes import (
    CHAT_ID,
    FakeChatSettingsStore,
    FakeMessage,
    FakeSender,
    make_context,
    make_update,
)


def _project(root: Path, name: str, *, git_file: bool = False) -> Path:
    """Create a project folder with a ``.git`` directory OR a ``.git`` file."""
    project = root / name
    project.mkdir(parents=True)
    if git_file:
        (project / ".git").write_text("gitdir: ../.git/worktrees/x", encoding="utf-8")
    else:
        (project / ".git").mkdir()
    return project


async def _run(
    store: FakeChatSettingsStore,
    args: list[str] | None,
) -> FakeSender:
    sender = FakeSender()
    container = SimpleNamespace(message_sender=sender, session_store=store)
    await wdir_command(make_update(FakeMessage()), make_context(container, args=args))
    return sender


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    projects = tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setattr(core_config, "OPENCODE_WORKDIR", str(projects))
    return projects


# ── Listing ─────────────────────────────────────────────────────────────


async def test_list_discovers_git_dir_and_git_file(root: Path) -> None:
    _project(root, "Alpha Project")
    _project(root, "beta", git_file=True)
    (root / "notgit").mkdir()

    sender = await _run(FakeChatSettingsStore(), None)

    text = sender.plain_texts[-1]
    assert "Proyectos disponibles" in text
    assert "`alpha`" in text
    assert "Alpha Project" in text
    assert "`beta`" in text
    # A folder without .git is not a project.
    assert "notgit" not in text


async def test_list_marks_the_current_workdir(root: Path) -> None:
    _project(root, "Alpha Project")
    beta = _project(root, "beta")
    store = FakeChatSettingsStore({"workdir": str(beta)})

    sender = await _run(store, None)

    lines = sender.plain_texts[-1].splitlines()
    beta_line = next(line for line in lines if "`beta`" in line)
    alpha_line = next(line for line in lines if "Alpha Project" in line)
    assert beta_line.startswith("🟢")
    assert not alpha_line.startswith("🟢")


async def test_current_marker_uses_exact_path_string(root: Path) -> None:
    """The marker compares ``str(entry) == str(current)`` (case-sensitive).

    This pins the current semantics: a differing casing of the same path must
    NOT match. If the extraction switches to ``Path`` equality (case-insensitive
    on Windows) this test must be revisited on purpose.
    """
    _project(root, "beta")
    store = FakeChatSettingsStore({"workdir": str(root / "BETA")})

    sender = await _run(store, None)

    beta_line = next(line for line in sender.plain_texts[-1].splitlines() if "`beta`" in line)
    assert not beta_line.startswith("🟢")


async def test_list_without_projects_warns(root: Path) -> None:
    (root / "notgit").mkdir()

    sender = await _run(FakeChatSettingsStore(), None)

    assert "No se encontraron proyectos" in sender.plain_texts[-1]


async def test_missing_root_reports_and_does_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "does-not-exist"
    monkeypatch.setattr(core_config, "OPENCODE_WORKDIR", str(missing))
    store = FakeChatSettingsStore()

    sender = await _run(store, None)

    assert "Directorio no encontrado" in sender.plain_texts[-1]
    assert store.set_calls == []


async def test_permission_error_reports(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(root, "beta")

    def _deny(_self: Path) -> Any:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "iterdir", _deny)
    store = FakeChatSettingsStore()

    sender = await _run(store, None)

    assert "Sin permisos para listar directorios" in sender.plain_texts[-1]
    assert store.set_calls == []


# ── Switching ───────────────────────────────────────────────────────────


async def test_switch_by_alias_persists_workdir(root: Path) -> None:
    beta = _project(root, "beta")
    store = FakeChatSettingsStore()

    sender = await _run(store, ["beta"])

    assert store.set_calls == [(CHAT_ID, "workdir", str(beta))]
    assert "Workdir cambiado" in sender.plain_texts[-1]


async def test_switch_by_full_name_with_spaces(root: Path) -> None:
    alpha = _project(root, "Alpha Project")
    store = FakeChatSettingsStore()

    await _run(store, ["Alpha", "Project"])

    assert store.settings["workdir"] == str(alpha)


async def test_switch_is_case_insensitive(root: Path) -> None:
    beta = _project(root, "beta")
    store = FakeChatSettingsStore()

    await _run(store, ["BETA"])

    assert store.settings["workdir"] == str(beta)


async def test_switch_by_unique_partial_match(root: Path) -> None:
    solo = _project(root, "solo-project")
    store = FakeChatSettingsStore()

    await _run(store, ["solo-p"])

    assert store.settings["workdir"] == str(solo)


async def test_switch_to_a_git_file_project(root: Path) -> None:
    worktree = _project(root, "worktree", git_file=True)
    store = FakeChatSettingsStore()

    await _run(store, ["worktree"])

    assert store.settings["workdir"] == str(worktree)


async def test_ambiguous_partial_match_reports_candidates(root: Path) -> None:
    _project(root, "alpha-one")
    _project(root, "alpha-two")
    store = FakeChatSettingsStore()

    sender = await _run(store, ["alpha-"])

    text = sender.plain_texts[-1]
    assert "Múltiples coincidencias" in text
    assert "alpha-one" in text
    assert "alpha-two" in text
    assert store.set_calls == []


async def test_short_alias_resolves_to_the_first_project(root: Path) -> None:
    """Surprise: the bare family name is a real exact key for the first match.

    ``short`` (first dash-segment) is inserted once, so ``/wdir alpha`` with
    ``alpha-one`` and ``alpha-two`` resolves to ``alpha-one`` instead of being
    ambiguous. The extraction must preserve this, not "improve" it.
    """
    first = _project(root, "alpha-one")
    _project(root, "alpha-two")
    store = FakeChatSettingsStore()

    await _run(store, ["alpha"])

    assert store.settings["workdir"] == str(first)


async def test_unknown_project_reports_not_found(root: Path) -> None:
    _project(root, "beta")
    store = FakeChatSettingsStore()

    sender = await _run(store, ["zzz"])

    assert "Proyecto no encontrado" in sender.plain_texts[-1]
    assert store.set_calls == []
