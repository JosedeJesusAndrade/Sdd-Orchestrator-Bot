"""Telegram implementation of the ``FrontendGate`` policy.

The allow-list is the SAME ``ALLOWED_CHAT_IDS`` the bot already trusts; this
class translates an opaque ``telegram:{chat_id}`` key back to its integer chat
id and compares against it. ``@authorized`` delegates to ``is_allowed`` so the
decorator and the gate can never disagree.
"""

from __future__ import annotations

from collections.abc import Collection

from frontends.telegram.keys import PREFIX
from services.conversation import ConversationKey, split_conversation_key


class TelegramGate:
    """Allow only conversations whose chat id is in the configured list."""

    def __init__(self, allowed_chat_ids: Collection[int]) -> None:
        self._allowed = frozenset(allowed_chat_ids)

    def is_allowed(self, conversation_id: ConversationKey) -> bool:
        prefix, local = split_conversation_key(conversation_id)
        if prefix != PREFIX:
            return False
        try:
            chat_id = int(local)
        except ValueError:
            return False
        return chat_id in self._allowed
