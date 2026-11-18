"""Unit tests for the ``EffectiveWorkdir`` resolver (WD-01 / WD-02).

Pins the precedence (raw setting -> active-session directory -> env fallback),
the single ``workdir_mismatch`` WARNING, the raw-setting read (task 1.4) and the
``fallback`` facade.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from services.agent_sessions import ConversationSummary
from services.effective_workdir import (
    EffectiveWorkdir,
    WorkdirResolution,
    WorkdirSource,
)
from services.session_store import SessionInfo
from tests.conversation_keys import conv

_FALLBACK = Path("C:/fallback")


class FakeStore:
    """Records the raw setting read; exposes one active session."""

    def __init__(
        self,
        *,
        setting: Any = None,
        active: SessionInfo | None = None,
    ) -> None:
        self._setting = setting
        self._active = active
        self.setting_calls: list[tuple[Any, str, Any]] = []

    async def get_chat_setting(self, conversation_id: Any, key: str, default: Any = None) -> Any:
        self.setting_calls.append((conversation_id, key, default))
        return self._setting if self._setting is not None else default

    async def get_active_session(self, conversation_id: Any) -> SessionInfo | None:
        return self._active


class FakeCatalog:
    """Exact-by-id catalog double; optionally raises on lookup."""

    def __init__(
        self,
        by_id: dict[str, ConversationSummary] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._by_id = by_id or {}
        self._error = error
        self.calls: list[str] = []

    async def get_session(self, real_id: str) -> ConversationSummary | None:
        self.calls.append(real_id)
        if self._error is not None:
            raise self._error
        return self._by_id.get(real_id)


def _session(real_id: str | None = "ses_active") -> SessionInfo:
    return SessionInfo(
        name="default",
        real_id=real_id,
        title="default",
        created="2026-05-19T00:00:00+00:00",
        last_used=None,
        prompt_count=0,
        is_active=True,
    )


def _summary(real_id: str, directory: str) -> ConversationSummary:
    return ConversationSummary(
        real_id=real_id,
        title="Title",
        created_at=1,
        updated_at=1,
        message_count=0,
        directory=directory,
        agent="build",
    )


def _resolver(store: FakeStore, catalog: FakeCatalog) -> EffectiveWorkdir:
    return EffectiveWorkdir(store, catalog, _FALLBACK)


def _mismatch_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record for record in caplog.records if getattr(record, "event", "") == "workdir_mismatch"
    ]


# ── Precedence ─────────────────────────────────────────────────────────


async def test_setting_wins_over_a_divergent_active_session(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FakeStore(setting="C:/wA", active=_session())
    catalog = FakeCatalog({"ses_active": _summary("ses_active", "C:/wB")})
    resolver = _resolver(store, catalog)

    with caplog.at_level(logging.WARNING, logger="opencode_bot.services.effective_workdir"):
        result = await resolver.resolve(conv(42))

    assert isinstance(result, WorkdirResolution)
    assert result.path == Path("C:/wA")
    assert result.source is WorkdirSource.SETTING
    assert result.mismatch is True
    # Exactly ONE warning per resolve.
    assert len(_mismatch_records(caplog)) == 1
    # The RAW setting is read (no default injected that would mask the levels).
    assert store.setting_calls == [(conv(42), "workdir", None)]


async def test_no_warning_when_the_setting_matches_the_active_session(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FakeStore(setting="C:/wA", active=_session())
    catalog = FakeCatalog({"ses_active": _summary("ses_active", "C:/wA")})

    with caplog.at_level(logging.WARNING, logger="opencode_bot.services.effective_workdir"):
        result = await _resolver(store, catalog).resolve(conv(42))

    assert result.path == Path("C:/wA")
    assert result.source is WorkdirSource.SETTING
    assert result.mismatch is False
    assert _mismatch_records(caplog) == []


async def test_active_session_directory_is_used_without_a_setting() -> None:
    store = FakeStore(active=_session())
    catalog = FakeCatalog({"ses_active": _summary("ses_active", "C:/wB")})

    result = await _resolver(store, catalog).resolve(conv(42))

    assert result.path == Path("C:/wB")
    assert result.source is WorkdirSource.ACTIVE_SESSION
    assert result.mismatch is False
    assert catalog.calls == ["ses_active"]


async def test_env_fallback_when_nothing_is_bound() -> None:
    store = FakeStore(active=None)
    catalog = FakeCatalog()

    result = await _resolver(store, catalog).resolve(conv(42))

    assert result.path == _FALLBACK
    assert result.source is WorkdirSource.ENV_FALLBACK
    assert result.mismatch is False


@pytest.mark.parametrize("setting", ["", "   ", "relative/dir", "../above"])
async def test_empty_or_relative_settings_are_ignored(setting: str) -> None:
    store = FakeStore(setting=setting, active=_session())
    catalog = FakeCatalog({"ses_active": _summary("ses_active", "C:/wB")})

    result = await _resolver(store, catalog).resolve(conv(42))

    assert result.path == Path("C:/wB")
    assert result.source is WorkdirSource.ACTIVE_SESSION


@pytest.mark.parametrize("setting", ["", "relative/dir"])
async def test_empty_or_relative_settings_fall_through_to_env(setting: str) -> None:
    store = FakeStore(setting=setting, active=None)
    catalog = FakeCatalog()

    result = await _resolver(store, catalog).resolve(conv(42))

    assert result.path == _FALLBACK
    assert result.source is WorkdirSource.ENV_FALLBACK


async def test_active_session_without_real_id_is_skipped() -> None:
    store = FakeStore(setting=None, active=_session(real_id=None))
    catalog = FakeCatalog()

    result = await _resolver(store, catalog).resolve(conv(42))

    assert result.path == _FALLBACK
    assert result.source is WorkdirSource.ENV_FALLBACK
    assert catalog.calls == []


async def test_active_session_unknown_to_the_catalog_is_skipped() -> None:
    store = FakeStore(active=_session())
    catalog = FakeCatalog({})

    result = await _resolver(store, catalog).resolve(conv(42))

    assert result.path == _FALLBACK
    assert result.source is WorkdirSource.ENV_FALLBACK


async def test_catalog_failure_does_not_break_resolution() -> None:
    store = FakeStore(setting="C:/wA", active=_session())
    catalog = FakeCatalog(error=RuntimeError("db down"))

    result = await _resolver(store, catalog).resolve(conv(42))

    # The setting still wins; the mismatch cannot be proven, so it is False.
    assert result.path == Path("C:/wA")
    assert result.source is WorkdirSource.SETTING
    assert result.mismatch is False


async def test_fallback_property_exposes_the_env_default() -> None:
    resolver = _resolver(FakeStore(), FakeCatalog())

    assert resolver.fallback == _FALLBACK


async def test_resolver_returns_an_absolute_normalized_path() -> None:
    store = FakeStore(setting="C:\\wA\\..\\wA", active=None)

    result = await _resolver(store, FakeCatalog()).resolve(conv(42))

    assert result.path.is_absolute()
    assert ".." not in result.path.parts
