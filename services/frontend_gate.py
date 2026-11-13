"""FrontendGate — frontend-agnostic "who is allowed" seam (Fase C).

WHY THIS EXISTS (do not delete it as "unused"):
    Telegram gates every handler with ``@authorized`` in ``handlers/__init__.py``.
    That decorator does three things a second frontend (Flet) also needs:
    an allow-list check, a fresh ``new_request_id()``, and entry/exit logging.
    But ``@authorized`` is coupled to PTB's ``Update`` and stays untouched as
    the TELEGRAM implementation.

    This module is the seam where a Flet-specific gate plugs in. It is
    deliberately tiny and PTB-free so the Flet entry point can depend on it
    without importing Telegram. ``new_request_id()`` is already
    frontend-agnostic (contextvars) and is reused as-is; only the policy
    ("who is allowed") lives here. Real auth (PIN/token) is deferred; a real
    gate can be swapped in later without touching the call sites.

    ``AllowAllGate`` is the v1 policy: a local single-user app where access is
    physical access to the device.

    ``authorize_request`` / ``finish_request`` are the ONE shared path both
    entry points use: a fresh ``new_request_id()``, the ``handler_entry`` /
    ``unauthorized`` / ``handler_exit`` events, and the allow decision. Telegram
    (via ``@authorized``) and Flet (via ``on_send``) therefore log identically.

This module imports only the standard library and ``services.chat_view``
(itself PTB-free). Importing it must NOT put ``telegram`` into ``sys.modules``.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable

from services.conversation import ConversationKey
from utils.logging import get_module_logger, mask_conversation_key, new_request_id

logger = get_module_logger(__name__)


@runtime_checkable
class FrontendGate(Protocol):
    """The 'who is allowed' policy, independent of the frontend."""

    def is_allowed(self, conversation_id: ConversationKey) -> bool:
        """Return True when this conversation may use the core."""
        ...


class AllowAllGate:
    """v1: access == physical access to the device (single-user local app)."""

    def is_allowed(self, conversation_id: ConversationKey) -> bool:
        return True


def authorize_request(
    gate: FrontendGate,
    conversation_id: ConversationKey,
    handler: str,
) -> bool:
    """Start a request, log its entry, and apply the gate policy.

    Shared by ``@authorized`` (Telegram) and the Flet ``on_send`` path so the
    request id and the structured events cannot drift between frontends.

    Returns ``True`` when the conversation may proceed. A denied request logs
    ``unauthorized`` and returns ``False``.
    """
    new_request_id()
    masked = mask_conversation_key(conversation_id)

    logger.debug(
        "Handler entry",
        extra={
            "event": "handler_entry",
            "handler": handler,
            "chat_id_masked": masked,
        },
    )

    if not gate.is_allowed(conversation_id):
        logger.warning(
            "Unauthorized access",
            extra={
                "event": "unauthorized",
                "handler": handler,
                "chat_id_masked": masked,
            },
        )
        return False

    return True


def finish_request(
    handler: str,
    conversation_id: ConversationKey,
    started: float,
) -> None:
    """Log the matching ``handler_exit`` event with the elapsed duration."""
    elapsed_ms = round((time.monotonic() - started) * 1000, 2)
    logger.debug(
        "Handler exit",
        extra={
            "event": "handler_exit",
            "handler": handler,
            "chat_id_masked": mask_conversation_key(conversation_id),
            "duration_ms": elapsed_ms,
        },
    )
