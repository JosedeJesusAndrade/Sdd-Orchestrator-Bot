"""The frontends mint the opaque key at their boundary, not the core."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from frontends.telegram.keys import telegram_key
from handlers.messages import handle_message
from tests.handler_fakes import CHAT_ID, FakeMessage, FakeSender, make_context, make_update


class _RecordingPromptService:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, str]] = []

    async def execute(self, *, conversation_id: Any, prompt_text: str, view: Any) -> None:
        self.calls.append((conversation_id, prompt_text))


async def test_telegram_text_handler_mints_the_namespaced_key() -> None:
    service = _RecordingPromptService()
    container = SimpleNamespace(prompt_service=service, message_sender=FakeSender())

    await handle_message(make_update(FakeMessage("hola")), make_context(container))

    assert service.calls == [(telegram_key(CHAT_ID), "hola")]
