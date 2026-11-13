"""Opaque conversation identity shared by every frontend.

The core never knows a frontend's native identifier: Telegram has an ``int``
chat id, the Flet app has a stable local name. Each frontend mints a namespaced
string key at its boundary::

    telegram:123456789
    flet:local

Every service and ``SessionStore`` receives one of these keys. ``NewType`` makes
the boundary explicit for mypy while the runtime value stays a plain ``str`` —
JSON-friendly and portable across Windows/Unix.

This module is PTB-free and Flet-free by design (stdlib only).
"""

from __future__ import annotations

from typing import NewType

#: Opaque, namespaced conversation identifier. Never a raw chat id.
ConversationKey = NewType("ConversationKey", str)

_SEPARATOR = ":"


def make_conversation_key(prefix: str, raw: str) -> ConversationKey:
    """Build a namespaced conversation key.

    Args:
        prefix: Frontend namespace (e.g. ``"telegram"``, ``"flet"``). Must be
            non-empty and must not itself contain the separator.
        raw: Frontend-local identifier (e.g. the chat id as text, or
            ``"local"``). Must be non-empty.

    Raises:
        ValueError: If ``prefix`` or ``raw`` is invalid.
    """
    if not prefix or _SEPARATOR in prefix:
        raise ValueError(f"Invalid conversation key prefix: {prefix!r}")
    if not raw:
        raise ValueError("Conversation key local part must not be empty")
    return ConversationKey(f"{prefix}{_SEPARATOR}{raw}")


def split_conversation_key(key: ConversationKey) -> tuple[str, str]:
    """Return ``(prefix, local_part)`` for a key.

    A key without a separator (legacy/un-namespaced) yields ``("", key)``.
    """
    prefix, separator, local = str(key).partition(_SEPARATOR)
    if not separator:
        return "", str(key)
    return prefix, local
