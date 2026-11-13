"""Telegram frontend configuration (the *view* half of the config split).

This module owns the Telegram-only settings and the explicit fail-fast for
``ALLOWED_CHAT_IDS``. Unlike the old ``config.py`` it has NO import-time
``SystemExit``: importing ``telegram_config`` (or ``handlers.*``) is always
safe, even with an empty/invalid allow-list. The entry points call
:func:`load_telegram_config` explicitly, before building the app, and the
fatal error is raised only from there.

The frontend-agnostic core settings live in ``core_config``.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import NoReturn

from dotenv import load_dotenv

from core_config import BASE_DIR, logger

# Ensure .env is loaded even if this module is imported before core_config's
# load_dotenv ran (idempotent; existing process env always wins).
load_dotenv(BASE_DIR / ".env")

# ─── Telegram ───
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_MAX_MESSAGE_LENGTH = 4000
CONNECTIVITY_CHECK_INTERVAL = 30  # seconds between connectivity pings
CONNECTIVITY_FIRST_CHECK_DELAY = 10  # seconds before first connectivity check
CONTAINER_KEY = "container"

ALLOWED_CHAT_IDS_RAW = os.getenv("ALLOWED_CHAT_IDS", "")


@dataclass(frozen=True)
class TelegramConfig:
    """Validated Telegram settings produced by :func:`load_telegram_config`."""

    token: str
    allowed_chat_ids: tuple[int, ...]
    max_message_length: int = TELEGRAM_MAX_MESSAGE_LENGTH


def parse_allowed_chat_ids(raw: str) -> list[int]:
    """Parse a CSV of chat IDs into a list of ints.

    Pure and side-effect free so the contract is unit-testable without
    importing/initializing the whole bot. Raises ValueError on any
    non-numeric entry; the caller turns that into a fatal SystemExit.
    """
    return [int(cid.strip()) for cid in raw.split(",") if cid.strip()]


def _allowed_or_empty(raw: str) -> list[int]:
    """Tolerant parse used at import time — NEVER raises.

    A bad/empty value yields ``[]`` so importing this module stays side-effect
    free. The fatal validation lives in :func:`load_telegram_config`, called by
    the entry points. Mutating this list in place (see ``load_telegram_config``)
    keeps existing ``from telegram_config import ALLOWED_CHAT_IDS`` bindings
    valid after the reload.
    """
    try:
        return parse_allowed_chat_ids(raw)
    except ValueError:
        return []


#: Configured allow-list. Populated from the env for consumers that import it
#: eagerly (handlers, tests); re-validated and refreshed by the loader.
ALLOWED_CHAT_IDS: list[int] = _allowed_or_empty(ALLOWED_CHAT_IDS_RAW)


def _fail_fast_allowed_chat_ids(raw: str) -> NoReturn:
    """Report an invalid allow-list and exit without assuming logging exists.

    This runs from the explicit loader (usually before any entry point joined
    the app), so ``logger`` may still have no handlers. A logger.critical()
    alone would only reach logging.lastResort; the explicit stderr write
    guarantees the operator sees a clear message in every context (daemon,
    script, test subprocess). If an embedder DID configure handlers before
    calling the loader, the structured record goes through them too.
    """
    message = (
        "ALLOWED_CHAT_IDS inválido: se esperaba una lista CSV de enteros "
        f"(ej. '123456789,987654321'). Valor recibido: {raw!r}. "
        "El bot no puede iniciar sin una allow-list válida."
    )
    if logger.handlers:
        logger.critical(
            message,
            extra={"event": "config_allowed_chats_parse_error", "raw": raw},
            exc_info=True,
        )
    else:
        sys.stderr.write(message + "\n")
        sys.stderr.flush()
    raise SystemExit(1)


def load_telegram_config() -> TelegramConfig:
    """Parse + validate the Telegram settings, failing fast on a bad allow-list.

    Explicit, entry-point-owned replacement for the old import-time
    ``SystemExit``. Raises ``SystemExit(1)`` (with the operator-facing message)
    when ``ALLOWED_CHAT_IDS`` is empty or non-numeric. On success it refreshes
    the module-level :data:`ALLOWED_CHAT_IDS` **in place** and returns the
    frozen :class:`TelegramConfig`.
    """
    raw = os.getenv("ALLOWED_CHAT_IDS", "")
    try:
        allowed = parse_allowed_chat_ids(raw)
        if not allowed:
            # An empty allow-list is a fatal misconfiguration: the bot would
            # start and reject every chat silently. Fail fast alongside the
            # parse error.
            raise ValueError("ALLOWED_CHAT_IDS produced an empty allow-list")
    except ValueError:
        _fail_fast_allowed_chat_ids(raw)

    # In-place update keeps ``from telegram_config import ALLOWED_CHAT_IDS``
    # bindings (handlers) pointing at the refreshed values.
    ALLOWED_CHAT_IDS[:] = allowed
    return TelegramConfig(
        token=TELEGRAM_BOT_TOKEN,
        allowed_chat_ids=tuple(ALLOWED_CHAT_IDS),
    )
