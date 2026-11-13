"""Telegram frontend adapters.

Everything PTB-shaped lives here so ``services`` stays frontend-agnostic:
transport (``TelegramAdapter``), delivery/formatting (``MessageSender``), the
``ChatView`` implementation and the permission presenter.
"""

from __future__ import annotations

from frontends.telegram.adapter import TelegramAdapter
from frontends.telegram.bot_port import BotPort, MessageInfo
from frontends.telegram.chat_view import TelegramChatView
from frontends.telegram.container import AppContainer
from frontends.telegram.message_sender import MessageSender, MessageSenderPort
from frontends.telegram.permission_presenter import TelegramPermissionPresenter

__all__ = [
    "AppContainer",
    "BotPort",
    "MessageInfo",
    "MessageSender",
    "MessageSenderPort",
    "TelegramAdapter",
    "TelegramChatView",
    "TelegramPermissionPresenter",
]
