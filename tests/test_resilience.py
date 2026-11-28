"""Resilience tests: the app must never be stuck/partial/busy after a restart.

Failure scenarios covered:
  1. disconnect with a running prompt  -> the server turn is aborted;
  2. restart/re-open with a busy server -> the stale turn is aborted and the
     real history is loaded;
  3. reconnect                          -> the real history replaces a static
     partial snapshot;
  4. sending while busy                 -> a clear UX notice, never a freeze.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import flet as ft

from flet_app.flet_chat_view import render_history
from flet_app.resilience import BUSY_NOTICE, DisconnectGuard, busy_notice
from services.agent_sessions import Message
from services.prompt_service import PromptService, ReconcileResult
from services.session_store import SessionInfo
from tests.conversation_keys import conv

SID = "ses_resilience_1"


def _session(real_id: str | None = SID) -> SessionInfo:
    return SessionInfo(
        name="default",
        real_id=real_id,
        title="default",
        created="",
        last_used=None,
        prompt_count=0,
    )


class _Store:
    def __init__(self, active: SessionInfo | None) -> None:
        self.active = active

    async def get_active_session(self, conversation_id: Any) -> SessionInfo | None:
        return self.active


class _Workdir:
    async def resolve(self, conversation_id: Any) -> Any:
        return SimpleNamespace(path=Path(r"C:\project"))


class _Client:
    """Scripted ``OpenCodeServerClient`` double (only the resilience surface)."""

    def __init__(self, *, status: str | None = None, abort_ok: bool = True) -> None:
        self.status = status
        self.abort_ok = abort_ok
        self.aborted: list[str] = []
        self.status_queries: list[str] = []
        self.history: list[dict[str, Any]] = []

    async def get_session_status(self, session_id: str, *, directory: Any = None) -> str | None:
        self.status_queries.append(session_id)
        return self.status

    async def abort(self, session_id: str, *, directory: Any = None) -> bool:
        self.aborted.append(session_id)
        return self.abort_ok

    async def get_messages(
        self, session_id: str, limit: int = 50, *, directory: Any = None
    ) -> list[dict[str, Any]]:
        return self.history


class _Backend:
    def __init__(self) -> None:
        self.cancelled = 0

    def cancel(self) -> None:
        self.cancelled += 1


def _service(client: _Client, *, active: SessionInfo | None = None) -> PromptService:
    resolved = _session() if active is None else active
    return PromptService(
        session_store=_Store(resolved),  # type: ignore[arg-type]
        provider_factory=object(),  # type: ignore[arg-type]
        effective_workdir=_Workdir(),  # type: ignore[arg-type]
        server_client=client,  # type: ignore[arg-type]
    )


# 1. disconnect with a running prompt -> abort called -----------------------


async def test_disconnect_with_running_prompt_aborts_the_server_turn() -> None:
    client = _Client(status="busy")
    service = _service(client)
    key = conv(1)
    backend = _Backend()
    # Simulate an in-flight prompt owned by this process.
    service._running[key] = asyncio.current_task()  # type: ignore[assignment]
    service._backends[key] = backend  # type: ignore[assignment]

    handled = await service.abort_on_disconnect(key)

    assert handled is True
    assert backend.cancelled == 1
    assert client.aborted == [SID]
    # The server-abort clears the cached busy flag; ``_running`` is cleared by
    # the execute() finally when the cancelled task unwinds.
    assert service._server_busy.get(key) is False


# 2. restart/re-open with a busy server -> reconcile + real history --------


async def test_reconcile_aborts_stale_busy_turn_and_loads_real_history() -> None:
    client = _Client(status="busy")
    client.history = [
        {
            "info": {"id": "m1", "role": "user", "time": {"created": 1}},
            "parts": [{"type": "text", "text": "hola"}],
        },
        {
            "info": {"id": "m2", "role": "assistant", "time": {"created": 2}},
            "parts": [{"type": "text", "text": "respuesta real"}],
        },
    ]
    service = _service(client)
    key = conv(1)

    result = await service.reconcile(key)

    assert result.aborted_stale is True
    assert result.busy is False
    assert client.aborted == [SID]
    assert service.is_running(key) is False

    messages = await service.load_server_history(key)

    assert messages is not None
    assert [(m.role, m.text) for m in messages] == [
        ("user", "hola"),
        ("assistant", "respuesta real"),
    ]


async def test_reconcile_keeps_busy_when_the_abort_fails() -> None:
    # F2: server busy, no local task -> abort attempted but the abort FAILS.
    # The server is still busy, so the state must stay busy (never clear it and
    # let the next send queue behind the orphan) and the send must show a notice.
    client = _Client(status="busy", abort_ok=False)
    service = _service(client)
    key = conv(1)

    result = await service.reconcile(key)

    assert result.aborted_stale is False
    assert result.server_status == "busy"
    assert client.aborted == [SID]
    assert service.is_running(key) is True
    assert busy_notice(result) == BUSY_NOTICE


async def test_reconcile_keeps_a_live_local_turn_untouched() -> None:
    client = _Client(status="busy")
    service = _service(client)
    key = conv(1)
    service._running[key] = asyncio.current_task()  # type: ignore[assignment]

    result = await service.reconcile(key)

    assert result.busy is True
    assert result.aborted_stale is False
    assert client.aborted == []


async def test_reconcile_with_idle_server_does_not_abort() -> None:
    client = _Client(status="idle")
    service = _service(client)

    result = await service.reconcile(conv(1))

    assert result.busy is False
    assert client.aborted == []


# 3. reconnect -> real history replaces a static partial --------------------


def test_render_history_replaces_a_stale_partial() -> None:
    partial = ft.Text("RESPUESTA PARCIAL CONGELADA")
    column = ft.Column(controls=[partial])

    rendered = render_history(
        column,
        [
            Message(id="m1", role="user", text="hola", timestamp=1),
            Message(id="m2", role="assistant", text="respuesta real", timestamp=2),
        ],
    )

    assert rendered == 2
    assert partial not in column.controls
    assert len(column.controls) == 2


# 4. sending while busy -> clear UX, never a freeze -------------------------


def test_busy_state_yields_a_clear_notice_not_a_freeze() -> None:
    assert busy_notice(ReconcileResult(busy=True)) == BUSY_NOTICE
    assert busy_notice(ReconcileResult(busy=False)) is None
    assert "sigue trabajando" in BUSY_NOTICE


# 5. transient blip vs real close -> only the close aborts (F5) -------------


async def test_disconnect_blip_does_not_abort_the_turn() -> None:
    aborts: list[int] = []

    async def abort() -> bool:
        aborts.append(1)
        return True

    guard = DisconnectGuard(abort, grace_s=0.05)

    await guard.on_disconnect()
    assert guard.armed is True
    # A quick reconnect (the Wi-Fi came back) disarms the pending abort.
    guard.disarm()
    await asyncio.sleep(0.1)

    assert aborts == []
    assert guard.armed is False


async def test_close_aborts_immediately_even_after_a_disconnect() -> None:
    aborts: list[int] = []

    async def abort() -> bool:
        aborts.append(1)
        return True

    guard = DisconnectGuard(abort, grace_s=10.0)

    await guard.on_disconnect()
    closed = await guard.on_close()

    assert closed is True
    assert aborts == [1]
    # The pending grace timer was cancelled: no second (orphan) abort later.
    await asyncio.sleep(0.02)
    assert aborts == [1]


async def test_disconnect_without_reconnect_aborts_after_grace() -> None:
    aborts: list[int] = []

    async def abort() -> bool:
        aborts.append(1)
        return True

    guard = DisconnectGuard(abort, grace_s=0.02)

    await guard.on_disconnect()
    assert aborts == []
    await asyncio.sleep(0.06)

    # No reconnect within the grace window -> a real disconnect -> abort.
    assert aborts == [1]
    assert guard.armed is False
