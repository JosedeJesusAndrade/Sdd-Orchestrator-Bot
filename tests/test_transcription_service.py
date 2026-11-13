"""Unit tests for the ``TranscriptionService`` port seam (core-isolation item 10).

The voice characterization file pins the Whisper/OpenAI behavior. This file
proves the service is driven entirely through ``TranscriberPort`` — a fake with
no ``openai`` import — and that the no-key skip short-circuits the port.
"""

from __future__ import annotations

from services.transcription_service import TranscriptionService


class _FakeTranscriber:
    def __init__(self, result: str | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[str] = []

    async def transcribe(self, file_path: str) -> str | None:
        self.calls.append(file_path)
        if self.error is not None:
            raise self.error
        return self.result


async def test_enabled_service_delegates_to_the_port() -> None:
    transcriber = _FakeTranscriber("hola")
    service = TranscriptionService(transcriber)

    assert await service.transcribe("a.ogg") == "hola"
    assert transcriber.calls == ["a.ogg"]


async def test_disabled_service_skips_without_calling_the_port() -> None:
    transcriber = _FakeTranscriber("hola")
    service = TranscriptionService(transcriber, enabled=False)

    assert await service.transcribe("a.ogg") is None
    assert transcriber.calls == []


async def test_port_failure_returns_none() -> None:
    transcriber = _FakeTranscriber(error=RuntimeError("boom"))
    service = TranscriptionService(transcriber)

    assert await service.transcribe("a.ogg") is None
