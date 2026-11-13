"""PullRequestService: the ``/pr`` git + gh pipeline, frontend-agnostic.

Extracted from ``handlers/ci.py::pr_command`` (core-isolation item 7). This
module owns the five-stage GitHub PR flow:

    git checkout -b <branch>  ->  git add .  ->  git commit -m <title>
    ->  git push -u origin <branch>  ->  gh pr create --base main ...

Design guarantees:
  - **PTB-free**: ``services/`` never imports a frontend. No UI copy lives here;
    the handler maps the typed :class:`PullRequestError`\\ s to human strings.
  - **Ports, not subprocesses**: the git/gh calls go through the injectable
    :class:`CommandRunner` port, so tests script every stage with no live
    ``git``/``gh`` and the production defaults keep ``create_subprocess_exec``.
  - **Cross-platform ``gh``**: :func:`resolve_gh` asks ``shutil.which("gh")``
    first (the Linux/VPS path). The hard-coded Windows install locations only
    participate on Windows, so the Linux production never depends on them.
  - **Injectable workdir**: the per-conversation ``workdir`` chat setting is
    read through a small structural port (``SessionStore`` satisfies it).

The CHANGELOG lifecycle is preserved exactly: a repo-root ``CHANGELOG.md`` is
read as the PR body and unlinked after reading; otherwise a default body is
used.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from services.conversation import ConversationKey

#: Chat setting that stores the per-conversation project path.
WORKDIR_SETTING = "workdir"
#: Default target branch for the PR.
DEFAULT_BASE_BRANCH = "main"
#: Body used when the repo has no ``CHANGELOG.md``.
DEFAULT_BODY = "PR generated via Telegram Bot."


# ── Command execution port ─────────────────────────────────────────────


@dataclass(frozen=True)
class CommandResult:
    """The stdout/stderr/returncode of one subprocess invocation."""

    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    """A fakeable subprocess runner (git or gh)."""

    async def run(self, cwd: str, *args: str) -> CommandResult: ...


class PullRequestStore(Protocol):
    """Minimal chat-settings contract ``PullRequestService`` needs.

    ``SessionStore`` satisfies it; tests can pass a small in-memory double.
    """

    async def get_chat_setting(
        self, conversation_key: ConversationKey, key: str, default: Any = None
    ) -> Any: ...


def resolve_gh() -> str:
    """Locate the GitHub CLI executable, cross-platform.

    ``shutil.which("gh")`` resolves on Linux/macOS (the production VPS) and,
    normally, on Windows. The hard-coded Windows install locations are only
    consulted when running on Windows, so on Linux ``~\\AppData\\...`` is never
    expanded or tested. Falls back to bare ``"gh"`` (PATH lookup by the OS).
    """
    candidates: list[str | None] = [shutil.which("gh")]
    if sys.platform == "win32":
        candidates.extend(
            [
                shutil.which("gh.exe"),
                r"C:\Program Files\GitHub CLI\gh.exe",
                r"C:\Program Files (x86)\GitHub CLI\gh.exe",
                os.path.expanduser(r"~\AppData\Local\Microsoft\WindowsApps\gh.exe"),
            ]
        )
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return "gh"


class SubprocessCommandRunner:
    """Default :class:`CommandRunner` backed by ``create_subprocess_exec``.

    ``executable`` is either a literal name/path or a zero-argument resolver
    called at each invocation (``gh`` resolution can therefore change at
    runtime without rebuilding the graph).
    """

    def __init__(self, executable: str | Callable[[], str]) -> None:
        self._executable = executable

    def _resolve(self) -> str:
        if callable(self._executable):
            return self._executable()
        return self._executable

    async def run(self, cwd: str, *args: str) -> CommandResult:
        proc = await asyncio.create_subprocess_exec(
            self._resolve(),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await proc.communicate()
        return CommandResult(
            returncode=proc.returncode or 0,
            stdout=stdout.decode("utf-8", errors="replace").strip() if stdout else "",
            stderr=stderr.decode("utf-8", errors="replace").strip() if stderr else "",
        )


def default_runners() -> tuple[CommandRunner, CommandRunner]:
    """Build the production ``(git, gh)`` runners."""
    return SubprocessCommandRunner("git"), SubprocessCommandRunner(resolve_gh)


# ── Domain errors ──────────────────────────────────────────────────────


class PullRequestError(Exception):
    """A pipeline stage failed.

    ``stage`` is one of ``branch``, ``add``, ``commit``, ``push`` or ``pr``;
    the view maps it to the user-facing message. ``stderr`` is passed through.
    """

    def __init__(self, stage: str, stderr: str) -> None:
        self.stage = stage
        self.stderr = stderr
        super().__init__(f"PR stage failed ({stage}): {stderr}")


class NothingToCommitError(PullRequestError):
    """The commit stage reported nothing to commit (working tree clean)."""

    def __init__(self, output: str) -> None:
        super().__init__("commit", output)


# ── Result DTO ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PullRequestResult:
    """Outcome of a successful PR creation."""

    branch: str
    title: str
    url: str
    output: str


# ── Pure helpers ───────────────────────────────────────────────────────


def sanitize_branch_name(title: str) -> str:
    """Convert a PR title into a valid git branch name."""
    name = title.lower().strip()
    name = re.sub(r"[^a-z0-9\s_-]", "", name)
    name = re.sub(r"\s+", "-", name)
    return name[:50] or "update"


# ── Service ────────────────────────────────────────────────────────────


class PullRequestService:
    """Create a GitHub PR from a conversation's working directory."""

    def __init__(
        self,
        store: PullRequestStore,
        *,
        git: CommandRunner,
        gh: CommandRunner,
        base_branch: str = DEFAULT_BASE_BRANCH,
    ) -> None:
        self._store = store
        self._git = git
        self._gh = gh
        self._base_branch = base_branch

    def new_branch_name(self, title: str) -> str:
        """Build a unique ``feature/<slug>-<hex4>`` branch name for a title."""
        return f"feature/{sanitize_branch_name(title)}-{os.urandom(2).hex()}"

    async def create(
        self,
        conversation_key: ConversationKey,
        title: str,
        *,
        branch_name: str | None = None,
    ) -> PullRequestResult:
        """Run the five-stage pipeline and return the created PR.

        Raises:
            NothingToCommitError: the working tree was clean (already returned
                to the base branch).
            PullRequestError: a stage failed; ``stage``/``stderr`` identify it.
        """
        workdir = str(
            await self._store.get_chat_setting(conversation_key, WORKDIR_SETTING, os.getcwd())
        )
        branch = branch_name or self.new_branch_name(title)
        body = self._consume_changelog(workdir)

        result = await self._git.run(workdir, "checkout", "-b", branch)
        if result.returncode != 0:
            raise PullRequestError("branch", result.stderr)

        result = await self._git.run(workdir, "add", ".")
        if result.returncode != 0:
            raise PullRequestError("add", result.stderr)

        result = await self._git.run(workdir, "commit", "-m", title)
        if result.returncode != 0:
            if "nothing to commit" in (result.stdout + result.stderr).lower():
                await self._git.run(workdir, "checkout", self._base_branch)
                raise NothingToCommitError(result.stdout + result.stderr)
            raise PullRequestError("commit", result.stderr)

        result = await self._git.run(workdir, "push", "-u", "origin", branch)
        if result.returncode != 0:
            raise PullRequestError("push", result.stderr)

        result = await self._gh.run(
            workdir,
            "pr",
            "create",
            "--title",
            title,
            "--body",
            body,
            "--base",
            self._base_branch,
            "--head",
            branch,
        )
        if result.returncode != 0:
            raise PullRequestError("pr", result.stderr)

        url = self._extract_pr_url(result.stdout)

        # Best-effort return to the base branch (the original ignored failures).
        await self._git.run(workdir, "checkout", self._base_branch)

        return PullRequestResult(branch=branch, title=title, url=url, output=result.stdout)

    @staticmethod
    def _consume_changelog(workdir: str) -> str:
        """Read ``CHANGELOG.md`` (then delete it) or return the default body."""
        changelog_path = Path(workdir) / "CHANGELOG.md"
        if changelog_path.exists():
            body = changelog_path.read_text(encoding="utf-8", errors="replace")
            changelog_path.unlink()
            return body
        return DEFAULT_BODY

    @staticmethod
    def _extract_pr_url(output: str) -> str:
        """Extract the ``github.com/.../pull/...`` URL from ``gh`` stdout."""
        for line in output.split("\n"):
            if "github.com" in line and "/pull/" in line:
                return line.strip()
        return ""
