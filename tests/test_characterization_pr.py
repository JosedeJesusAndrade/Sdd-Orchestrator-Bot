"""Characterization tests for ``/pr`` (``PullRequestService`` + handler).

Pins the pure branch sanitizer, both subprocess runners, the CHANGELOG body
lifecycle and the five-stage PR flow (including the typed error mapping) so the
``PullRequestService`` extraction cannot change behavior.

No live ``git``/``gh``: both runners are replaced with fakes that record every
call and return scripted ``CommandResult`` tuples.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from handlers.ci import pr_command
from services.pull_request_service import (
    CommandResult,
    PullRequestService,
    SubprocessCommandRunner,
    sanitize_branch_name,
)
from tests.handler_fakes import (
    FakeChatSettingsStore,
    FakeMessage,
    FakeSender,
    make_context,
    make_update,
)

# ── Branch sanitizer ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Add Login Page", "add-login-page"),
        ("Hello, World!", "hello-world"),
        ("fix  double   spaces", "fix-double-spaces"),
        ("keep_underscore-and-dash", "keep_underscore-and-dash"),
    ],
)
def test_sanitize_branch_name_formats_title(title: str, expected: str) -> None:
    assert sanitize_branch_name(title) == expected


def test_sanitize_branch_name_truncates_to_fifty_chars() -> None:
    assert sanitize_branch_name("a" * 80) == "a" * 50


@pytest.mark.parametrize("title", ["", "!!!", "   "])
def test_sanitize_branch_name_falls_back_to_update(title: str) -> None:
    assert sanitize_branch_name(title) == "update"


# ── Subprocess runners ─────────────────────────────────────────────────


class _Proc:
    def __init__(self, returncode: int, stdout: bytes | None, stderr: bytes | None) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes | None, bytes | None]:
        return self._stdout, self._stderr


async def test_run_git_command_decodes_strips_and_pipes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def fake_exec(*args: Any, **kwargs: Any) -> _Proc:
        calls.append((args, kwargs))
        return _Proc(0, b"  main \n", b" warn ")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    result = await SubprocessCommandRunner("git").run("C:/repo", "status")

    assert (result.returncode, result.stdout, result.stderr) == (0, "main", "warn")
    assert calls[0][0] == ("git", "status")
    kwargs = calls[0][1]
    assert kwargs["cwd"] == "C:/repo"
    assert kwargs["stdout"] is asyncio.subprocess.PIPE
    assert kwargs["stderr"] is asyncio.subprocess.PIPE


async def test_run_git_command_replaces_invalid_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_exec(*args: Any, **kwargs: Any) -> _Proc:
        return _Proc(0, b"caf\xe9", None)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    result = await SubprocessCommandRunner("git").run("C:/repo", "log")

    assert result.returncode == 0
    assert result.stdout == "caf\ufffd"
    assert result.stderr == ""


async def test_run_gh_command_uses_resolved_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []

    async def fake_exec(*args: Any, **kwargs: Any) -> _Proc:
        calls.append(args)
        return _Proc(0, b"out", b"")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    result = await SubprocessCommandRunner(lambda: "C:/fake/gh.exe").run("C:/repo", "pr", "list")

    assert (result.returncode, result.stdout, result.stderr) == (0, "out", "")
    assert calls[0] == ("C:/fake/gh.exe", "pr", "list")


# ── /pr flow ───────────────────────────────────────────────────────────


class _FakeRunner:
    """Scripted async runner matching the ``CommandRunner`` port."""

    def __init__(self, results: list[tuple[int, str, str]] | None = None) -> None:
        self.results = list(results or [])
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def run(self, cwd: str, *args: str) -> CommandResult:
        self.calls.append((cwd, args))
        rc, out, err = self.results.pop(0) if self.results else (0, "", "")
        return CommandResult(returncode=rc, stdout=out, stderr=err)


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    workdir: Path,
    *,
    git: _FakeRunner,
    gh: _FakeRunner,
) -> tuple[FakeMessage, FakeSender, PullRequestService]:
    store = FakeChatSettingsStore({"workdir": str(workdir)})
    sender = FakeSender()
    service = PullRequestService(store, git=git, gh=gh)
    return FakeMessage(), sender, service


async def _run_pr(
    msg: FakeMessage,
    service: PullRequestService,
    sender: FakeSender,
    args: list[str] | None,
) -> None:
    container = SimpleNamespace(message_sender=sender, pull_request_service=service)
    await pr_command(make_update(msg), make_context(container, args=args))


async def test_pr_happy_path_runs_all_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "CHANGELOG.md").write_text("Ship it", encoding="utf-8")
    git = _FakeRunner([(0, "", "")] * 5)
    gh = _FakeRunner([(0, "https://github.com/acme/repo/pull/7", "")])
    msg, sender, service = _wire(monkeypatch, tmp_path, git=git, gh=gh)

    await _run_pr(msg, service, sender, ["Add", "Login"])

    workdir = str(tmp_path)
    checkout_call = git.calls[0]
    assert checkout_call[1][:2] == ("checkout", "-b")
    branch = checkout_call[1][2]
    assert branch.startswith("feature/add-login-")
    suffix = branch.rsplit("-", 1)[1]
    assert len(suffix) == 4
    assert all(char in "0123456789abcdef" for char in suffix)

    assert git.calls == [
        (workdir, ("checkout", "-b", branch)),
        (workdir, ("add", ".")),
        (workdir, ("commit", "-m", "Add Login")),
        (workdir, ("push", "-u", "origin", branch)),
        (workdir, ("checkout", "main")),
    ]
    assert gh.calls == [
        (
            workdir,
            (
                "pr",
                "create",
                "--title",
                "Add Login",
                "--body",
                "Ship it",
                "--base",
                "main",
                "--head",
                branch,
            ),
        )
    ]
    # CHANGELOG is consumed and deleted.
    assert not (tmp_path / "CHANGELOG.md").exists()

    joined = "\n".join(sender.plain_texts)
    assert "📦 Creando PR" in joined
    assert "✅ PR creada exitosamente." in joined
    assert "https://github.com/acme/repo/pull/7" in joined


async def test_pr_defaults_title_and_body_without_changelog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git = _FakeRunner([(0, "", "")] * 5)
    gh = _FakeRunner([(0, "no url here", "")])
    msg, sender, service = _wire(monkeypatch, tmp_path, git=git, gh=gh)

    await _run_pr(msg, service, sender, None)

    branch = git.calls[0][1][2]
    assert branch.startswith("feature/update-from-telegram-")
    gh_args = gh.calls[0][1]
    assert gh_args[3] == "Update from Telegram"
    assert gh_args[5] == "PR generated via Telegram Bot."
    assert gh_args[-1] == branch
    # Without a URL the first 200 chars of stdout are shown.
    assert "no url here" in sender.plain_texts[-1]


async def test_pr_nothing_to_commit_returns_to_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git = _FakeRunner(
        [(0, "", ""), (0, "", ""), (1, "", "nothing to commit, working tree clean"), (0, "", "")]
    )
    gh = _FakeRunner()
    msg, sender, service = _wire(monkeypatch, tmp_path, git=git, gh=gh)

    await _run_pr(msg, service, sender, ["Doc"])

    assert "No hay cambios para commitear" in sender.plain_texts[-1]
    assert gh.calls == []
    assert git.calls[-1] == (str(tmp_path), ("checkout", "main"))


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ([(1, "", "boom")], "❌ Error creando rama:"),
        ([(0, "", ""), (1, "", "nope")], "❌ Error en git add:"),
        ([(0, "", ""), (0, "", ""), (1, "", "bad")], "❌ Error en commit:"),
        ([(0, "", ""), (0, "", ""), (0, "", ""), (1, "", "no push")], "❌ Error en push:"),
    ],
)
async def test_pr_stage_failures_map_to_typed_messages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    results: list[tuple[int, str, str]],
    expected: str,
) -> None:
    git = _FakeRunner(results)
    gh = _FakeRunner()
    msg, sender, service = _wire(monkeypatch, tmp_path, git=git, gh=gh)

    await _run_pr(msg, service, sender, ["Feature"])

    assert expected in sender.plain_texts[-1]
    assert gh.calls == []


async def test_pr_gh_failure_reports_without_returning_to_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git = _FakeRunner([(0, "", "")] * 4)
    gh = _FakeRunner([(1, "", "gh boom")])
    msg, sender, service = _wire(monkeypatch, tmp_path, git=git, gh=gh)

    await _run_pr(msg, service, sender, ["Feature"])

    assert "❌ Error creando PR:" in sender.plain_texts[-1]
    # On gh failure the handler returns before the final ``checkout main``.
    assert (str(tmp_path), ("checkout", "main")) not in git.calls
    assert len(git.calls) == 4
