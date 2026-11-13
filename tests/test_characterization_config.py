"""Characterization tests for ``/config`` (``handlers/commands.py``).

Pins key parsing (lowercased) and the mapping from ``SettingsService`` errors
to user messages, including the ``timeout`` bounds (1..86400). The validation
itself lives in ``SettingsService``; these tests drive it through the real
service wired to an in-memory store.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from frontends.telegram.keys import telegram_key
from handlers.commands import config_command
from locales import get_strings
from services.session_store import SessionStore
from services.settings_service import SettingsService
from tests.handler_fakes import CHAT_ID, FakeMessage, make_context, make_update


class _FakeSettingsStore:
    """In-memory SessionStore double for settings only."""

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        self.settings = dict(settings or {})
        self.set_calls: list[tuple[Any, str, Any]] = []

    async def get_all_chat_settings(self, conversation_id: Any) -> dict[str, Any]:
        return dict(self.settings)

    async def get_chat_setting(self, conversation_id: Any, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    async def set_chat_setting(self, conversation_id: Any, key: str, value: Any) -> None:
        self.settings[key] = value
        self.set_calls.append((conversation_id, key, value))


async def _run(
    msg: FakeMessage,
    store: _FakeSettingsStore,
    args: list[str] | None,
) -> None:
    service = SettingsService(cast(SessionStore, store))
    container = SimpleNamespace(settings_service=service)
    await config_command(make_update(msg), make_context(container, args=args))


# ── Listing / reading ───────────────────────────────────────────────────


async def test_no_args_lists_effective_settings() -> None:
    store = _FakeSettingsStore({"model": "m/x", "timeout": 42})
    msg = FakeMessage()

    await _run(msg, store, None)

    text = msg.reply_texts[-1]
    assert "modelo: `m/x`" in text
    assert "timeout: `42s`" in text
    assert "provider: `opencode`" in text
    assert "workdir:" in text


async def test_single_key_reads_the_current_value() -> None:
    store = _FakeSettingsStore({"timeout": 12})
    msg = FakeMessage()

    await _run(msg, store, ["timeout"])

    assert msg.reply_texts[-1] == "timeout = `12`"
    assert store.set_calls == []


# ── Writing ─────────────────────────────────────────────────────────────


async def test_sets_timeout_with_specific_confirmation() -> None:
    store = _FakeSettingsStore()
    msg = FakeMessage()

    await _run(msg, store, ["timeout", "500"])

    assert store.settings["timeout"] == 500
    assert "timeout → 500s" in msg.reply_texts[-1]


async def test_key_is_lowercased_before_lookup() -> None:
    store = _FakeSettingsStore()
    msg = FakeMessage()

    await _run(msg, store, ["MODEL", "pro"])

    assert store.settings["model"] == "deepseek/deepseek-v4-pro"
    assert "model → `deepseek/deepseek-v4-pro`" in msg.reply_texts[-1]


async def test_sets_another_known_key_with_generic_confirmation() -> None:
    store = _FakeSettingsStore()
    msg = FakeMessage()

    await _run(msg, store, ["workdir", "C:/project"])

    assert store.settings["workdir"] == "C:/project"
    assert "workdir → `C:/project`" in msg.reply_texts[-1]


@pytest.mark.parametrize("raw", ["1", "86400"])
async def test_accepts_timeout_at_the_bounds(raw: str) -> None:
    store = _FakeSettingsStore()
    msg = FakeMessage()

    await _run(msg, store, ["timeout", raw])

    assert store.settings["timeout"] == int(raw)
    assert store.set_calls == [(telegram_key(CHAT_ID), "timeout", int(raw))]


@pytest.mark.parametrize("raw", ["0", "-5", "abc", "12.5", "86401", "999999"])
async def test_invalid_timeout_reports_and_does_not_persist(raw: str) -> None:
    store = _FakeSettingsStore()
    msg = FakeMessage()

    await _run(msg, store, ["timeout", raw])

    assert msg.reply_texts[-1] == get_strings().CONFIG_INVALID_TIMEOUT
    assert store.set_calls == []
    assert "timeout" not in store.settings


async def test_unknown_key_reports_and_does_not_persist() -> None:
    store = _FakeSettingsStore()
    msg = FakeMessage()

    await _run(msg, store, ["theme", "dark"])

    assert get_strings().CONFIG_UNKNOWN_KEY.format(key="theme") == msg.reply_texts[-1]
    assert store.set_calls == []
