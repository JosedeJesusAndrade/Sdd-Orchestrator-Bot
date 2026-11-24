"""Tests for ``SessionSwitcher`` — the BUG #2 no-reload + history cache.

The switcher is driven with tiny structural fakes (no Flet, no services): the
sidebar records ``select`` calls and the history loader counts fetches.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

from flet_app.data.session_switch import SessionSwitcher, SwitchResult
from flet_app.data.sidebar import SidebarSnapshot
from flet_app.data.state import LoadReady
from services.agent_sessions import ConversationSummary, Message


def _summary(real_id: str, *, active: bool = False) -> ConversationSummary:
    return ConversationSummary(
        real_id=real_id,
        title=real_id,
        created_at=0,
        updated_at=0,
        message_count=0,
        directory="/w",
        agent="opencode",
        is_active=active,
    )


def _snapshot(
    active_real_id: str | None, sessions: tuple[ConversationSummary, ...] = ()
) -> SidebarSnapshot:
    return SidebarSnapshot(
        expanded=False,
        sessions=sessions,
        active_real_id=active_real_id,
        workdir="C:/proj",
        model="deepseek/deepseek-flash",
        state=LoadReady(None),
        error=None,
        profile_name="Ana",
    )


class FakeSidebar:
    def __init__(self, active_real_id: str | None, sessions: tuple[ConversationSummary, ...] = ()):
        self._snapshot = _snapshot(active_real_id, sessions)
        self.select_calls: list[str] = []

    @property
    def snapshot(self) -> SidebarSnapshot:
        return self._snapshot

    async def select(self, real_id: str) -> SidebarSnapshot:
        self.select_calls.append(real_id)
        self._snapshot = replace(self._snapshot, active_real_id=real_id)
        return self._snapshot


class FakeHistory:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, real_id: str) -> list[Message]:
        self.calls.append(real_id)
        return [Message(id=f"{real_id}-1", role="user", text="hola", timestamp=1)]


async def test_tapping_the_active_session_returns_none_and_touches_nothing() -> None:
    sidebar = FakeSidebar("ses_a")
    history = FakeHistory()
    switcher = SessionSwitcher(sidebar, history)

    result = await switcher.switch("ses_a")

    assert result is None
    assert sidebar.select_calls == []
    assert history.calls == []


async def test_switch_loads_history_and_serves_it_from_cache() -> None:
    sidebar = FakeSidebar(None)
    history = FakeHistory()
    switcher = SessionSwitcher(sidebar, history)

    first_a = await switcher.switch("ses_a")
    await switcher.switch("ses_b")
    back_to_a = await switcher.switch("ses_a")

    assert isinstance(first_a, SwitchResult)
    assert isinstance(back_to_a, SwitchResult)
    assert history.calls == ["ses_a", "ses_b"]  # the second visit hit the cache
    assert first_a.messages == back_to_a.messages
    assert [message.text for message in back_to_a.messages] == ["hola"]


async def test_switch_reports_the_updated_snapshot() -> None:
    sidebar = FakeSidebar("ses_a")
    switcher = SessionSwitcher(sidebar, FakeHistory())

    result = await switcher.switch("ses_b")

    assert result is not None
    assert result.snapshot.active_real_id == "ses_b"
    assert sidebar.select_calls == ["ses_b"]


async def test_invalidate_forces_a_reload() -> None:
    sidebar = FakeSidebar(None)
    history = FakeHistory()
    switcher = SessionSwitcher(sidebar, history)

    await switcher.switch("ses_a")
    switcher.invalidate("ses_a")
    await switcher.switch("ses_b")
    await switcher.switch("ses_a")

    assert history.calls == ["ses_a", "ses_b", "ses_a"]


async def test_invalidate_all_clears_every_entry() -> None:
    sidebar = FakeSidebar(None)
    history = FakeHistory()
    switcher = SessionSwitcher(sidebar, history)

    await switcher.switch("ses_a")
    await switcher.switch("ses_b")
    switcher.invalidate()
    await switcher.switch("ses_a")
    await switcher.switch("ses_b")

    assert history.calls == ["ses_a", "ses_b", "ses_a", "ses_b"]


async def test_cached_reports_only_cached_entries() -> None:
    switcher = SessionSwitcher(FakeSidebar(None), FakeHistory())

    await switcher.switch("ses_a")

    assert switcher.cached("ses_a") is not None
    assert switcher.cached("ses_b") is None


async def test_switching_between_sessions_is_under_200ms_with_fakes() -> None:
    """The orchestration itself adds no latency; only the (cached) history does."""

    calls: list[str] = []

    async def history(real_id: str) -> list[Any]:
        calls.append(real_id)
        return []

    switcher = SessionSwitcher(FakeSidebar("ses_a"), history)
    started = time.perf_counter()
    await switcher.switch("ses_b")
    await switcher.switch("ses_c")
    await switcher.switch("ses_b")  # cached
    elapsed = time.perf_counter() - started

    assert calls == ["ses_b", "ses_c"]
    assert elapsed < 0.2
