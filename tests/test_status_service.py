"""Unit tests for StatusService (Fase B + flet-data-layer P5).

Covers the observable snapshot contract: an active vs. no session, uptime,
the timestamp fallback paths preserved byte-for-byte from the old handler,
the async git-branch lookup (success, non-zero, spawn error, timeout and
event-loop responsiveness), and the ST-02 fields (``workdir``/``provider``/
``session_count``).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import pytest

from services.effective_workdir import WorkdirResolution, WorkdirSource
from services.session_store import SessionInfo, SessionStore
from services.status_service import StatusService, StatusSnapshot
from tests.conversation_keys import conv

_CREATED = "2026-05-19T00:00:00+00:00"
_LAST_USED = "2026-05-19T01:00:00+00:00"


class FakeStore:
    def __init__(
        self,
        session: SessionInfo | None = None,
        *,
        model: str = "test/model",
        settings: dict[str, Any] | None = None,
    ) -> None:
        self._session = session
        self._model = model
        self._settings = settings or {}

    async def get_active_session(self, conversation_id: object) -> SessionInfo | None:
        return self._session

    async def get_model(self, conversation_id: object) -> str:
        return self._model

    async def get_all_chat_settings(self, conversation_id: object) -> dict[str, Any]:
        return dict(self._settings)


class FakeResolver:
    """Minimal ``EffectiveWorkdir`` double returning a fixed path."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self.calls: list[object] = []

    async def resolve(self, conversation_id: object) -> WorkdirResolution:
        self.calls.append(conversation_id)
        return WorkdirResolution(
            path=self._path,
            source=WorkdirSource.SETTING,
            mismatch=False,
        )


class FakeAgentSessions:
    """Count-only double; records every directory it was asked about."""

    def __init__(self, count: int = 0, *, error: Exception | None = None) -> None:
        self._count = count
        self._error = error
        self.calls: list[str | None] = []

    async def count_sessions(self, directory: str | None = None) -> int:
        self.calls.append(directory)
        if self._error is not None:
            raise self._error
        return self._count


class FakeProcess:
    """Minimal ``asyncio.subprocess.Process`` double."""

    def __init__(
        self,
        stdout: bytes = b"main\n",
        returncode: int = 0,
        *,
        delay: float = 0.0,
    ) -> None:
        self._stdout = stdout
        self.returncode = returncode
        self._delay = delay
        self.killed = False
        self.waited = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._stdout, b""

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.waited = True
        return self.returncode


def _spawn(
    process: FakeProcess | None = None,
    *,
    error: Exception | None = None,
) -> tuple[Any, list[tuple[tuple[str, ...], dict[str, Any]]]]:
    calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    async def fake_spawn(*args: str, **kwargs: Any) -> FakeProcess:
        calls.append((args, kwargs))
        if error is not None:
            raise error
        assert process is not None
        return process

    return fake_spawn, calls


class RecordingLogger:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def warning(self, message: str, *, extra: dict[str, Any] | None = None, **_: Any) -> None:
        self.events.append(dict(extra or {}))

    def debug(self, message: str, *, extra: dict[str, Any] | None = None, **_: Any) -> None:
        self.events.append(dict(extra or {}))

    def info(self, message: str, **_: Any) -> None:  # pragma: no cover - defensive
        pass


def _session(
    *,
    name: str = "work",
    real_id: str | None = "ses_123",
    created: str = _CREATED,
    last_used: str | None = _LAST_USED,
    prompt_count: int = 7,
) -> SessionInfo:
    return SessionInfo(
        name=name,
        real_id=real_id,
        title=name,
        created=created,
        last_used=last_used,
        prompt_count=prompt_count,
        is_active=True,
    )


def _service(
    session: SessionInfo | None,
    *,
    settings: dict[str, Any] | None = None,
    start_time: float | None = None,
    resolver: FakeResolver | None = None,
    agent_sessions: FakeAgentSessions | None = None,
) -> StatusService:
    return StatusService(
        cast(SessionStore, FakeStore(session, model="test/model", settings=settings or {})),
        start_time=start_time,
        effective_workdir=cast(Any, resolver),
        agent_sessions=cast(Any, agent_sessions),
    )


async def _snapshot(
    session: SessionInfo | None,
    *,
    settings: dict[str, Any] | None = None,
    start_time: float | None = None,
    resolver: FakeResolver | None = None,
    agent_sessions: FakeAgentSessions | None = None,
) -> StatusSnapshot:
    service = _service(
        session,
        settings=settings,
        start_time=start_time,
        resolver=resolver,
        agent_sessions=agent_sessions,
    )
    return await service.snapshot(conv(42))


# ── Active session / no session ────────────────────────────────────────


async def test_snapshot_with_active_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("services.status_service.time.time", lambda: 1000.0)
    fake_spawn, calls = _spawn(FakeProcess(stdout=b"main\n"))
    monkeypatch.setattr("services.status_service.asyncio.create_subprocess_exec", fake_spawn)
    counts = FakeAgentSessions(3)

    service = StatusService(
        cast(
            SessionStore,
            FakeStore(
                _session(),
                model="deepseek/deepseek-v4-pro",
                # The legacy setting differs: the resolver must win (WD-02).
                settings={"workdir": "C:/legacy", "provider": "anthropic"},
            ),
        ),
        start_time=940.0,
        effective_workdir=cast(Any, FakeResolver("C:/repo")),
        agent_sessions=cast(Any, counts),
    )

    snapshot = await service.snapshot(conv(42))

    assert isinstance(snapshot, StatusSnapshot)
    assert snapshot.session_name == "work"
    assert snapshot.session_real_id == "ses_123"
    assert snapshot.model == "deepseek/deepseek-v4-pro"
    assert snapshot.branch == "main"
    assert snapshot.workdir == str(Path("C:/repo"))
    assert snapshot.provider == "anthropic"
    assert snapshot.session_count == 3
    assert snapshot.created == datetime.fromisoformat(_CREATED)
    assert snapshot.last_used == datetime.fromisoformat(_LAST_USED)
    assert snapshot.created_fallback is None
    assert snapshot.last_used_fallback is None
    assert snapshot.prompt_count == 7
    assert snapshot.uptime_seconds == 60
    assert counts.calls == [str(Path("C:/repo"))]
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == ("git", "rev-parse", "--abbrev-ref", "HEAD")
    assert kwargs["cwd"] == str(Path("C:/repo"))


async def test_snapshot_without_active_session_uses_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("services.status_service.time.time", lambda: 1000.0)

    snapshot = await _snapshot(None, settings={}, start_time=None)

    assert snapshot.session_name is None
    assert snapshot.session_real_id is None
    assert snapshot.model == "test/model"
    assert snapshot.branch == "?"
    assert snapshot.workdir == ""
    assert snapshot.provider == "opencode"
    assert snapshot.session_count == 0
    assert snapshot.created is None
    assert snapshot.last_used is None
    assert snapshot.created_fallback is None
    assert snapshot.last_used_fallback is None
    assert snapshot.prompt_count == 0
    assert snapshot.uptime_seconds is None


async def test_git_cwd_comes_from_the_resolver_not_the_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_spawn, calls = _spawn(FakeProcess(stdout=b"dev\n"))
    monkeypatch.setattr("services.status_service.asyncio.create_subprocess_exec", fake_spawn)

    snapshot = await _snapshot(
        _session(),
        settings={"workdir": "C:/legacy"},
        resolver=FakeResolver("C:/resolved"),
    )

    assert snapshot.branch == "dev"
    assert [kwargs["cwd"] for _args, kwargs in calls] == [str(Path("C:/resolved"))]
    assert snapshot.workdir == str(Path("C:/resolved"))


# ── ST-02 fields ───────────────────────────────────────────────────────


async def test_provider_defaults_to_opencode_when_absent() -> None:
    snapshot = await _snapshot(_session(), settings={})

    assert snapshot.provider == "opencode"


async def test_provider_comes_from_the_effective_settings() -> None:
    snapshot = await _snapshot(_session(), settings={"provider": "anthropic"})

    assert snapshot.provider == "anthropic"


async def test_session_count_is_exact_and_uncapped(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_spawn, _calls = _spawn(FakeProcess(stdout=b"main\n"))
    monkeypatch.setattr("services.status_service.asyncio.create_subprocess_exec", fake_spawn)
    counts = FakeAgentSessions(120)

    snapshot = await _snapshot(
        _session(),
        resolver=FakeResolver("C:/repo"),
        agent_sessions=counts,
    )

    assert snapshot.session_count == 120
    assert counts.calls == [str(Path("C:/repo"))]


async def test_session_count_failure_degrades_to_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_spawn, _calls = _spawn(FakeProcess(stdout=b"main\n"))
    monkeypatch.setattr("services.status_service.asyncio.create_subprocess_exec", fake_spawn)
    events: list[str] = []
    monkeypatch.setattr(
        "services.status_service.log_exception",
        lambda event, **_: events.append(event),
    )

    snapshot = await _snapshot(
        _session(),
        resolver=FakeResolver("C:/repo"),
        agent_sessions=FakeAgentSessions(error=RuntimeError("db down")),
    )

    assert snapshot.session_count == 0
    assert events == ["status_session_count_failed"]


async def test_count_is_not_requested_without_a_workdir() -> None:
    counts = FakeAgentSessions(5)

    snapshot = await _snapshot(_session(), settings={}, agent_sessions=counts)

    assert snapshot.session_count == 0
    assert counts.calls == []


async def test_snapshot_is_computed_on_demand_only(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_spawn, _calls = _spawn(FakeProcess(stdout=b"main\n"))
    monkeypatch.setattr("services.status_service.asyncio.create_subprocess_exec", fake_spawn)
    counts = FakeAgentSessions(1)

    service = _service(_session(), resolver=FakeResolver("C:/repo"), agent_sessions=counts)
    assert counts.calls == []

    await service.snapshot(conv(42))

    assert counts.calls == [str(Path("C:/repo"))]


# ── Timestamp fallbacks (byte-for-byte old-handler behavior) ───────────


async def test_malformed_timestamps_fall_back_to_raw_strings() -> None:
    snapshot = await _snapshot(_session(created="not-a-date", last_used="also-bad"))

    assert snapshot.created is None
    assert snapshot.last_used is None
    assert snapshot.created_fallback == "not-a-date"
    assert snapshot.last_used_fallback == "also-bad"


async def test_naive_timestamps_fall_back_to_raw_strings() -> None:
    snapshot = await _snapshot(
        _session(
            created="2026-05-19T00:00:00",
            last_used="2026-05-19T01:00:00",
        )
    )

    assert snapshot.created is None
    assert snapshot.last_used is None
    assert snapshot.created_fallback == "2026-05-19T00:00:00"
    assert snapshot.last_used_fallback == "2026-05-19T01:00:00"


async def test_aware_created_with_malformed_last_used_keeps_paths_separate() -> None:
    snapshot = await _snapshot(_session(created=_CREATED, last_used="garbage"))

    assert snapshot.created == datetime.fromisoformat(_CREATED)
    assert snapshot.created_fallback is None
    assert snapshot.last_used is None
    assert snapshot.last_used_fallback == "garbage"


async def test_empty_and_missing_timestamps_have_no_fallback() -> None:
    snapshot = await _snapshot(_session(created="", last_used=None))

    assert snapshot.created is None
    assert snapshot.last_used is None
    assert snapshot.created_fallback is None
    assert snapshot.last_used_fallback is None


# ── Git branch lookup (async, ST-01) ───────────────────────────────────


async def test_branch_falls_back_when_git_returns_non_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_spawn, _calls = _spawn(FakeProcess(stdout=b"", returncode=1))
    monkeypatch.setattr("services.status_service.asyncio.create_subprocess_exec", fake_spawn)
    recording = RecordingLogger()
    monkeypatch.setattr("services.status_service.logger", recording)

    snapshot = await _snapshot(_session(), resolver=FakeResolver("C:/repo"))

    assert snapshot.branch == "?"
    assert any(event.get("event") == "git_branch_lookup_failed" for event in recording.events)


async def test_branch_falls_back_when_git_cannot_be_spawned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_spawn, _calls = _spawn(error=OSError("git not found"))
    monkeypatch.setattr("services.status_service.asyncio.create_subprocess_exec", fake_spawn)
    events: list[str] = []
    monkeypatch.setattr(
        "services.status_service.log_exception",
        lambda event, **_: events.append(event),
    )

    snapshot = await _snapshot(_session(), resolver=FakeResolver("C:/repo"))

    assert snapshot.branch == "?"
    assert events == ["git_branch_lookup_failed"]


async def test_git_timeout_degrades_and_logs_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("services.status_service.GIT_TIMEOUT_SECONDS", 0.01)
    slow = FakeProcess(delay=10.0)
    fake_spawn, _calls = _spawn(slow)
    monkeypatch.setattr("services.status_service.asyncio.create_subprocess_exec", fake_spawn)
    recording = RecordingLogger()
    monkeypatch.setattr("services.status_service.logger", recording)

    snapshot = await _snapshot(_session(), resolver=FakeResolver("C:/repo"))

    assert snapshot.branch == "?"
    assert any(event.get("event") == "git_branch_timeout" for event in recording.events)
    assert slow.killed is True
    assert slow.waited is True


async def test_slow_git_does_not_block_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A git that takes 0.2 s: the loop must keep scheduling another task.
    monkeypatch.setattr("services.status_service.GIT_TIMEOUT_SECONDS", 10.0)
    fake_spawn, _calls = _spawn(FakeProcess(stdout=b"main\n", delay=0.2))
    monkeypatch.setattr("services.status_service.asyncio.create_subprocess_exec", fake_spawn)

    service = _service(_session(), resolver=FakeResolver("C:/repo"))

    ticks: list[int] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        while not stop.is_set():
            ticks.append(1)
            await asyncio.sleep(0.01)

    heartbeat_task = asyncio.create_task(heartbeat())
    snapshot_task = asyncio.create_task(service.snapshot(conv(42)))

    await asyncio.sleep(0.05)
    ticks_while_git_pending = len(ticks)

    snapshot = await snapshot_task
    stop.set()
    await heartbeat_task

    assert ticks_while_git_pending > 0
    assert snapshot.branch == "main"


async def test_branch_is_unknown_without_workdir_and_skips_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_spawn, calls = _spawn(FakeProcess(stdout=b"main\n"))
    monkeypatch.setattr("services.status_service.asyncio.create_subprocess_exec", fake_spawn)

    snapshot = await _snapshot(_session(), settings={})

    assert snapshot.branch == "?"
    assert calls == []
