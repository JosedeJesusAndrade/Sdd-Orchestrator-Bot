"""Parser + dispatch tests for the typed ``/session`` command model.

Covers core-isolation item 9: every sub-command parses into a
``SessionCommand``, missing arguments become ``SessionUsage``, and
``SessionService.dispatch`` returns the matching outcome without changing the
underlying service calls.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from services.session_commands import (
    SessionAdopted,
    SessionCommand,
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
from services.session_service import SessionDetails, SessionListSnapshot, SessionService
from services.session_store import SessionInfo, SessionStore
from tests.conversation_keys import conv

# ── Parser table ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["new"], SessionCommand(SessionCommandKind.NEW)),
        (["new", "sess-1"], SessionCommand(SessionCommandKind.NEW, name="sess-1")),
        (["list"], SessionCommand(SessionCommandKind.LIST)),
        (["switch", "other"], SessionCommand(SessionCommandKind.SWITCH, name="other")),
        (["delete", "other"], SessionCommand(SessionCommandKind.DELETE, name="other")),
        (["info", "other"], SessionCommand(SessionCommandKind.INFO, name="other")),
        (["discover"], SessionCommand(SessionCommandKind.DISCOVER)),
        (
            ["adopt", "ses_1", "friendly"],
            SessionCommand(SessionCommandKind.ADOPT, name="friendly", real_id="ses_1"),
        ),
        (["adopt", "ses_1"], SessionCommand(SessionCommandKind.ADOPT, real_id="ses_1")),
    ],
)
def test_parse_known_subcommands(args: list[str], expected: SessionCommand) -> None:
    assert parse_session_command(args) == expected


@pytest.mark.parametrize("raw", ["LIST", "List", "lIsT"])
def test_parser_is_case_insensitive(raw: str) -> None:
    assert parse_session_command([raw]) == SessionCommand(SessionCommandKind.LIST)


def test_parse_empty_args_is_bare_usage() -> None:
    assert parse_session_command([]) == SessionUsage()


def test_parse_unknown_subcommand_carries_the_raw_value() -> None:
    assert parse_session_command(["frobnicate"]) == SessionUsage(
        kind=SessionCommandKind.UNKNOWN, raw="frobnicate"
    )


# ── Dispatch outcomes ──────────────────────────────────────────────────


def _info(
    name: str = "default", real_id: str | None = "ses_x", *, is_active: bool = False
) -> SessionInfo:
    return SessionInfo(
        name=name,
        real_id=real_id,
        title=name,
        created="2026-05-19T00:00:00",
        last_used=None,
        prompt_count=0,
        is_active=is_active,
    )


class FakeStore:
    def __init__(self, sessions: list[SessionInfo] | None = None) -> None:
        self.sessions = list(sessions or [])
        self.created: list[tuple[Any, str]] = []
        self.switched: list[tuple[Any, str]] = []
        self.deleted: list[tuple[Any, str]] = []
        self.delete_real_id: str | None = None

    async def list_sessions(self, conversation_id: Any) -> list[SessionInfo]:
        return list(self.sessions)

    async def get_active_session(self, conversation_id: Any) -> SessionInfo | None:
        return next((s for s in self.sessions if s.is_active), None)

    async def create_session(self, conversation_id: Any, name: str) -> SessionInfo:
        info = _info(name)
        self.sessions.append(info)
        self.created.append((conversation_id, name))
        return info

    async def switch_session(self, conversation_id: Any, name: str) -> SessionInfo:
        self.switched.append((conversation_id, name))
        return next(s for s in self.sessions if s.name == name)

    async def delete_session(self, conversation_id: Any, name: str) -> str | None:
        self.deleted.append((conversation_id, name))
        return self.delete_real_id

    async def update_session_id(self, conversation_id: Any, real_id: str) -> None:
        pass

    async def update_session_title(self, conversation_id: Any, name: str, title: str) -> None:
        pass


class _InfoService(SessionService):
    """Real service with a stubbed ``enrich`` (no OpenCode DB access)."""

    def __init__(self, store: FakeStore, enrichment: dict[str, Any] | None = None) -> None:
        super().__init__(cast(SessionStore, store))
        self.enrichment = dict(enrichment or {})
        self.enriched: list[str | None] = []

    async def enrich(self, real_id: str | None) -> dict[str, Any]:
        self.enriched.append(real_id)
        return dict(self.enrichment)


def _service(store: FakeStore) -> SessionService:
    return SessionService(cast(SessionStore, store))


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("services.session_service.invalidate_opencode_sessions_cache", lambda: None)

    async def empty_fetch() -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr("services.session_service.fetch_opencode_sessions", empty_fetch)


async def test_dispatch_new_creates_and_returns_the_session() -> None:
    store = FakeStore()

    outcome = await _service(store).dispatch(conv(42), SessionCommand(SessionCommandKind.NEW, "s1"))

    assert isinstance(outcome, SessionCreated)
    assert outcome.session.name == "s1"
    assert store.created == [(conv(42), "s1")]


async def test_dispatch_new_without_name_is_usage() -> None:
    store = FakeStore()

    outcome = await _service(store).dispatch(conv(42), SessionCommand(SessionCommandKind.NEW))

    assert outcome == SessionUsage(kind=SessionCommandKind.NEW)
    assert store.created == []


async def test_dispatch_list_returns_the_snapshot() -> None:
    store = FakeStore([_info("mine", "ses_1")])

    outcome = await _service(store).dispatch(conv(42), SessionCommand(SessionCommandKind.LIST))

    assert isinstance(outcome, SessionListed)
    assert isinstance(outcome.snapshot, SessionListSnapshot)
    assert [s.name for s in outcome.snapshot.sessions] == ["mine"]


async def test_dispatch_switch_returns_the_session() -> None:
    store = FakeStore([_info("mine", "ses_1"), _info("other", "ses_2")])

    outcome = await _service(store).dispatch(
        conv(42), SessionCommand(SessionCommandKind.SWITCH, "other")
    )

    assert isinstance(outcome, SessionSwitched)
    assert outcome.session.name == "other"
    assert store.switched == [(conv(42), "other")]


async def test_dispatch_switch_without_name_is_usage() -> None:
    store = FakeStore([_info("mine", "ses_1")])

    outcome = await _service(store).dispatch(conv(42), SessionCommand(SessionCommandKind.SWITCH))

    assert outcome == SessionUsage(kind=SessionCommandKind.SWITCH)
    assert store.switched == []


async def test_dispatch_delete_returns_the_deleted_name_and_id() -> None:
    store = FakeStore([_info("mine", "ses_1")])
    store.delete_real_id = "ses_1"

    outcome = await _service(store).dispatch(
        conv(42), SessionCommand(SessionCommandKind.DELETE, "mine")
    )

    assert outcome == SessionDeleted(name="mine", real_id="ses_1")
    assert store.deleted == [(conv(42), "mine")]


async def test_dispatch_delete_without_name_is_usage() -> None:
    store = FakeStore([_info("mine", "ses_1")])

    outcome = await _service(store).dispatch(conv(42), SessionCommand(SessionCommandKind.DELETE))

    assert outcome == SessionUsage(kind=SessionCommandKind.DELETE)
    assert store.deleted == []


async def test_dispatch_info_returns_raw_details() -> None:
    store = FakeStore([_info("mine", "ses_1")])
    service = _InfoService(store, {"message_count": 3})

    outcome = await service.dispatch(conv(42), SessionCommand(SessionCommandKind.INFO, "mine"))

    assert isinstance(outcome, SessionInfoResult)
    assert outcome.details == SessionDetails(
        name="mine",
        real_id="ses_1",
        title="mine",
        created="2026-05-19T00:00:00",
        last_used=None,
        prompt_count=0,
        enrichment={"message_count": 3},
    )
    assert service.enriched == ["ses_1"]


async def test_dispatch_discover_returns_the_sessions() -> None:
    store = FakeStore()

    outcome = await _service(store).dispatch(conv(42), SessionCommand(SessionCommandKind.DISCOVER))

    assert isinstance(outcome, SessionDiscovered)
    assert outcome.sessions == []


async def test_dispatch_adopt_returns_the_adopted_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FakeStore()

    async def fetch() -> list[dict[str, Any]]:
        return [{"id": "ses_abc", "title": "Adopted"}]

    monkeypatch.setattr("services.session_service.fetch_opencode_sessions", fetch)

    outcome = await _service(store).dispatch(
        conv(42), SessionCommand(SessionCommandKind.ADOPT, "friendly", "ses_abc")
    )

    assert outcome == SessionAdopted(name="friendly", real_id="ses_abc")
    assert store.created == [(conv(42), "friendly")]


async def test_dispatch_adopt_without_arguments_is_usage() -> None:
    store = FakeStore()

    outcome = await _service(store).dispatch(
        conv(42), SessionCommand(SessionCommandKind.ADOPT, real_id="ses_abc")
    )

    assert outcome == SessionUsage(kind=SessionCommandKind.ADOPT)
    assert store.created == []


async def test_dispatch_unknown_is_usage() -> None:
    store = FakeStore()

    outcome = await _service(store).dispatch(
        conv(42), SessionCommand(SessionCommandKind.UNKNOWN, raw="frobnicate")
    )

    assert outcome == SessionUsage(kind=SessionCommandKind.UNKNOWN, raw="frobnicate")


# ── get_info resolution ────────────────────────────────────────────────


async def test_get_info_uses_the_explicit_name() -> None:
    store = FakeStore([_info("other", "ses_123")])
    service = _InfoService(store, {"model": "deepseek/flash"})

    details = await service.get_info(conv(42), "other")

    assert details.name == "other"
    assert details.real_id == "ses_123"
    assert details.enrichment == {"model": "deepseek/flash"}
    assert service.enriched == ["ses_123"]


async def test_get_info_falls_back_to_the_active_session() -> None:
    active = _info("active-one", "ses_a", is_active=True)
    store = FakeStore([_info("other"), active])

    details = await _InfoService(store).get_info(conv(42))

    assert details.name == "active-one"


async def test_get_info_falls_back_to_default() -> None:
    store = FakeStore([_info("default")])

    details = await _InfoService(store).get_info(conv(42))

    assert details.name == "default"


async def test_get_info_missing_raises_with_the_resolved_name() -> None:
    from services.session_store import SessionNotFoundError

    store = FakeStore([])

    with pytest.raises(SessionNotFoundError) as exc:
        await _InfoService(store).get_info(conv(42))

    assert str(exc.value) == "default"
