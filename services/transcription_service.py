"""TranscriptionService: OpenAI Whisper voice transcription, frontend-agnostic.

Extracted from ``handlers/messages.py::transcribe_voice`` (core-isolation item
10). This module owns the transcription *logic*; the Telegram handler keeps the
download (``voice.get_file()`` / ``download_to_drive``) and temp-file cleanup.

Design guarantees:
  - **PTB-free**: no Telegram types, no frontend imports. The audio arrives as a
    local file path.
  - **Ports, not the SDK**: :class:`TranscriberPort` is the injectable seam; the
    production :class:`WhisperTranscriber` lazily imports ``openai`` (an
    optional dependency) and keeps the exact Whisper call (``whisper-1`` /
    ``response_format="text"`` / strip).
  - **No behavior drift**: the same structured log events the handler used to
    emit (``transcribe_voice_skip``, ``transcribe_voice``, ``api_error``,
    ``voice_transcribe_error``) are emitted here, including the ``ImportError``
    hint.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from utils.logging import get_module_logger, mask_conversation_key

if TYPE_CHECKING:
    from services.conversation import ConversationKey

logger = get_module_logger(__name__)


class TranscriberPort(Protocol):
    """A fakeable audio-to-text backend."""

    async def transcribe(self, file_path: str) -> str | None: ...


class WhisperTranscriber:
    """Production transcriber backed by OpenAI Whisper (lazy import)."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def transcribe(self, file_path: str) -> str | None:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self._api_key)

        with open(file_path, "rb") as audio_file:
            transcript = await client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="text",
            )

        return transcript.strip() if transcript else None


class TranscriptionService:
    """Transcribe audio files, mapping failures to ``None`` plus a log event."""

    def __init__(self, transcriber: TranscriberPort, *, enabled: bool = True) -> None:
        self._transcriber = transcriber
        self._enabled = enabled

    async def transcribe(self, file_path: str, key: ConversationKey | None = None) -> str | None:
        """Return the stripped transcript, or ``None`` when unavailable.

        ``enabled=False`` (no API key) skips silently at DEBUG. Import failures,
        provider/API errors and any other exception are logged with the legacy
        event names and returned as ``None``.
        """
        masked = mask_conversation_key(key) if key is not None else "-"

        if not self._enabled:
            logger.debug(
                "OPENAI_API_KEY not set — skipping voice transcription",
                extra={"event": "transcribe_voice_skip", "chat_id_masked": masked},
            )
            return None

        logger.debug(
            "Transcribe voice",
            extra={"event": "transcribe_voice", "chat_id_masked": masked},
        )

        try:
            return await self._transcriber.transcribe(file_path)
        except ImportError:
            logger.error(
                "openai package not installed",
                extra={
                    "event": "voice_transcribe_error",
                    "chat_id_masked": masked,
                    "error_type": "ImportError",
                    "hint": "Run: pip install openai",
                },
            )
            return None
        except Exception as e:
            # Differentiate API errors (HTTP) from other failures.
            # openai raises APIStatusError with a ``status_code`` for 4xx/5xx.
            status = getattr(e, "status_code", None)
            if status is not None:
                logger.error(
                    "Voice transcription API error",
                    extra={
                        "event": "api_error",
                        "provider": "openai",
                        "status": status,
                        "error": str(e),
                        "chat_id_masked": masked,
                    },
                )
            else:
                logger.error(
                    "Voice transcription failed",
                    extra={
                        "event": "voice_transcribe_error",
                        "chat_id_masked": masked,
                        "error_type": type(e).__name__,
                        "error": str(e),
                    },
                )
            return None
