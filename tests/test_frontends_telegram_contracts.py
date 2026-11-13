"""The Telegram frontend adapters satisfy the core Protocols.

This is the seam proof for the ``core-isolation`` item 1 move: ``services``
defines the contracts (``ChatView`` / ``ProgressHandle``) and
``frontends.telegram`` provides concrete adapters that mypy accepts as
implementations and that pass the runtime-checkable ``isinstance`` checks.
"""

from __future__ import annotations

from frontends.telegram.bot_port import MessageInfo
from frontends.telegram.chat_view import TelegramChatView
from services.chat_view import ChatView, ProgressHandle


class _FakeSender:
    """Minimal structural ``MessageSenderPort`` used only to build the view."""

    async def send_formatted(self, chat_id: int, text: str) -> list[MessageInfo]:
        return [MessageInfo(chat_id=chat_id, message_id=1, text=text)]

    async def send_plain(self, chat_id: int, text: str) -> MessageInfo | None:
        return MessageInfo(chat_id=chat_id, message_id=1, text=text)

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> MessageInfo | None:
        return MessageInfo(chat_id=chat_id, message_id=message_id, text=text)


def test_telegram_chat_view_implements_chat_view() -> None:
    view = TelegramChatView(_FakeSender(), 1)

    assert isinstance(view, ChatView)


async def test_telegram_progress_handle_implements_progress_handle() -> None:
    view = TelegramChatView(_FakeSender(), 1)

    handle = await view.start_progress()

    assert isinstance(handle, ProgressHandle)
