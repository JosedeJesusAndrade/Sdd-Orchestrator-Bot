"""ChatView — frontend-agnostic port for conversation delivery.

ETC seam: ``PromptService`` depends on this abstraction, never on Telegram.
Telegram is ONE implementation (``TelegramChatView`` in
``frontends.telegram.chat_view``); a future Flet frontend can implement the
same Protocol without touching the core.

This module is deliberately PTB-free: it imports only the standard library.
Importing it must NOT put ``telegram`` into ``sys.modules`` — the concrete
Telegram adapter lives in ``frontends.telegram.chat_view``.

The progress handle is intentionally OPAQUE: the core only knows
"update/finish/error in place" — it never sees a frontend-specific
``message_id`` or chat-edit operation.

Contract:
    ChatView.source_label -> str
        Frontend identity prepended to the engine prompt (e.g. "📱 Telegram").
    ChatView.strings -> CoreStrings
        Frontend-owned UI copy. The core formats these templates but never
        decides a single user-facing word or emoji (item 4).
    ChatView.send(text) -> bool
        Deliver a final message; True only if the WHOLE message reached the
        user (every fragment). Partial delivery is reported as False (F8).
    ChatView.start_progress() -> ProgressHandle
        Open an in-place status placeholder (the view picks its own text) and
        return its handle.

Future evolution (option b — NOT implemented): if a frontend needs to render
"⚠️ Entregado parcialmente 4/5" it must know the counts, which a boolean
erases. The transport already computes both (``MessageSender.send_formatted``
logs ``sent``/``total``); the natural upgrade is to change this port to
``send(text) -> SendResult(sent: int, total: int)`` and have the core
translate it into a user-facing partial message. Deferred until the Flet
frontend actually needs per-fragment status; today the boolean is enough.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from services.conversation import ConversationKey

# Re-exported for callers that think in terms of the ChatView seam; the
# canonical definition (and helpers) live in ``services.conversation``.
__all__ = ["ChatView", "ConversationKey", "CoreStrings", "ProgressHandle"]


@dataclass(frozen=True)
class CoreStrings:
    """Frontend-owned UI copy that the core formats but never decides.

    The core reads this frozen bundle from the active ``ChatView`` and only
    substitutes data into the templates. The actual values live in each
    frontend's strings module (built from ``locales``); ``PromptService``
    contains no emoji or sentence literals.

    Templates use ``str.format``:
      - ``processing_elapsed``: ``{elapsed}``
      - ``error``: ``{error}``
      - ``error_code`` / ``error_code_no_output``: ``{code}``
      - ``new_session``: ``{name}``
    """

    processing: str
    processing_elapsed: str
    cancelled: str
    completed: str
    completed_no_output: str
    error: str
    error_code: str
    error_code_no_output: str
    delivery_failed: str
    permission_not_accepted: str
    permission_not_accepted_notice: str
    new_session: str


@runtime_checkable
class ProgressHandle(Protocol):
    """Opaque handle for an in-place progress message."""

    async def update(self, text: str) -> None:
        """Progress tick — replace the placeholder text."""
        ...

    async def finish(self, text: str) -> None:
        """Success completion — replace the placeholder text."""
        ...

    async def error(self, text: str) -> None:
        """Failure/cancel completion — replace the placeholder text."""
        ...


@runtime_checkable
class ChatView(Protocol):
    """A frontend conversation surface."""

    source_label: str
    strings: CoreStrings

    async def send(self, text: str) -> bool:
        """Deliver a fire-and-forget final message.

        Returns True if the message was delivered.
        """
        ...

    async def start_progress(self) -> ProgressHandle:
        """Open an in-place progress placeholder and return its handle.

        The view decides the placeholder text (``strings.processing``).
        """
        ...
