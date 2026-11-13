"""Handler package: shared authorization decorator with structured logging.

This module now contains:
  - `@authorized` — decorator that resolves the frontend gate from
    ``bot_data``, delegates the allow-list check to ``is_allowed`` and logs
    every call with structured events (handler_entry, handler_exit,
    unauthorized)

The gate itself is ``TelegramGate`` (backed by ``ALLOWED_CHAT_IDS``); the
request id and the events come from ``services.frontend_gate``, the SAME path
the Flet entry point uses.

Session state has been moved to services/session_store.py (SessionStore).
Process tracking has been moved to services/prompt_service.py (PromptService).
"""

import functools
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, Concatenate, ParamSpec, cast

from telegram import Update
from telegram.ext import ContextTypes

from frontends.telegram.keys import telegram_key
from handlers._guards import require_chat
from services.frontend_gate import FrontendGate, authorize_request, finish_request
from telegram_config import GATE_KEY
from utils.logging import get_module_logger, new_request_id

logger = get_module_logger(__name__)

P = ParamSpec("P")


def _resolve_gate(context: ContextTypes.DEFAULT_TYPE) -> FrontendGate | None:
    """Return the gate stored by the composition root, if it is valid."""
    gate = context.application.bot_data.get(GATE_KEY)
    if not isinstance(gate, FrontendGate):
        return None
    return gate


def authorized(
    handler: Callable[Concatenate[Update, ContextTypes.DEFAULT_TYPE, P], Awaitable[None]],
) -> Callable[Concatenate[Update, ContextTypes.DEFAULT_TYPE, P], Coroutine[Any, Any, None]]:
    """Decorator: only allow conversations the frontend gate permits.

    Responsibilities (in order):
      1. Extract the Update from positional/keyword args
      2. Resolve the ``FrontendGate`` from ``bot_data[GATE_KEY]``
      3. Mint the opaque ``telegram:{chat_id}`` key
      4. Delegate to ``authorize_request`` (fresh request id + entry log +
         allow-list check — one source of truth with the Flet path)
      5. Time the handler execution and log ``handler_exit`` via
         ``finish_request``

    Event coverage:
      - DEBUG event=handler_entry   at handler start
      - DEBUG event=handler_exit    at handler end (with duration_ms)
      - WARNING event=unauthorized  if the gate denies the conversation
    """

    @functools.wraps(handler)
    async def wrapper(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> None:
        if update is None:
            # No key exists to authorize, but keep a fresh request id so the
            # diagnostic is correlated like every other handler invocation.
            new_request_id()
            logger.error(
                "Update missing in authorized handler",
                extra={
                    "event": "authorized_missing_update",
                    "handler": handler.__name__,
                },
            )
            return

        gate = _resolve_gate(context)
        if gate is None:
            logger.error(
                "Frontend gate missing from bot_data",
                extra={
                    "event": "authorized_missing_gate",
                    "handler": handler.__name__,
                },
            )
            return

        conversation_id = telegram_key(require_chat(update).id)
        if not authorize_request(gate, conversation_id, handler.__name__):
            return

        start = time.monotonic()
        try:
            return await handler(update, context, *args, **kwargs)
        finally:
            finish_request(handler.__name__, conversation_id, start)

    # ``functools.wraps`` wraps ``wrapper`` in ``functools._Wrapped``, which
    # mypy cannot reconcile with the Concatenate/ParamSpec return type; the
    # callable shape is identical, so cast is type-only.
    return cast(
        Callable[Concatenate[Update, ContextTypes.DEFAULT_TYPE, P], Coroutine[Any, Any, None]],
        wrapper,
    )
