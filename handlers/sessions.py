"""Session handlers: /session new|list|switch|delete|info|discover|adopt.

Architecture change (core-isolation item 9):
  The ``if/elif`` router is gone. ``parse_session_command`` turns the raw args
  into a typed command and ``SessionService.dispatch`` executes it; this module
  only renders the outcome and maps domain errors to UI copy (the view).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from telegram import Update
from telegram.ext import ContextTypes

from frontends.telegram.container import AppContainer
from frontends.telegram.keys import telegram_key
from handlers import authorized
from handlers._guards import require_chat, require_message
from locales import get_strings
from services.session_commands import (
    SessionAdopted,
    SessionCommandKind,
    SessionCreated,
    SessionDeleted,
    SessionDiscovered,
    SessionInfoResult,
    SessionListed,
    SessionSwitched,
    SessionUsage,
    parse_session_command,
)
from services.session_service import (
    DiscoveredSession,
    InvalidSessionNameError,
    SessionDetails,
    SessionListSnapshot,
    SessionNotDiscoveredError,
)
from services.session_store import SessionExistsError, SessionNotFoundError
from telegram_config import CONTAINER_KEY
from utils.logging import get_module_logger, mask_conversation_key
from utils.time_formatting import relative_time

logger = get_module_logger(__name__)

_ADOPT_USAGE = "\u274c Uso: /session adopt <id> <nombre>"
_ADOPT_PLACEHOLDER = (
    "\u274c Reemplaz\u00e1 `<nombre>` con un nombre para la sesi\u00f3n.\n"
    "Ejemplo: `/session adopt {real_id} telegram-bot`"
)
_ADOPT_INVALID_NAME = (
    "\u274c El nombre debe tener entre 1 y 30 caracteres y solo usar "
    "letras, n\u00fameros, guiones y guiones bajos.\n"
    "Ejemplo: `mi-sesion`, `balanceate_api`, `docs`"
)


def _get_container(context: ContextTypes.DEFAULT_TYPE) -> AppContainer:
    """Extract the typed AppContainer from PTB context."""
    return cast(AppContainer, context.application.bot_data[CONTAINER_KEY])


def _format_usage(strings: Any, usage: SessionUsage) -> str:
    """Render a usage / unknown-subcommand response."""
    kind = usage.kind
    if kind is SessionCommandKind.NEW:
        return str(strings.SESSION_NEW_USAGE)
    if kind is SessionCommandKind.SWITCH:
        return str(strings.SESSION_SWITCH_USAGE)
    if kind is SessionCommandKind.DELETE:
        return str(strings.SESSION_DELETE_USAGE)
    if kind is SessionCommandKind.ADOPT:
        return _ADOPT_USAGE
    if kind is SessionCommandKind.UNKNOWN:
        return str(strings.SESSION_UNKNOWN_SUB.format(sub=usage.raw))
    return str(strings.SESSION_USAGE)


def _format_list(strings: Any, snapshot: SessionListSnapshot) -> str:
    """Render bot-managed + OpenCode-discovered sessions (unified)."""
    bot_sessions = snapshot.sessions
    unadopted = snapshot.unadopted

    if not bot_sessions and not unadopted:
        return str(strings.SESSION_LIST_EMPTY) + " Us\u00e1 /session new <nombre> para crear una."

    lines = [str(strings.SESSION_LIST_HEADER) + "\n"]

    # Bot sessions first (with adoption status from OpenCode)
    for s in bot_sessions:
        marker = "\U0001f7e2" if s.is_active else "\u26aa"
        if s.real_id:
            id_display = s.real_id[:16] + "..."
            last_used_str = ""
            if s.last_used:
                try:
                    last_dt = datetime.fromisoformat(s.last_used)
                    last_used_str = f", {relative_time(last_dt)}"
                except Exception:
                    # Malformed timestamp: omit the ", hace X" suffix.
                    pass
            lines.append(
                f"{marker} {s.name} \u2192 `{id_display}` ({s.prompt_count} prompts{last_used_str})"
            )
        else:
            lines.append(f"{marker} {s.name} \u2192 (nueva, sin usar a\u00fan)")

    # OpenCode unadopted sessions
    if unadopted:
        lines.append("\n\u26aa *Sesiones OpenCode no adoptadas:*")
        lines.append("_Us\u00e1 /session adopt `<id>` `<nombre>` para adoptarlas._\n")
        for oc in unadopted:
            lines.append(f"\u26aa `{oc.title}`\n   `{oc.id}`")

    return "\n".join(lines)


def _format_info(strings: Any, details: SessionDetails) -> str:
    """Render the RAW ``SessionDetails`` into the ``/session info`` view."""
    oc_id = details.real_id
    id_display = (
        str(strings.SESSION_INFO_NO_ID)
        if not oc_id
        else (oc_id[:24] + "..." if len(oc_id) > 24 else oc_id)
    )

    last_used_str = "nunca"
    if details.last_used:
        try:
            last_dt = datetime.fromisoformat(details.last_used)
            last_used_str = relative_time(last_dt)
        except Exception:
            # Malformed timestamp: fall back to the raw value.
            last_used_str = str(details.last_used)

    created = details.created
    created_display = created[:19] if created and created != "\u2014" else created

    lines = [
        str(strings.SESSION_INFO_HEADER.format(name=details.name)),
        f"    ID: {id_display}",
        f"    T\u00edtulo: {details.title}",
        f"    Creada: {created_display}",
        f"    \u00daltimo uso: {last_used_str}",
        f"    Prompts: {details.prompt_count}",
    ]

    enrichment = details.enrichment
    if "message_count" in enrichment:
        lines.append(f"    Mensajes en BD: {enrichment['message_count']}")
    if "created_at" in enrichment:
        lines.append(f"    Creada (BD): {enrichment['created_at']}")
    if "model" in enrichment:
        lines.append(f"    Modelo (BD): {enrichment['model']}")

    return "\n".join(lines)


def _format_discover(strings: Any, discovered: list[DiscoveredSession]) -> str:
    """Render the OpenCode discovery listing (max 15 rows)."""
    if not discovered:
        return str(strings.SESSION_DISCOVER_EMPTY)

    lines = [str(strings.SESSION_DISCOVER_HEADER) + "\n"]

    for oc in discovered[:15]:
        sid = oc.id
        title = oc.title[:60]

        if oc.adopted_name:
            lines.append(
                f"\u2705 *{oc.adopted_name}* \u2192 `{sid}` "
                f"(ya adoptada, {oc.prompt_count} prompts)"
            )
        else:
            lines.append(
                f"\u26aa *{title}*\n   `{sid}`\n   Para adoptar: `/session adopt {sid} <nombre>`"
            )
        lines.append("")

    if len(discovered) > 15:
        lines.append(f"Mostrando 15 de {len(discovered)} sesiones.")

    lines.append("\nUsa `/session adopt <id> <nombre>` para adoptar una sesi\u00f3n.")
    return "\n".join(lines)


async def _reply_exists(msg: Any, strings: Any, command: Any) -> None:
    """Map a duplicate-name error to the per-subcommand copy."""
    if command.kind is SessionCommandKind.NEW:
        await msg.reply_text(
            str(strings.SESSION_EXISTS.format(name=command.name)) + " "
            f"Us\u00e1 /session switch {command.name} para cambiarte."
        )
        return
    await msg.reply_text(
        f"\u26a0\ufe0f El nombre '{command.name}' ya existe. "
        f"Us\u00e1 /session switch {command.name} para usarla."
    )


async def _reply_not_found(msg: Any, strings: Any, command: Any, resolved_name: str) -> None:
    """Map a missing session to the per-subcommand copy."""
    if command.kind is SessionCommandKind.SWITCH:
        await msg.reply_text(
            str(strings.SESSION_NOT_FOUND.format(name=command.name))
            + "\nUs\u00e1 /session list para ver tus sesiones."
        )
        return
    if command.kind is SessionCommandKind.DELETE:
        await msg.reply_text(str(strings.SESSION_NOT_FOUND.format(name=command.name)))
        return
    await msg.reply_text(str(strings.SESSION_NOT_FOUND.format(name=resolved_name)))


async def _render_outcome(
    msg: Any,
    container: AppContainer,
    chat_id: int,
    key: Any,
    strings: Any,
    command: Any,
    outcome: Any,
) -> None:
    """Render a successful dispatch outcome (the Telegram view)."""
    if isinstance(outcome, SessionCreated):
        name = command.name or outcome.session.name
        logger.info("Session '%s' created for %s", name, mask_conversation_key(key))
        await msg.reply_text(
            str(strings.SESSION_CREATED.format(name=name))
            + "\nEl pr\u00f3ximo prompt se ejecutar\u00e1 en esta sesi\u00f3n."
        )
    elif isinstance(outcome, SessionListed):
        await msg.reply_text(_format_list(strings, outcome.snapshot))
    elif isinstance(outcome, SessionSwitched):
        name = command.name or outcome.session.name
        logger.info("Session switched to '%s' for %s", name, mask_conversation_key(key))
        await msg.reply_text(
            str(strings.SESSION_SWITCHED.format(name=name))
            + "\nPr\u00f3ximo prompt usar\u00e1 esta sesi\u00f3n."
        )
    elif isinstance(outcome, SessionDeleted):
        await msg.reply_text(str(strings.SESSION_DELETED.format(name=outcome.name)))
    elif isinstance(outcome, SessionInfoResult):
        await msg.reply_text(_format_info(strings, outcome.details))
    elif isinstance(outcome, SessionDiscovered):
        text = _format_discover(strings, outcome.sessions)
        if outcome.sessions:
            # Use MessageSender for delivery of the (potentially long) list.
            await container.message_sender.send_formatted(chat_id, text)
        else:
            await msg.reply_text(text)
    elif isinstance(outcome, SessionAdopted):
        await msg.reply_text(
            f"\u2705 Sesi\u00f3n '{outcome.name}' adoptada (ID: `{outcome.real_id}`)"
        )
    else:
        await msg.reply_text(_format_usage(strings, outcome))


@authorized
async def session_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for /session subcommands."""
    msg = require_message(update)
    chat_id = require_chat(update).id
    container = _get_container(context)
    strings = get_strings()
    key = telegram_key(chat_id)

    parsed = parse_session_command(context.args or [])
    if isinstance(parsed, SessionUsage):
        await msg.reply_text(_format_usage(strings, parsed))
        return

    # ``adopt`` argument guards are view-level: the ``<nombre>`` placeholder is
    # documentation, not a valid session name, so it must never reach the service.
    if parsed.kind is SessionCommandKind.ADOPT and (
        not parsed.real_id or not parsed.name or parsed.name == "<nombre>"
    ):
        if not parsed.real_id or not parsed.name:
            await msg.reply_text(_ADOPT_USAGE)
        else:
            assert parsed.real_id is not None
            await msg.reply_text(_ADOPT_PLACEHOLDER.format(real_id=parsed.real_id[:24]))
        return

    try:
        outcome = await container.session_service.dispatch(key, parsed)
    except SessionExistsError:
        await _reply_exists(msg, strings, parsed)
        return
    except SessionNotFoundError as exc:
        await _reply_not_found(msg, strings, parsed, str(exc))
        return
    except InvalidSessionNameError:
        await msg.reply_text(_ADOPT_INVALID_NAME)
        return
    except SessionNotDiscoveredError:
        await msg.reply_text(f"\u274c La sesi\u00f3n `{parsed.real_id}` no existe en OpenCode.")
        return

    await _render_outcome(msg, container, chat_id, key, strings, parsed, outcome)
