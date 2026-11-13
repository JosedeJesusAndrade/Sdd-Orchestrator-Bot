"""Characterization tests for the ``/session`` subcommand router.

Pins the ``if/elif`` dispatch in ``handlers/sessions.py`` (new/list/switch/
delete/info/discover/adopt + unknown + missing-arg usage) before it becomes a
typed ``parse_session_command`` + ``dispatch`` pair.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from handlers.sessions import session_command
from locales import get_strings
from services.session_service import SessionListSnapshot
from services.session_store import SessionInfo
from tests.handler_fakes import CHAT_ID, FakeMessage, make_context, make_update


def _info(name: str = "default", real_id: str | None = "ses_x") -> SessionInfo:
    return SessionInfo(
        name=name,
        real_id=real_id,
        title=name,
        created="2026-05-19T00:00:00",
        last_used=None,
        prompt_count=0,
    )


class _FakeStore:
    def __init__(self) -> None:
        self.list_calls: list[Any] = []

    async def list_sessions(self, conversation_id: Any) -> list[SessionInfo]:
        self.list_calls.append(conversation_id)
        return [_info()]

    async def get_active_session(self, conversation_id: Any) -> SessionInfo | None:
        return None


class _FakeSessionService:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def create(self, conversation_id: Any, name: str) -> SessionInfo:
        self.calls.append(("create", conversation_id, name))
        return _info(name)

    async def list_with_discovered(self, conversation_id: Any) -> SessionListSnapshot:
        self.calls.append(("list", conversation_id))
        return SessionListSnapshot(sessions=[], unadopted=[])

    async def switch(self, conversation_id: Any, name: str) -> SessionInfo:
        self.calls.append(("switch", conversation_id, name))
        return _info(name)

    async def delete(self, conversation_id: Any, name: str) -> str | None:
        self.calls.append(("delete", conversation_id, name))
        return None

    async def discover(self, conversation_id: Any) -> list[Any]:
        self.calls.append(("discover", conversation_id))
        return []

    async def adopt(self, conversation_id: Any, real_id: str, name: str) -> None:
        self.calls.append(("adopt", conversation_id, real_id, name))

    async def enrich(self, real_id: str | None) -> dict[str, Any]:
        self.calls.append(("enrich", real_id))
        return {}


async def _run(
    msg: FakeMessage,
    service: _FakeSessionService,
    store: _FakeStore,
    args: list[str] | None,
) -> None:
    container = SimpleNamespace(session_service=service, session_store=store)
    await session_command(make_update(msg), make_context(container, args=args))


# ── Dispatch table ──────────────────────────────────────────────────────


async def test_no_args_shows_usage_without_dispatch() -> None:
    service = _FakeSessionService()
    store = _FakeStore()
    msg = FakeMessage()

    await _run(msg, service, store, None)

    assert msg.reply_texts == [get_strings().SESSION_USAGE]
    assert service.calls == []
    assert store.list_calls == []


async def test_new_dispatches_to_create() -> None:
    service = _FakeSessionService()
    store = _FakeStore()
    msg = FakeMessage()

    await _run(msg, service, store, ["new", "sess-1"])

    assert service.calls == [("create", CHAT_ID, "sess-1")]
    assert "creada y activada" in msg.reply_texts[-1]


async def test_new_without_name_shows_usage() -> None:
    service = _FakeSessionService()
    store = _FakeStore()
    msg = FakeMessage()

    await _run(msg, service, store, ["new"])

    assert msg.reply_texts == [get_strings().SESSION_NEW_USAGE]
    assert service.calls == []


async def test_list_dispatches_to_list_with_discovered() -> None:
    service = _FakeSessionService()
    store = _FakeStore()
    msg = FakeMessage()

    await _run(msg, service, store, ["list"])

    assert service.calls == [("list", CHAT_ID)]
    assert get_strings().SESSION_LIST_EMPTY in msg.reply_texts[-1]


async def test_switch_dispatches_to_switch() -> None:
    service = _FakeSessionService()
    store = _FakeStore()
    msg = FakeMessage()

    await _run(msg, service, store, ["switch", "other"])

    assert service.calls == [("switch", CHAT_ID, "other")]
    assert "Sesión cambiada a `other`" in msg.reply_texts[-1]


async def test_switch_without_name_shows_usage() -> None:
    service = _FakeSessionService()
    store = _FakeStore()
    msg = FakeMessage()

    await _run(msg, service, store, ["switch"])

    assert msg.reply_texts == [get_strings().SESSION_SWITCH_USAGE]
    assert service.calls == []


async def test_delete_dispatches_to_delete() -> None:
    service = _FakeSessionService()
    store = _FakeStore()
    msg = FakeMessage()

    await _run(msg, service, store, ["delete", "other"])

    assert service.calls == [("delete", CHAT_ID, "other")]
    assert "Sesión `other` eliminada" in msg.reply_texts[-1]


async def test_delete_without_name_shows_usage() -> None:
    service = _FakeSessionService()
    store = _FakeStore()
    msg = FakeMessage()

    await _run(msg, service, store, ["delete"])

    assert msg.reply_texts == [get_strings().SESSION_DELETE_USAGE]
    assert service.calls == []


async def test_info_reads_the_store_and_enriches() -> None:
    service = _FakeSessionService()
    store = _FakeStore()
    msg = FakeMessage()

    await _run(msg, service, store, ["info"])

    assert store.list_calls == [CHAT_ID]
    assert service.calls == [("enrich", "ses_x")]


async def test_discover_dispatches_to_discover() -> None:
    service = _FakeSessionService()
    store = _FakeStore()
    msg = FakeMessage()

    await _run(msg, service, store, ["discover"])

    assert service.calls == [("discover", CHAT_ID)]
    assert msg.reply_texts[-1] == get_strings().SESSION_DISCOVER_EMPTY


async def test_adopt_dispatches_with_id_and_name() -> None:
    service = _FakeSessionService()
    store = _FakeStore()
    msg = FakeMessage()

    await _run(msg, service, store, ["adopt", "ses_1", "friendly"])

    assert service.calls == [("adopt", CHAT_ID, "ses_1", "friendly")]
    assert "adoptada" in msg.reply_texts[-1]


async def test_adopt_without_arguments_shows_usage() -> None:
    service = _FakeSessionService()
    store = _FakeStore()
    msg = FakeMessage()

    await _run(msg, service, store, ["adopt", "ses_1"])

    assert "Uso: /session adopt <id> <nombre>" in msg.reply_texts[-1]
    assert service.calls == []


async def test_adopt_with_placeholder_name_is_rejected() -> None:
    service = _FakeSessionService()
    store = _FakeStore()
    msg = FakeMessage()

    await _run(msg, service, store, ["adopt", "ses_1", "<nombre>"])

    assert "Reemplazá `<nombre>`" in msg.reply_texts[-1]
    assert service.calls == []


async def test_unknown_subcommand_reports_it() -> None:
    service = _FakeSessionService()
    store = _FakeStore()
    msg = FakeMessage()

    await _run(msg, service, store, ["frobnicate"])

    assert msg.reply_texts == [get_strings().SESSION_UNKNOWN_SUB.format(sub="frobnicate")]
    assert service.calls == []


@pytest.mark.parametrize("raw", ["LIST", "List", "lIsT"])
async def test_subcommand_is_case_insensitive(raw: str) -> None:
    service = _FakeSessionService()
    store = _FakeStore()
    msg = FakeMessage()

    await _run(msg, service, store, [raw])

    assert service.calls == [("list", CHAT_ID)]
