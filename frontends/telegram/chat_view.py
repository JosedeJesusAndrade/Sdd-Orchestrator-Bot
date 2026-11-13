"""Telegram implementation of the ChatView port.

Kept OUT of ``services.chat_view`` (the contract module) so that importing
the contract never drags in ``telegram``. This adapter is the only ChatView
aware of ``MessageSender`` / Telegram ``message_id``.

The Bot-API chunk size is read from ``telegram_config`` (the Telegram layer),
never from the core.
"""

from __future__ import annotations

from frontends.telegram.bot_port import MessageInfo
from frontends.telegram.formatting import split_message
from frontends.telegram.message_sender import MessageSenderPort
from frontends.telegram.strings import TELEGRAM_STRINGS
from services.chat_view import ProgressHandle
from telegram_config import TELEGRAM_MAX_MESSAGE_LENGTH
from utils.logging import get_module_logger

logger = get_module_logger(__name__)


class _TelegramProgressHandle:
    """ProgressHandle backed by MessageSender.edit_message.

    Encapsulates the Telegram ``message_id`` so ``PromptService`` never
    sees it.
    """

    def __init__(
        self,
        sender: MessageSenderPort,
        conversation_id: int,
        message: MessageInfo | None,
    ) -> None:
        self._sender = sender
        self._conversation_id = conversation_id
        self._message_id = message.message_id if message is not None else None

    async def _edit(self, text: str) -> None:
        if self._message_id is None:
            return
        await self._sender.edit_message(
            self._conversation_id,
            self._message_id,
            text,
        )

    async def update(self, text: str) -> None:
        await self._edit(text)

    async def finish(self, text: str) -> None:
        await self._edit(text)

    async def error(self, text: str) -> None:
        await self._edit(text)


class TelegramChatView:
    """ChatView implemented with the existing Telegram MessageSender.

    Constructed per conversation so a single instance never mixes chats.
    Delivery/formatting (MDV2 fallback, splitting, edit fallback) is
    delegated to MessageSender — no logic is re-implemented here.
    """

    source_label = "\U0001f4f1 Telegram"  # "📱 Telegram"
    strings = TELEGRAM_STRINGS

    def __init__(
        self,
        sender: MessageSenderPort,
        conversation_id: int,
        *,
        max_message_length: int = TELEGRAM_MAX_MESSAGE_LENGTH,
    ) -> None:
        self._sender = sender
        self._conversation_id = conversation_id
        self._max_message_length = max_message_length

    async def send(self, text: str) -> bool:
        """Deliver ``text``; True only if the WHOLE message was delivered.

        F8: previously this returned ``bool(messages)`` — True if even ONE
        fragment landed — so a 1/5 delivery was reported as a success and the
        UI showed "✅ Completado." for a mostly-missing response. Now a partial
        delivery is a failure for status purposes.
        """
        fragments = split_message(text, self._max_message_length)
        messages = await self._sender.send_formatted(self._conversation_id, text)
        return len(messages) == len(fragments)

    async def start_progress(self) -> ProgressHandle:
        message = await self._sender.send_plain(self._conversation_id, self.strings.processing)
        if message is None:
            # The placeholder never reached Telegram, so this handle is a
            # no-op and the user will see NO progress UI at all. MessageSender
            # already logs the transport failure (send_plain_error); this
            # distinct event tells the operator the progress surface is dark,
            # not just that one message failed. WARNING (not an exception):
            # the prompt itself still runs and its final response still lands.
            logger.warning(
                "Progress placeholder unavailable",
                extra={
                    "event": "progress_placeholder_unavailable",
                    "chat_id": self._conversation_id,
                },
            )
        return _TelegramProgressHandle(self._sender, self._conversation_id, message)
