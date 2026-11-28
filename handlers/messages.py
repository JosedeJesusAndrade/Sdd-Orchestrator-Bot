"""Message handlers: text prompts and voice messages.

Architecture change (Week 2→3):
  All handlers now access services via AppContainer from PTB context
  instead of lazy-importing the bot module.

Phase 2 logging:
  - Uses per-module logger (`__name__`) instead of the root `config.logger`.

Core-isolation:
  - The Whisper transcription (and its ``transcribe_voice_skip`` /
    ``transcribe_voice`` / ``api_error`` / ``voice_transcribe_error`` events)
    lives in ``services/transcription_service.py``. This handler keeps only the
    Telegram download and temp-file cleanup, then delegates to the service.
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import timedelta
from typing import cast

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from frontends.telegram.chat_view import TelegramChatView
from frontends.telegram.container import AppContainer
from frontends.telegram.keys import telegram_key
from handlers import authorized
from handlers._guards import require_chat, require_message, require_text
from services.prompt_service import PromptAlreadyRunningError
from telegram_config import CONTAINER_KEY
from utils.logging import get_module_logger, log_exception, mask_chat_id

logger = get_module_logger(__name__)


def _get_container(context: ContextTypes.DEFAULT_TYPE) -> AppContainer:
    """Extract the typed AppContainer from PTB context."""
    return cast(AppContainer, context.application.bot_data[CONTAINER_KEY])


@authorized
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle voice messages: transcribe and process as prompt."""
    chat_id = require_chat(update).id
    msg = require_message(update)
    container = _get_container(context)

    voice = msg.voice
    if not voice:
        return

    # PTB types ``Voice.duration`` as ``int | datetime.timedelta``. Comparing a
    # timedelta with an int raises TypeError, so normalize defensively.
    raw_duration = voice.duration
    duration = raw_duration.total_seconds() if isinstance(raw_duration, timedelta) else raw_duration
    if duration < 1:
        await msg.reply_text("El audio es muy corto, no se detectó voz.")
        return

    progress_msg = await msg.reply_text("\U0001f3a4 Transcribiendo audio...")

    temp_path = None
    try:
        file = await voice.get_file()
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            temp_path = tmp.name
        await file.download_to_drive(temp_path)

        text = await container.transcription_service.transcribe(
            temp_path, key=telegram_key(chat_id)
        )

        if not text:
            await progress_msg.edit_text(
                "\u274c No pude transcribir el audio. Intenta de nuevo o escribe el prompt."
            )
            return

        await progress_msg.edit_text(
            "\U0001f3a4 *Transcripción:* {text}".format(
                text=text[:200] + ("..." if len(text) > 200 else "")
            ),
            parse_mode=ParseMode.MARKDOWN_V2,
        )

        # Delegate to PromptService via container
        await container.prompt_service.execute(
            conversation_id=telegram_key(chat_id),
            prompt_text=text,
            view=TelegramChatView(container.message_sender, chat_id),
        )

    except PromptAlreadyRunningError:
        await msg.reply_text("\u23f3 Ya hay un prompt en proceso. Usa /cancel para cancelarlo.")
    except Exception as e:
        # Why extra={} here? The bare "%s" formatter above meant we lost the
        # machine-readable event index and any correlated fields (chat_id,
        # error_type). The voice path is high-signal for diagnosing audio
        # pipeline issues, so capture the exception type explicitly so we can
        # grep for `event=voice_handler_error error_type=...` later.
        logger.error(
            "Voice handler error",
            extra={
                "event": "voice_handler_error",
                "chat_id_masked": mask_chat_id(chat_id),
                "error_type": type(e).__name__,
                "error": str(e),
            },
        )
        try:
            await progress_msg.edit_text("\u274c Error al procesar el audio. Intenta de nuevo.")
        except Exception:
            log_exception(
                "voice_error_edit_failed",
                module=__name__,
                level=logging.DEBUG,
            )
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                log_exception(
                    "voice_temp_cleanup_failed",
                    module=__name__,
                    level=logging.WARNING,
                )


@authorized
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle a text prompt: validate, delegate to PromptService.

    Raises PromptAlreadyRunningError if a prompt is already executing.
    The caller (@authorized decorator) ensures only authorized chats can use this.
    """
    chat_id = require_chat(update).id
    msg = require_message(update)
    container = _get_container(context)

    prompt = require_text(msg).strip()
    if not prompt:
        return

    try:
        await container.prompt_service.execute(
            conversation_id=telegram_key(chat_id),
            prompt_text=prompt,
            view=TelegramChatView(container.message_sender, chat_id),
        )
    except PromptAlreadyRunningError:
        await msg.reply_text("\u23f3 Ya hay un prompt en proceso. Usa /cancel para cancelarlo.")
