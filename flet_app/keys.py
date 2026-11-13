"""Flet conversation-key minting.

The Flet desktop app is single-user, so its identity is the STABLE ``flet:local``
key — never a random UUID. Persistence continuity across launches depends on
this key not changing.
"""

from __future__ import annotations

from services.conversation import ConversationKey, make_conversation_key

PREFIX = "flet"

#: Default local name for the single-user desktop conversation.
DEFAULT_FLET_CONVERSATION = "local"


def flet_key(name: str = DEFAULT_FLET_CONVERSATION) -> ConversationKey:
    """Mint the opaque conversation key for the Flet app."""
    return make_conversation_key(PREFIX, name)
