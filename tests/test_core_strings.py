"""Item 4 proof: the core formats, the view owns the copy.

A sentinel ``CoreStrings`` drives every terminal path of ``PromptService``; if
the core ever hardcoded a user-facing literal, the sentinel would not appear.
A source scan pins the same rule for the modules that used to hold the copy.
"""

from __future__ import annotations

from pathlib import Path

from services.ai_backend import AIBackendResult
from services.chat_view import CoreStrings
from services.prompt_service import PromptService
from services.session_store import SessionInfo
from tests.conversation_keys import conv
from tests.test_chat_view import FakeBackend, FakeChatView, FakeFactory, FakeStore

SENTINEL = CoreStrings(
    processing="__PROCESSING__",
    processing_elapsed="__ELAPSED__{elapsed}",
    cancelled="__CANCELLED__",
    completed="__COMPLETED__",
    completed_no_output="__NO_OUTPUT__",
    error="__ERROR__{error}",
    error_code="__ERROR_CODE__{code}",
    error_code_no_output="__ERROR_CODE_NO_OUTPUT__{code}",
    delivery_failed="__DELIVERY_FAILED__",
    permission_not_accepted="__PERM_NOT_ACCEPTED__",
    permission_not_accepted_notice="__PERM_NOTICE__",
    new_session="__NEW_SESSION__{name}__",
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

_UI_MARKERS = (
    "\u23f3",
    "\u23f9\ufe0f",
    "\u2705",
    "\u274c",
    "\u26a0\ufe0f",
    "\U0001f195",
    "procesando",
    "Cancelado",
    "Completado",
    "No se pudo entregar",
    "Nueva sesi\u00f3n",
)


class _ExistingSessionStore(FakeStore):
    """Store with a real session (no new-session header) for exact output."""

    async def get_active_session(self, conversation_id) -> SessionInfo:
        return SessionInfo(
            name="default",
            real_id="ses_existing",
            title="default",
            created="",
            last_used=None,
            prompt_count=0,
        )


async def test_processing_and_completion_use_sentinel_strings(monkeypatch) -> None:
    monkeypatch.setattr("services.prompt_service.PROGRESS_UPDATE_INTERVAL", 0.01)

    view = FakeChatView(strings=SENTINEL)
    backend = FakeBackend(
        AIBackendResult(stdout="body", returncode=0),
        gate=view.first_update,
    )
    service = PromptService(
        session_store=_ExistingSessionStore(),
        provider_factory=FakeFactory(backend),
    )

    await service.execute(conversation_id=conv(1), prompt_text="hi", view=view)

    assert view.started == ["__PROCESSING__"]
    handle = view.handles[0]
    assert handle.updates[0].startswith("__ELAPSED__")
    assert handle.finished == ["__COMPLETED__"]
    assert handle.errors == []
    assert view.sent == ["body"]


async def test_cancelled_uses_sentinel_string() -> None:
    view = FakeChatView(strings=SENTINEL)
    backend = FakeBackend(AIBackendResult(stdout="must not run", returncode=0))
    service = PromptService(session_store=FakeStore(), provider_factory=FakeFactory(backend))
    service._cancel.add(conv(7))

    await service.execute(conversation_id=conv(7), prompt_text="x", view=view)

    assert view.handles[0].errors == ["__CANCELLED__"]


async def test_error_uses_sentinel_template() -> None:
    view = FakeChatView(strings=SENTINEL)
    backend = FakeBackend(AIBackendResult(stdout="", stderr="boom", returncode=1))
    service = PromptService(session_store=FakeStore(), provider_factory=FakeFactory(backend))

    await service.execute(conversation_id=conv(1), prompt_text="x", view=view)

    error_text = view.handles[0].errors[0]
    assert error_text.startswith("__ERROR__")
    assert "boom" in error_text


async def test_completed_without_output_uses_sentinel_string() -> None:
    view = FakeChatView(strings=SENTINEL)
    backend = FakeBackend(AIBackendResult(stdout="", returncode=0))
    service = PromptService(
        session_store=_ExistingSessionStore(),
        provider_factory=FakeFactory(backend),
    )

    await service.execute(conversation_id=conv(1), prompt_text="x", view=view)

    assert view.sent == ["__NO_OUTPUT__"]


async def test_delivery_failure_uses_sentinel_string() -> None:
    view = FakeChatView(deliver=False, strings=SENTINEL)
    backend = FakeBackend(AIBackendResult(stdout="content", returncode=0))
    service = PromptService(
        session_store=_ExistingSessionStore(),
        provider_factory=FakeFactory(backend),
    )

    await service.execute(conversation_id=conv(1), prompt_text="x", view=view)

    assert view.handles[0].errors == ["__DELIVERY_FAILED__"]


async def test_unapplied_permission_uses_sentinel_strings() -> None:
    view = FakeChatView(strings=SENTINEL)
    backend = FakeBackend(
        AIBackendResult(stdout="body", returncode=0, permission_reply_not_accepted=True)
    )
    service = PromptService(
        session_store=_ExistingSessionStore(),
        provider_factory=FakeFactory(backend),
    )

    await service.execute(conversation_id=conv(1), prompt_text="x", view=view)

    assert view.sent == ["body\n\n__PERM_NOTICE__"]
    assert view.handles[0].finished == []
    assert view.handles[0].errors == ["__PERM_NOT_ACCEPTED__"]


async def test_new_session_header_uses_sentinel_template() -> None:
    view = FakeChatView(strings=SENTINEL)
    backend = FakeBackend(AIBackendResult(stdout="body", returncode=0))
    service = PromptService(session_store=FakeStore(), provider_factory=FakeFactory(backend))

    await service.execute(conversation_id=conv(1), prompt_text="x", view=view)

    assert view.sent == ["__NEW_SESSION__default__body"]


def test_prompt_service_has_no_ui_copy_literals() -> None:
    source = (_PROJECT_ROOT / "services" / "prompt_service.py").read_text(encoding="utf-8")

    offenders = [marker for marker in _UI_MARKERS if marker in source]
    assert offenders == [], f"PromptService still hardcodes UI copy: {offenders}"


def test_permissions_module_owns_no_ui_notice() -> None:
    source = (_PROJECT_ROOT / "services" / "permissions.py").read_text(encoding="utf-8")

    assert "PERMISSION_REPLY_NOT_ACCEPTED_NOTICE" not in source
    assert "\u26a0\ufe0f" not in source
