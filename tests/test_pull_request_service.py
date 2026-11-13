"""Unit tests for the ``PullRequestService`` seam (core-isolation item 7).

The ``/pr`` characterization file pins the end-to-end handler behavior. This
file covers the pieces that are specific to the extraction:

  * ``resolve_gh`` is portable — ``shutil.which("gh")`` wins on Linux and the
    hard-coded Windows install locations are only consulted on Windows.
  * ``CommandRunner`` is a fakeable port (no live ``git``/``gh``).
"""

from __future__ import annotations

import os
import shutil
import sys

import pytest

from services.conversation import make_conversation_key
from services.pull_request_service import (
    CommandResult,
    PullRequestService,
    SubprocessCommandRunner,
    resolve_gh,
)
from tests.handler_fakes import FakeChatSettingsStore


class _NoopRunner:
    async def run(self, cwd: str, *args: str) -> CommandResult:
        return CommandResult(returncode=0, stdout="", stderr="")


# ── gh resolution (the Linux note) ─────────────────────────────────────


def test_resolve_gh_prefers_path_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """``shutil.which('gh')`` is the first candidate and wins on Linux."""
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/gh" if name == "gh" else None)
    monkeypatch.setattr(os.path, "isfile", lambda path: path == "/usr/local/bin/gh")

    assert resolve_gh() == "/usr/local/bin/gh"


def test_resolve_gh_ignores_windows_locations_off_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Linux the ``~\\AppData`` / ``C:\\Program Files`` candidates never run."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    # Even if a Windows path "exists", it is not in the candidate list on Linux.
    monkeypatch.setattr(os.path, "isfile", lambda path: True)

    assert resolve_gh() == "gh"


def test_resolve_gh_falls_back_to_bare_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(os.path, "isfile", lambda path: False)

    assert resolve_gh() == "gh"


# ── Ports ──────────────────────────────────────────────────────────────


def test_new_branch_name_uses_sanitized_slug_and_short_suffix() -> None:
    service = PullRequestService(FakeChatSettingsStore(), git=_NoopRunner(), gh=_NoopRunner())

    branch = service.new_branch_name("Add Login")

    assert branch.startswith("feature/add-login-")
    suffix = branch.rsplit("-", 1)[1]
    assert len(suffix) == 4
    assert all(char in "0123456789abcdef" for char in suffix)


async def test_create_uses_the_injected_runners() -> None:
    class _RecordingRunner:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[str, ...]]] = []

        async def run(self, cwd: str, *args: str) -> CommandResult:
            self.calls.append((cwd, args))
            return CommandResult(returncode=0, stdout="", stderr="")

    git = _RecordingRunner()
    gh = _RecordingRunner()
    service = PullRequestService(FakeChatSettingsStore({"workdir": "C:/repo"}), git=git, gh=gh)

    await service.create(
        make_conversation_key("telegram", "1"),
        "Ship it",
        branch_name="feature/ship-it-abcd",
    )

    assert git.calls[0] == ("C:/repo", ("checkout", "-b", "feature/ship-it-abcd"))
    assert gh.calls[0][1][:2] == ("pr", "create")


def test_subprocess_runner_accepts_a_dynamic_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = {"calls": 0}

    def _resolve() -> str:
        resolved["calls"] += 1
        return "gh"

    runner = SubprocessCommandRunner(_resolve)
    assert runner._resolve() == "gh"
    assert resolved["calls"] == 1
