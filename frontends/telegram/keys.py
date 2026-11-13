"""Telegram conversation-key minting.

The Telegram frontend is the only layer that knows its native ``int`` chat id;
it converts it into the opaque ``telegram:{chat_id}`` key the core consumes.
"""

from __future__ import annotations

from services.conversation import ConversationKey, make_conversation_key

PREFIX = "telegram"


def telegram_key(chat_id: int) -> ConversationKey:
    """Mint the opaque conversation key for a Telegram chat."""
    return make_conversation_key(PREFIX, str(chat_id))
