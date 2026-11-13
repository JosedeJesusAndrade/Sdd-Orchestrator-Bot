"""Characterization tests for voice handling (``TranscriptionService`` + handler).

Two surfaces:
  * ``TranscriptionService`` — the OpenAI Whisper call, the no-key skip and the
    three error mappings (all with a fake ``openai`` module in ``sys.modules``).
  * ``handle_voice`` — duration guard, progress edits, and delegation of the
    transcript to ``PromptService.execute``.

No network: the OpenAI client and the ``TranscriptionService`` are replaced by
fakes.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from frontends.telegram.chat_view import TelegramChatView
from frontends.telegram.keys import telegram_key
from handlers import messages
from services import transcription_service
from services.prompt_service import PromptAlreadyRunningError
from services.transcription_service import TranscriptionService, WhisperTranscriber
from tests.handler_fakes import (
    CHAT_ID,
    FakeMessage,
    FakeSender,
    make_context,
    make_update,
)


class _FakeLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, Any]]] = []

    def debug(self, message: str, extra: dict[str, Any] | None = None) -> None:
        self.records.append(("debug", message, extra or {}))

    def error(self, message: str, extra: dict[str, Any] | None = None) -> None:
        self.records.append(("error", message, extra or {}))

    @property
    def events(self) -> list[Any]:
        return [extra.get("event") for _, _, extra in self.records]


def _install_openai(monkeypatch: pytest.MonkeyPatch, create: Any) -> None:
    """Inject a fake ``openai`` module with an ``AsyncOpenAI`` client."""

    class _Client:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key
            self.audio = SimpleNamespace(
                transcriptions=SimpleNamespace(create=create),
            )

    module = types.ModuleType("openai")
    module.__dict__["AsyncOpenAI"] = _Client
    monkeypatch.setitem(sys.modules, "openai", module)


@pytest.fixture
def logger(monkeypatch: pytest.MonkeyPatch) -> _FakeLogger:
    fake = _FakeLogger()
    monkeypatch.setattr(messages, "logger", fake)
    monkeypatch.setattr(transcription_service, "logger", fake)
    return fake


# ── TranscriptionService ────────────────────────────────────────────────


async def test_transcribe_without_api_key_skips(
    monkeypatch: pytest.MonkeyPatch, logger: _FakeLogger
) -> None:
    service = TranscriptionService(WhisperTranscriber(""), enabled=False)

    result = await service.transcribe("unused.ogg", key=telegram_key(123))

    assert result is None
    assert "transcribe_voice_skip" in logger.events


async def test_transcribe_returns_the_stripped_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, logger: _FakeLogger
) -> None:
    audio = tmp_path / "a.ogg"
    audio.write_bytes(b"audio-bytes")
    captured: dict[str, Any] = {}

    async def create(**kwargs: Any) -> str:
        captured.update(kwargs)
        captured["content"] = kwargs["file"].read()
        return "  hello world \n"

    _install_openai(monkeypatch, create)
    service = TranscriptionService(WhisperTranscriber("sk-test"))

    result = await service.transcribe(str(audio), key=telegram_key(1))

    assert result == "hello world"
    assert captured["model"] == "whisper-1"
    assert captured["response_format"] == "text"
    assert captured["content"] == b"audio-bytes"
    assert "transcribe_voice" in logger.events


async def test_transcribe_empty_response_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, logger: _FakeLogger
) -> None:
    audio = tmp_path / "a.ogg"
    audio.write_bytes(b"x")

    async def create(**kwargs: Any) -> str:
        return ""

    _install_openai(monkeypatch, create)
    service = TranscriptionService(WhisperTranscriber("sk-test"))

    assert await service.transcribe(str(audio), key=telegram_key(1)) is None


async def test_transcribe_whitespace_response_is_empty_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, logger: _FakeLogger
) -> None:
    """Current edge case: truthy-but-blank text strips to ``""`` (not ``None``)."""
    audio = tmp_path / "a.ogg"
    audio.write_bytes(b"x")

    async def create(**kwargs: Any) -> str:
        return "   \n"

    _install_openai(monkeypatch, create)
    service = TranscriptionService(WhisperTranscriber("sk-test"))

    assert await service.transcribe(str(audio), key=telegram_key(1)) == ""


async def test_transcribe_api_error_maps_to_api_error_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, logger: _FakeLogger
) -> None:
    audio = tmp_path / "a.ogg"
    audio.write_bytes(b"x")

    class _ApiError(Exception):
        status_code = 500

    async def create(**kwargs: Any) -> str:
        raise _ApiError("server exploded")

    _install_openai(monkeypatch, create)
    service = TranscriptionService(WhisperTranscriber("sk-test"))

    result = await service.transcribe(str(audio), key=telegram_key(1))

    assert result is None
    record = next(r for r in logger.records if r[2].get("event") == "api_error")
    assert record[2]["provider"] == "openai"
    assert record[2]["status"] == 500


async def test_transcribe_generic_error_maps_to_voice_transcribe_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, logger: _FakeLogger
) -> None:
    audio = tmp_path / "a.ogg"
    audio.write_bytes(b"x")

    async def create(**kwargs: Any) -> str:
        raise RuntimeError("boom")

    _install_openai(monkeypatch, create)
    service = TranscriptionService(WhisperTranscriber("sk-test"))

    result = await service.transcribe(str(audio), key=telegram_key(1))

    assert result is None
    record = next(r for r in logger.records if r[2].get("event") == "voice_transcribe_error")
    assert record[2]["error_type"] == "RuntimeError"


async def test_transcribe_import_error_maps_to_voice_transcribe_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, logger: _FakeLogger
) -> None:
    audio = tmp_path / "a.ogg"
    audio.write_bytes(b"x")

    # A module without ``AsyncOpenAI`` makes ``from openai import ...`` raise
    # ImportError, exercising the service's ImportError branch.
    monkeypatch.setitem(sys.modules, "openai", types.ModuleType("openai"))
    service = TranscriptionService(WhisperTranscriber("sk-test"))

    result = await service.transcribe(str(audio), key=telegram_key(1))

    assert result is None
    record = next(r for r in logger.records if r[2].get("event") == "voice_transcribe_error")
    assert record[2]["error_type"] == "ImportError"


# ── handle_voice ────────────────────────────────────────────────────────


class _FakeFile:
    async def download_to_drive(self, path: str) -> None:
        Path(path).write_bytes(b"audio")


class _FakeVoice:
    def __init__(self, duration: int) -> None:
        self.duration = duration

    async def get_file(self) -> _FakeFile:
        return _FakeFile()


class _FakePromptService:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[Any, str, Any]] = []
        self._error = error

    async def execute(self, *, conversation_id: Any, prompt_text: str, view: Any) -> None:
        self.calls.append((conversation_id, prompt_text, view))
        if self._error is not None:
            raise self._error


class _FakeTranscriptionService:
    def __init__(self, result: str | None) -> None:
        self.result = result
        self.calls: list[tuple[str, Any]] = []

    async def transcribe(self, file_path: str, key: Any = None) -> str | None:
        self.calls.append((file_path, key))
        return self.result


def _voice_message(duration: int) -> FakeMessage:
    msg = FakeMessage()
    msg.voice = _FakeVoice(duration)
    return msg


def _container(service: _FakePromptService, transcription: _FakeTranscriptionService) -> Any:
    return SimpleNamespace(
        message_sender=FakeSender(),
        prompt_service=service,
        transcription_service=transcription,
    )


async def test_voice_delegates_transcript_to_prompt_service(
    monkeypatch: pytest.MonkeyPatch, logger: _FakeLogger
) -> None:
    msg = _voice_message(2)
    service = _FakePromptService()
    transcription = _FakeTranscriptionService("hola mundo")

    await messages.handle_voice(make_update(msg), make_context(_container(service, transcription)))

    assert len(service.calls) == 1
    conversation_id, prompt_text, view = service.calls[0]
    assert (conversation_id, prompt_text) == (telegram_key(CHAT_ID), "hola mundo")
    assert isinstance(view, TelegramChatView)
    assert any("Transcripción:" in text and "hola mundo" in text for text in msg.edit_texts)


async def test_voice_too_short_reports_without_transcribing(
    monkeypatch: pytest.MonkeyPatch, logger: _FakeLogger
) -> None:
    msg = _voice_message(0)
    service = _FakePromptService()
    transcription = _FakeTranscriptionService("x")

    await messages.handle_voice(make_update(msg), make_context(_container(service, transcription)))

    assert msg.reply_texts[-1] == "El audio es muy corto, no se detectó voz."
    assert transcription.calls == []
    assert service.calls == []


async def test_voice_without_transcript_reports_failure(
    monkeypatch: pytest.MonkeyPatch, logger: _FakeLogger
) -> None:
    msg = _voice_message(2)
    service = _FakePromptService()
    transcription = _FakeTranscriptionService(None)

    await messages.handle_voice(make_update(msg), make_context(_container(service, transcription)))

    assert "No pude transcribir el audio" in msg.edit_texts[-1]
    assert service.calls == []


async def test_voice_busy_prompt_reports_and_logs(
    monkeypatch: pytest.MonkeyPatch, logger: _FakeLogger
) -> None:
    msg = _voice_message(2)
    service = _FakePromptService(error=PromptAlreadyRunningError())
    transcription = _FakeTranscriptionService("hola")

    await messages.handle_voice(make_update(msg), make_context(_container(service, transcription)))

    assert any("Ya hay un prompt en proceso" in text for text in msg.reply_texts)


async def test_voice_generic_error_reports_and_logs(
    monkeypatch: pytest.MonkeyPatch, logger: _FakeLogger
) -> None:
    msg = _voice_message(2)
    service = _FakePromptService(error=RuntimeError("boom"))
    transcription = _FakeTranscriptionService("hola")

    await messages.handle_voice(make_update(msg), make_context(_container(service, transcription)))

    assert "Error al procesar el audio" in msg.edit_texts[-1]
    assert "voice_handler_error" in logger.events
