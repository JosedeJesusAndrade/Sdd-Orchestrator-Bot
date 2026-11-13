"""Characterization tests for ``/session info`` (``handlers/sessions.py``).

Pins target resolution (explicit / active / default), ID and timestamp
formatting, the two fallbacks and the OpenCode enrichment lines so the
``SessionService.get_info`` + DTO extraction cannot drift.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from frontends.telegram.keys import telegram_key
from handlers.sessions import session_command
from services.session_store import SessionInfo
from tests.handler_fakes import (
    CHAT_ID,
    FakeMessage,
    make_context,
    make_update,
)


def _info(
    name: str = "default",
    *,
    real_id: str | None = None,
    title: str | None = None,
    created: str = "2026-05-19T00:00:00",
    last_used: str | None = None,
    prompt_count: int = 0,
    is_active: bool = False,
) -> SessionInfo:
    return SessionInfo(
        name=name,
        real_id=real_id,
        title=title or name,
        created=created,
        last_used=last_used,
        prompt_count=prompt_count,
        is_active=is_active,
    )


class _FakeStore:
    def __init__(self, sessions: list[SessionInfo], active: SessionInfo | None = None) -> None:
        self._sessions = list(sessions)
        self._active = active
        self.list_calls: list[Any] = []

    async def list_sessions(self, conversation_id: Any) -> list[SessionInfo]:
        self.list_calls.append(conversation_id)
        return list(self._sessions)

    async def get_active_session(self, conversation_id: Any) -> SessionInfo | None:
        return self._active


class _FakeSessionService:
    def __init__(self, enrichment: dict[str, Any] | None = None) -> None:
        self.enrichment = dict(enrichment or {})
        self.enriched: list[str | None] = []

    async def enrich(self, real_id: str | None) -> dict[str, Any]:
        self.enriched.append(real_id)
        return dict(self.enrichment)


async def _run_info(
    msg: FakeMessage,
    store: _FakeStore,
    service: _FakeSessionService,
    args: list[str],
) -> None:
    container = SimpleNamespace(session_store=store, session_service=service)
    await session_command(make_update(msg), make_context(container, args=args))


# ── Target resolution ───────────────────────────────────────────────────


async def test_info_uses_explicit_name() -> None:
    store = _FakeStore([_info("other", real_id="ses_123")], active=_info("default"))
    service = _FakeSessionService()
    msg = FakeMessage()

    await _run_info(msg, store, service, ["info", "other"])

    text = msg.reply_texts[-1]
    assert "Sesión: other" in text
    assert "ses_123" in text
    assert service.enriched == ["ses_123"]


async def test_info_falls_back_to_the_active_session() -> None:
    active = _info("active-one", real_id="ses_a", is_active=True)
    store = _FakeStore([_info("other"), active], active=active)
    service = _FakeSessionService()
    msg = FakeMessage()

    await _run_info(msg, store, service, ["info"])

    assert "Sesión: active-one" in msg.reply_texts[-1]


async def test_info_falls_back_to_default_when_no_active_session() -> None:
    store = _FakeStore([_info("default")], active=None)
    service = _FakeSessionService()
    msg = FakeMessage()

    await _run_info(msg, store, service, ["info"])

    assert "Sesión: default" in msg.reply_texts[-1]


async def test_info_reports_not_found() -> None:
    store = _FakeStore([], active=None)
    service = _FakeSessionService()
    msg = FakeMessage()

    await _run_info(msg, store, service, ["info"])

    assert "La sesión `default` no existe." in msg.reply_texts[-1]
    assert service.enriched == []


# ── ID formatting ───────────────────────────────────────────────────────


async def test_info_truncates_long_ids_to_24_chars() -> None:
    long_id = "s" * 30
    store = _FakeStore([_info("default", real_id=long_id)])
    service = _FakeSessionService()
    msg = FakeMessage()

    await _run_info(msg, store, service, ["info"])

    text = msg.reply_texts[-1]
    assert long_id[:24] + "..." in text
    assert long_id not in text


async def test_info_keeps_ids_of_exactly_24_chars() -> None:
    short_id = "x" * 24
    store = _FakeStore([_info("default", real_id=short_id)])
    service = _FakeSessionService()
    msg = FakeMessage()

    await _run_info(msg, store, service, ["info"])

    text = msg.reply_texts[-1]
    assert short_id in text
    assert short_id + "..." not in text


async def test_info_shows_placeholder_without_an_id() -> None:
    store = _FakeStore([_info("default", real_id=None)])
    service = _FakeSessionService()
    msg = FakeMessage()

    await _run_info(msg, store, service, ["info"])

    assert "(sin ID aún)" in msg.reply_texts[-1]


# ── Timestamp formatting / fallbacks ────────────────────────────────────


async def test_info_without_last_used_shows_nunca() -> None:
    store = _FakeStore([_info("default", last_used=None)])
    service = _FakeSessionService()
    msg = FakeMessage()

    await _run_info(msg, store, service, ["info"])

    assert "Último uso: nunca" in msg.reply_texts[-1]


async def test_info_with_valid_last_used_uses_relative_time() -> None:
    last_used = (datetime.now(UTC) - timedelta(hours=2, minutes=30)).isoformat()
    store = _FakeStore([_info("default", last_used=last_used)])
    service = _FakeSessionService()
    msg = FakeMessage()

    await _run_info(msg, store, service, ["info"])

    assert "hace 2h 30min" in msg.reply_texts[-1]


async def test_info_with_malformed_last_used_falls_back_to_raw_value() -> None:
    store = _FakeStore([_info("default", last_used="not-a-date")])
    service = _FakeSessionService()
    msg = FakeMessage()

    await _run_info(msg, store, service, ["info"])

    assert "Último uso: not-a-date" in msg.reply_texts[-1]


async def test_info_truncates_created_timestamp_to_19_chars() -> None:
    store = _FakeStore([_info("default", created="2026-05-19T00:00:00+00:00")])
    service = _FakeSessionService()
    msg = FakeMessage()

    await _run_info(msg, store, service, ["info"])

    assert "Creada: 2026-05-19T00:00:00" in msg.reply_texts[-1]


async def test_info_keeps_the_missing_created_marker() -> None:
    store = _FakeStore([_info("default", created="—")])
    service = _FakeSessionService()
    msg = FakeMessage()

    await _run_info(msg, store, service, ["info"])

    assert "Creada: —" in msg.reply_texts[-1]


# ── Enrichment ──────────────────────────────────────────────────────────


async def test_info_renders_enrichment_fields() -> None:
    store = _FakeStore([_info("default", real_id="ses_1")])
    service = _FakeSessionService(
        {
            "message_count": 3,
            "created_at": "2026-05-19T00:00:00+00:00",
            "model": "deepseek/deepseek-flash",
        }
    )
    msg = FakeMessage()

    await _run_info(msg, store, service, ["info"])

    text = msg.reply_texts[-1]
    assert "Mensajes en BD: 3" in text
    assert "Creada (BD): 2026-05-19T00:00:00+00:00" in text
    assert "Modelo (BD): deepseek/deepseek-flash" in text
    assert service.enriched == ["ses_1"]


async def test_info_omits_enrichment_lines_when_absent() -> None:
    store = _FakeStore([_info("default", real_id="ses_1")])
    service = _FakeSessionService()
    msg = FakeMessage()

    await _run_info(msg, store, service, ["info"])

    text = msg.reply_texts[-1]
    assert "Mensajes en BD" not in text
    assert "Creada (BD)" not in text
    assert "Modelo (BD)" not in text


async def test_info_includes_the_session_title_and_prompt_count() -> None:
    store = _FakeStore([_info("default", title="My title", prompt_count=9)])
    service = _FakeSessionService()
    msg = FakeMessage()

    await _run_info(msg, store, service, ["info"])

    text = msg.reply_texts[-1]
    assert "Título: My title" in text
    assert "Prompts: 9" in text
    assert store.list_calls == [telegram_key(CHAT_ID)]
