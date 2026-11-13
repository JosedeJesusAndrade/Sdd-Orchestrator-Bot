"""Item 5 proof: ``@authorized`` delegates to the gate's ``is_allowed``.

The decorator no longer compares chat ids itself; it resolves the gate from
``bot_data[GATE_KEY]`` and calls the shared ``authorize_request`` helper. These
tests drive a real decorated handler with no PTB network.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, cast

import pytest
from telegram import Update
from telegram.ext import ContextTypes

from frontends.telegram.gate import TelegramGate
from handlers import authorized
from telegram_config import GATE_KEY


class _SpyGate:
    """Gate double that records every is_allowed call."""

    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.calls: list[str] = []

    def is_allowed(self, conversation_id: Any) -> bool:
        self.calls.append(str(conversation_id))
        return self.allowed


def _update(chat_id: int) -> Update:
    return cast(Update, SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id)))


def _context(gate: object | None) -> ContextTypes.DEFAULT_TYPE:
    bot_data: dict[str, object] = {}
    if gate is not None:
        bot_data[GATE_KEY] = gate
    return cast(
        ContextTypes.DEFAULT_TYPE,
        SimpleNamespace(application=SimpleNamespace(bot_data=bot_data)),
    )


async def test_allowed_handler_runs_and_gate_is_the_source_of_truth() -> None:
    calls: list[int] = []

    @authorized
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        calls.append(cast(Any, update).effective_chat.id)

    gate = _SpyGate(allowed=True)
    await handler(_update(123), _context(gate))

    assert calls == [123]
    assert gate.calls == ["telegram:123"]


async def test_denied_handler_does_not_run() -> None:
    calls: list[int] = []

    @authorized
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        calls.append(cast(Any, update).effective_chat.id)

    await handler(_update(999), _context(TelegramGate([123])))

    assert calls == []


async def test_real_telegram_gate_allows_configured_chat() -> None:
    calls: list[int] = []

    @authorized
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        calls.append(cast(Any, update).effective_chat.id)

    await handler(_update(123), _context(TelegramGate([123])))

    assert calls == [123]


async def test_missing_gate_is_logged_and_skips_handler(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[int] = []

    @authorized
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        calls.append(1)

    with caplog.at_level(logging.ERROR, logger="opencode_bot.handlers"):
        await handler(_update(123), _context(None))

    assert calls == []
    assert any(r.__dict__.get("event") == "authorized_missing_gate" for r in caplog.records)


async def test_missing_update_is_logged_and_skips(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[int] = []

    @authorized
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        calls.append(1)

    with caplog.at_level(logging.ERROR, logger="opencode_bot.handlers"):
        await handler(cast(Update, None), _context(TelegramGate([123])))

    assert calls == []
    assert any(r.__dict__.get("event") == "authorized_missing_update" for r in caplog.records)
