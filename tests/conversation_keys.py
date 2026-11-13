"""Test helpers for opaque conversation keys.

Tests exercise services/stores directly; this wraps a legacy int/str value into
a ``ConversationKey`` without forcing every fixture to know the namespace.
"""

from __future__ import annotations

from services.conversation import ConversationKey


def conv(value: int | str) -> ConversationKey:
    """Coerce a test value into a ``ConversationKey``."""
    return ConversationKey(str(value))
