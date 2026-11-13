"""Reusable fakes for the handler characterization tests.

These doubles let the tests drive the real Telegram handlers with NO PTB
network, NO Telegram update objects and NO live services. ``@authorized``
still runs, so ``CHAT_ID`` must be in the configured allow-list.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from config import ALLOWED_CHAT_IDS, CONTAINER_KEY

CHAT_ID = ALLOWED_CHAT_IDS[0]


class FakeMessage:
    """Message double: records replies/edits and chains like PTB messages."""

    def __init__(self, text: str | None = None) -> None:
        self.text = text
        self.voice: Any = None
        self.replies: list[tuple[str, dict[str, Any]]] = []
        self.edits: list[tuple[str, dict[str, Any]]] = []

    @property
    def reply_texts(self) -> list[str]:
        return [text for text, _ in self.replies]

    @property
    def edit_texts(self) -> list[str]:
        return [text for text, _ in self.edits]

    async def reply_text(self, text: str, **kwargs: Any) -> FakeMessage:
        self.replies.append((text, kwargs))
        return self

    async def edit_text(self, text: str, **kwargs: Any) -> FakeMessage:
        self.edits.append((text, kwargs))
        return self


class FakeSender:
    """MessageSender double recording plain and formatted deliveries."""

    def __init__(self) -> None:
        self.plain: list[tuple[int, str]] = []
        self.formatted: list[tuple[int, str]] = []

    async def send_plain(self, chat_id: int, text: str) -> SimpleNamespace:
        self.plain.append((chat_id, text))
        return SimpleNamespace(message_id=1)

    async def send_formatted(self, chat_id: int, text: str) -> list[SimpleNamespace]:
        self.formatted.append((chat_id, text))
        return [SimpleNamespace(message_id=1)]

    @property
    def plain_texts(self) -> list[str]:
        return [text for _, text in self.plain]


def make_update(msg: FakeMessage, *, chat_id: int = CHAT_ID) -> Any:
    """Build the minimal Update shape ``require_chat``/``require_message`` need."""
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        message=msg,
    )


def make_context(container: Any, *, args: list[str] | None = None) -> Any:
    """Build the minimal PTB Context shape the handlers read."""
    return SimpleNamespace(
        application=SimpleNamespace(bot_data={CONTAINER_KEY: container}),
        args=args,
        bot=SimpleNamespace(),
    )


class FakeChatSettingsStore:
    """Minimal SessionStore double exposing only chat settings."""

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        self.settings = dict(settings or {})
        self.set_calls: list[tuple[Any, str, Any]] = []

    async def get_chat_setting(self, conversation_id: Any, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    async def set_chat_setting(self, conversation_id: Any, key: str, value: Any) -> None:
        self.settings[key] = value
        self.set_calls.append((conversation_id, key, value))
