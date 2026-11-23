"""Unit tests for the Flet data layer controllers (``flet-data-layer`` P6).

No widgets, no Flet client: the controllers are driven with small structural
fakes (the same style the core's suites use), plus the REAL ``WorkdirService``
for the explorer (it already owns the filesystem test matrix).
"""

from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path
from typing import Any

import pytest

from flet_app.data.explorer import ExplorerController
from flet_app.data.model_selector import ModelSelectorController
from flet_app.data.sidebar import (
    DEFAULT_PROFILE_NAME,
    SidebarController,
    SidebarSnapshot,
)
from flet_app.data.state import (
    Loadable,
    LoadEmpty,
    LoadError,
    LoadIdle,
    LoadLoading,
    LoadReady,
    SidebarFailure,
)
from flet_app.data.status_strip import StatusStripController
from services.agent_sessions import ConversationSummary, InvalidSessionIdError
from services.conversation import ConversationKey
from services.conversations_service import ConversationHistoryError
from services.effective_workdir import WorkdirResolution, WorkdirSource
from services.model_catalog import ModelOption, OpenCodeModelCatalog
from services.session_store import SessionInfo
from services.status_service import StatusSnapshot
from services.workdir_service import (
    DirectoryEntry,
    DirectoryListing,
    WorkdirFailure,
    WorkdirFailureKind,
    WorkdirService,
)
from tests.conversation_keys import conv

_KEY: ConversationKey = conv("flet:local")


# ── Fixtures / fakes ───────────────────────────────────────────────────


def _summary(
    real_id: str,
    title: str = "t",
    *,
    active: bool = False,
    adopted_name: str | None = None,
) -> ConversationSummary:
    return ConversationSummary(
        real_id=real_id,
        title=title,
        created_at=0,
        updated_at=0,
        message_count=0,
        directory="/w",
        agent="opencode",
        is_active=active,
        adopted_name=adopted_name,
    )


def _session(
    real_id: str | None = "ses_abc",
    *,
    name: str = "default",
    title: str | None = None,
) -> SessionInfo:
    return SessionInfo(
        name=name,
        real_id=real_id,
        title=title if title is not None else name,
        created="2026-01-01T00:00:00+00:00",
        last_used=None,
        prompt_count=0,
        is_active=True,
    )


class FakeConversations:
    def __init__(
        self,
        items: list[ConversationSummary] | None = None,
        *,
        list_error: Exception | None = None,
        open_error: Exception | None = None,
    ) -> None:
        self.items = items or []
        self.list_error = list_error
        self.open_error = open_error
        self.list_calls: list[object] = []
        self.open_calls: list[tuple[object, str, str]] = []

    async def list_conversations(self, key: object) -> list[ConversationSummary]:
        self.list_calls.append(key)
        if self.list_error is not None:
            raise self.list_error
        return list(self.items)

    async def open(self, key: object, real_id: str, title: str) -> str:
        self.open_calls.append((key, real_id, title))
        if self.open_error is not None:
            raise self.open_error
        return "default"


class FakeSessionWriter:
    def __init__(self) -> None:
        self.calls: list[object] = []
        self.rename_calls: list[tuple[object, str, str]] = []
        self.rename_result: SessionInfo | None = _session()
        self.rename_error: Exception | None = None

    async def new_chat(self, key: object) -> SessionInfo:
        self.calls.append(key)
        return _session(None)

    async def rename(self, key: object, real_id: str, title: str) -> SessionInfo | None:
        self.rename_calls.append((key, real_id, title))
        if self.rename_error is not None:
            raise self.rename_error
        return self.rename_result


class FakeSessionState:
    def __init__(
        self,
        *,
        model: str = "deepseek/deepseek-flash",
        active: SessionInfo | None = None,
        stored: list[SessionInfo] | None = None,
    ) -> None:
        self.model = model
        self.active = active
        self.stored = list(stored or [])
        self.model_calls: list[object] = []
        self.active_calls: list[object] = []
        self.list_calls: list[object] = []

    async def get_model(self, key: object) -> str:
        self.model_calls.append(key)
        return self.model

    async def get_active_session(self, key: object) -> SessionInfo | None:
        self.active_calls.append(key)
        return self.active

    async def list_sessions(self, key: object) -> list[SessionInfo]:
        self.list_calls.append(key)
        return list(self.stored)


class FakeWorkdir:
    def __init__(self, path: str = "C:/proj") -> None:
        self.path = path
        self.calls: list[object] = []

    async def resolve(self, key: object) -> WorkdirResolution:
        self.calls.append(key)
        return WorkdirResolution(Path(self.path), WorkdirSource.SETTING, False)


def _controller(
    conversations: FakeConversations | None = None,
    *,
    writer: FakeSessionWriter | None = None,
    state: FakeSessionState | None = None,
    workdir: FakeWorkdir | None = None,
    expanded: bool = False,
    profile_name: str = "Ana",
) -> SidebarController:
    return SidebarController(
        conversations if conversations is not None else FakeConversations(),
        writer if writer is not None else FakeSessionWriter(),
        workdir if workdir is not None else FakeWorkdir(),
        state if state is not None else FakeSessionState(),
        _KEY,
        expanded=expanded,
        profile_name=profile_name,
    )


# ── Loadable mixin / typed failure ─────────────────────────────────────


def test_load_controllers_share_the_loadable_mixin() -> None:
    assert issubclass(SidebarController, Loadable)
    assert issubclass(StatusStripController, Loadable)
    assert issubclass(ModelSelectorController, Loadable)


def test_explorer_deliberately_skips_the_loadable_mixin() -> None:
    assert not issubclass(ExplorerController, Loadable)


def test_load_error_carries_a_typed_failure() -> None:
    failure = SidebarFailure(kind="unexpected", detail="boom")

    error = LoadError[None](failure=failure)

    assert error.failure is failure
    assert error.failure.kind == "unexpected"


# ── SidebarSnapshot shape ──────────────────────────────────────────────


def test_sidebar_snapshot_has_exactly_eight_fields() -> None:
    assert [field.name for field in dataclasses.fields(SidebarSnapshot)] == [
        "expanded",
        "sessions",
        "active_real_id",
        "workdir",
        "model",
        "state",
        "error",
        "profile_name",
    ]


def test_sidebar_snapshot_is_frozen() -> None:
    snapshot = _controller().snapshot

    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.__setattr__("expanded", True)


def test_sidebar_initial_snapshot_is_idle() -> None:
    controller = _controller(profile_name="Ana")

    snapshot = controller.snapshot
    assert isinstance(snapshot.state, LoadIdle)
    assert snapshot.sessions == ()
    assert snapshot.expanded is False
    assert snapshot.profile_name == "Ana"
    assert snapshot.error is None


def test_sidebar_uses_a_default_profile_name() -> None:
    controller = SidebarController(
        FakeConversations(),
        FakeSessionWriter(),
        FakeWorkdir(),
        FakeSessionState(),
        _KEY,
        profile_name=None,
    )

    assert controller.snapshot.profile_name != ""


def test_default_profile_name_constant_is_usable() -> None:
    assert DEFAULT_PROFILE_NAME  # non-empty fallback


# ── SidebarController.load ─────────────────────────────────────────────


async def test_load_fills_the_snapshot_and_is_ready() -> None:
    conversations = FakeConversations([_summary("ses_a"), _summary("ses_b")])
    state = FakeSessionState(active=_session("ses_a"))
    controller = _controller(conversations, state=state, workdir=FakeWorkdir("C:/proj"))

    snapshot = await controller.load()

    assert snapshot.state == LoadReady(None)
    assert [item.real_id for item in snapshot.sessions] == ["ses_a", "ses_b"]
    assert snapshot.active_real_id == "ses_a"
    assert snapshot.workdir == str(Path("C:/proj"))
    assert snapshot.model == "deepseek/deepseek-flash"
    assert snapshot.error is None
    assert controller.snapshot is snapshot


async def test_load_without_sessions_is_empty() -> None:
    controller = _controller(FakeConversations([]))

    snapshot = await controller.load()

    assert isinstance(snapshot.state, LoadEmpty)
    assert snapshot.sessions == ()
    assert snapshot.active_real_id is None


async def test_load_transitions_through_loading() -> None:
    observed: list[object] = []

    class _SpyConversations(FakeConversations):
        controller: SidebarController | None = None

        async def list_conversations(self, key: object) -> list[ConversationSummary]:
            observed.append(self.controller.snapshot.state if self.controller else None)
            return await super().list_conversations(key)

    conversations = _SpyConversations([])
    controller = _controller(conversations)
    conversations.controller = controller

    await controller.load()

    assert isinstance(observed[0], LoadLoading)


async def test_load_failure_is_a_typed_error_snapshot() -> None:
    conversations = FakeConversations(list_error=ConversationHistoryError("boom"))
    controller = _controller(conversations)

    snapshot = await controller.load()

    assert isinstance(snapshot.state, LoadError)
    assert snapshot.error is not None
    assert snapshot.error.kind == "conversation_history"
    assert snapshot.error.detail == "boom"


# ── SidebarController.select / new_chat ────────────────────────────────


async def test_select_activates_and_reloads() -> None:
    conversations = FakeConversations([_summary("ses_b", "Beta")])
    controller = _controller(conversations)
    await controller.load()

    snapshot = await controller.select("ses_b")

    assert conversations.open_calls == [(_KEY, "ses_b", "Beta")]
    assert len(conversations.list_calls) == 2
    assert snapshot.state == LoadReady(None)


async def test_select_uses_the_real_id_when_no_title_is_cached() -> None:
    conversations = FakeConversations([])
    controller = _controller(conversations)

    await controller.select("ses_x")

    assert conversations.open_calls == [(_KEY, "ses_x", "ses_x")]


async def test_select_failure_is_a_typed_error_snapshot() -> None:
    conversations = FakeConversations(open_error=InvalidSessionIdError("bad"))
    controller = _controller(conversations)

    snapshot = await controller.select("bad")

    assert isinstance(snapshot.state, LoadError)
    assert snapshot.error is not None
    assert snapshot.error.kind == "invalid_session_id"
    assert conversations.list_calls == []


async def test_new_chat_creates_then_reloads() -> None:
    writer = FakeSessionWriter()
    conversations = FakeConversations([_summary("ses_a")])
    controller = _controller(conversations, writer=writer)

    snapshot = await controller.new_chat()

    assert writer.calls == [_KEY]
    assert len(conversations.list_calls) == 1
    assert snapshot.state == LoadReady(None)


# ── SidebarController.rename + stored-title overlay (EM-02/EM-03) ─────


async def test_rename_persists_and_reloads() -> None:
    writer = FakeSessionWriter()
    conversations = FakeConversations([_summary("ses_a")])
    controller = _controller(conversations, writer=writer)
    await controller.load()

    ok = await controller.rename("ses_a", "Nuevo título")

    assert ok is True
    assert writer.rename_calls == [(_KEY, "ses_a", "Nuevo título")]
    assert len(conversations.list_calls) == 2


async def test_rename_orphan_is_a_silent_false_without_reload() -> None:
    writer = FakeSessionWriter()
    writer.rename_result = None
    conversations = FakeConversations([_summary("ses_a")])
    controller = _controller(conversations, writer=writer)
    await controller.load()

    ok = await controller.rename("ses_orphan", "Nuevo")

    assert ok is False
    assert len(conversations.list_calls) == 1
    assert controller.snapshot.error is None


async def test_rename_failure_publishes_a_typed_error() -> None:
    writer = FakeSessionWriter()
    writer.rename_error = RuntimeError("disk full")
    controller = _controller(writer=writer)

    ok = await controller.rename("ses_a", "Nuevo")

    assert ok is False
    assert isinstance(controller.snapshot.state, LoadError)
    assert controller.snapshot.error is not None
    assert controller.snapshot.error.kind == "unexpected"


async def test_load_overlays_the_stored_title_over_opencode() -> None:
    conversations = FakeConversations(
        [_summary("ses_a", "Título de OpenCode", adopted_name="chat-2")]
    )
    state = FakeSessionState(stored=[_session("ses_a", name="chat-2", title="Mi proyecto")])
    controller = _controller(conversations, state=state)

    snapshot = await controller.load()

    assert snapshot.sessions[0].title == "Mi proyecto"
    assert snapshot.sessions[0].adopted_name == "chat-2"


async def test_load_keeps_the_opencode_title_when_stored_equals_name() -> None:
    conversations = FakeConversations(
        [_summary("ses_a", "Título de OpenCode", adopted_name="chat-1")]
    )
    state = FakeSessionState(stored=[_session("ses_a", name="chat-1", title="chat-1")])
    controller = _controller(conversations, state=state)

    snapshot = await controller.load()

    assert snapshot.sessions[0].title == "Título de OpenCode"


async def test_load_does_not_overlay_orphans() -> None:
    conversations = FakeConversations([_summary("ses_a", "Título de OpenCode")])
    state = FakeSessionState(stored=[_session("ses_a", name="default", title="Renamed")])
    controller = _controller(conversations, state=state)

    snapshot = await controller.load()

    assert snapshot.sessions[0].title == "Título de OpenCode"


async def test_rename_is_visible_after_reload() -> None:
    # The CRITICAL proof: OpenCode's title never changes; only the store does.
    conversations = FakeConversations(
        [_summary("ses_a", "Título de OpenCode", adopted_name="chat-2")]
    )
    state = FakeSessionState(stored=[_session("ses_a", name="chat-2", title="chat-2")])
    writer = FakeSessionWriter()
    writer.rename_result = _session("ses_a", name="chat-2", title="Renombrado")
    controller = _controller(conversations, writer=writer, state=state)

    await controller.load()
    assert controller.snapshot.sessions[0].title == "Título de OpenCode"

    state.stored = [_session("ses_a", name="chat-2", title="Renombrado")]
    ok = await controller.rename("ses_a", "Renombrado")

    assert ok is True
    assert controller.snapshot.sessions[0].title == "Renombrado"


# ── SidebarController.toggle (FD-05) ───────────────────────────────────


def test_toggle_is_synchronous() -> None:
    controller = _controller(expanded=False)

    assert not inspect.iscoroutinefunction(controller.toggle)
    assert not inspect.iscoroutine(controller.toggle)


def test_toggle_flips_the_cached_snapshot() -> None:
    controller = _controller(expanded=False)
    before = controller.snapshot

    after = controller.toggle()

    assert after.expanded is True
    assert controller.snapshot is after
    assert before.expanded is False  # the previous snapshot is untouched


def test_toggle_calls_no_service() -> None:
    conversations = FakeConversations()
    writer = FakeSessionWriter()
    state = FakeSessionState()
    workdir = FakeWorkdir()
    controller = _controller(
        conversations, writer=writer, state=state, workdir=workdir, expanded=False
    )

    controller.toggle()

    assert conversations.list_calls == []
    assert conversations.open_calls == []
    assert writer.calls == []
    assert state.model_calls == []
    assert state.active_calls == []
    assert workdir.calls == []


async def test_toggle_preserves_loaded_data() -> None:
    controller = _controller(FakeConversations([_summary("ses_a")]), expanded=False)
    loaded = await controller.load()

    toggled = controller.toggle()

    assert toggled.expanded is True
    assert toggled.sessions == loaded.sessions
    assert toggled.state == loaded.state


# ── StatusStripController (FD-06) ──────────────────────────────────────


class FakeStatus:
    def __init__(self, snapshot: StatusSnapshot | None = None, *, error: Exception | None = None):
        self.snapshot_value = snapshot if snapshot is not None else _status()
        self.error = error
        self.calls: list[object] = []

    async def snapshot(self, key: object) -> StatusSnapshot:
        self.calls.append(key)
        if self.error is not None:
            raise self.error
        return self.snapshot_value


def _status(workdir: str = "C:/proj") -> StatusSnapshot:
    return StatusSnapshot(
        session_name="default",
        session_real_id="ses_abc",
        model="deepseek/deepseek-flash",
        branch="main",
        workdir=workdir,
        provider="opencode",
        session_count=7,
        created=None,
        last_used=None,
        prompt_count=0,
        uptime_seconds=1,
    )


async def test_status_strip_is_idle_and_does_not_poll() -> None:
    service = FakeStatus()
    controller = StatusStripController(service, _KEY)

    assert isinstance(controller.state, LoadIdle)
    assert service.calls == []


async def test_status_strip_load_returns_the_snapshot_on_demand() -> None:
    service = FakeStatus()
    controller = StatusStripController(service, _KEY)

    value = await controller.load()

    assert value is service.snapshot_value
    assert value.session_count == 7
    assert service.calls == [_KEY]
    assert isinstance(controller.state, LoadReady)


async def test_status_strip_failure_is_typed() -> None:
    service = FakeStatus(error=RuntimeError("nope"))
    controller = StatusStripController(service, _KEY)

    value = await controller.load()

    assert value is None
    assert isinstance(controller.state, LoadError)
    failure = controller.state.failure
    assert isinstance(failure, SidebarFailure)
    assert failure.kind == "unexpected"


# ── ModelSelectorController (FD-07) ────────────────────────────────────


class FakePreferences:
    def __init__(self, model: str = "provider/a") -> None:
        self.model = model
        self.set_calls: list[tuple[object, str]] = []

    async def get_model(self, key: object) -> str:
        return self.model

    async def set_model(self, key: object, model: str) -> None:
        self.model = model
        self.set_calls.append((key, model))


class _StaticSource:
    name = "http"

    def __init__(self, options: list[ModelOption]) -> None:
        self._options = options

    async def fetch(self) -> list[ModelOption]:
        return list(self._options)


def _catalog(options: list[ModelOption], preferences: FakePreferences) -> OpenCodeModelCatalog:
    return OpenCodeModelCatalog([_StaticSource(options)], preferences)


async def test_model_selector_returns_the_grouped_snapshot() -> None:
    preferences = FakePreferences("provider/a")
    options = [
        ModelOption(id="provider/a", provider="Provider", label="A"),
        ModelOption(id="provider/b", provider="Provider", label="B"),
        ModelOption(id="other/c", provider="Other", label="C"),
    ]
    controller = ModelSelectorController(_catalog(options, preferences), preferences, _KEY)

    snapshot = await controller.load()

    assert snapshot is not None
    assert [group.provider for group in snapshot.groups] == ["Provider", "Other"]
    assert [option.id for option in snapshot.options if option.is_default] == ["provider/a"]
    assert snapshot.degraded is False
    assert isinstance(controller.state, LoadReady)


async def test_model_selector_empty_catalog_is_empty_degraded() -> None:
    preferences = FakePreferences()
    controller = ModelSelectorController(_catalog([], preferences), preferences, _KEY)

    snapshot = await controller.load()

    assert snapshot is not None
    assert snapshot.options == ()
    assert snapshot.degraded is True
    assert isinstance(controller.state, LoadEmpty)


async def test_model_selector_select_persists_and_moves_the_default() -> None:
    preferences = FakePreferences("provider/a")
    options = [
        ModelOption(id="provider/a", provider="Provider", label="A"),
        ModelOption(id="provider/b", provider="Provider", label="B"),
    ]
    controller = ModelSelectorController(_catalog(options, preferences), preferences, _KEY)

    snapshot = await controller.select("provider/b")

    assert preferences.set_calls == [(_KEY, "provider/b")]
    assert snapshot is not None
    assert [option.id for option in snapshot.options if option.is_default] == ["provider/b"]


# ── ExplorerController (FD-08) ─────────────────────────────────────────


class FakeWorkdirStore:
    def __init__(self) -> None:
        self.settings: dict[str, Any] = {}
        self.writes: list[tuple[object, str, object]] = []

    async def get_chat_setting(
        self, conversation_key: object, key: str, default: Any = None
    ) -> Any:
        return self.settings.get(key, default)

    async def set_chat_setting(self, conversation_key: object, key: str, value: object) -> None:
        self.settings[key] = value
        self.writes.append((conversation_key, key, value))


def _explorer(tmp_path: Path, store: FakeWorkdirStore | None = None) -> ExplorerController:
    state = store if store is not None else FakeWorkdirStore()
    service = WorkdirService(state, home=tmp_path)
    return ExplorerController(service, _KEY)


def test_explorer_roots_is_home(tmp_path: Path) -> None:
    controller = _explorer(tmp_path)

    assert controller.roots() == (tmp_path,)


async def test_explorer_list_dir_remembers_the_listing(tmp_path: Path) -> None:
    (tmp_path / "proj").mkdir()
    controller = _explorer(tmp_path)

    listing = await controller.list_dir(tmp_path)

    assert isinstance(listing, DirectoryListing)
    assert controller.listing is listing
    assert [entry.name for entry in listing.entries] == ["proj"]


async def test_explorer_list_dir_passes_a_failure_through(tmp_path: Path) -> None:
    controller = _explorer(tmp_path)

    result = await controller.list_dir(tmp_path.parent)

    assert isinstance(result, WorkdirFailure)
    assert result.kind is WorkdirFailureKind.OUTSIDE_ROOT
    assert controller.listing is result


async def test_explorer_create_child_refreshes_the_parent(tmp_path: Path) -> None:
    controller = _explorer(tmp_path)

    result = await controller.create_child(tmp_path, "nuevo")

    assert isinstance(result, DirectoryEntry)
    listing = controller.listing
    assert isinstance(listing, DirectoryListing)
    assert [entry.name for entry in listing.entries] == ["nuevo"]


async def test_explorer_create_child_failure_does_not_refresh(tmp_path: Path) -> None:
    controller = _explorer(tmp_path)

    result = await controller.create_child(tmp_path, "a/b")

    assert isinstance(result, WorkdirFailure)
    assert result.kind is WorkdirFailureKind.INVALID_NAME
    assert controller.listing is None


async def test_explorer_rename_refreshes_the_parent(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    controller = _explorer(tmp_path)

    result = await controller.rename(tmp_path / "a", "b")

    assert isinstance(result, DirectoryEntry)
    assert (tmp_path / "b").is_dir()
    listing = controller.listing
    assert isinstance(listing, DirectoryListing)
    assert [entry.name for entry in listing.entries] == ["b"]


async def test_explorer_delete_refreshes_the_parent(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    controller = _explorer(tmp_path)

    result = await controller.delete(tmp_path / "empty")

    assert isinstance(result, DirectoryEntry)
    assert not (tmp_path / "empty").exists()
    listing = controller.listing
    assert isinstance(listing, DirectoryListing)
    assert listing.entries == ()


async def test_explorer_select_persists_and_refreshes_the_selected_dir(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "file.txt").write_text("x", encoding="utf-8")
    store = FakeWorkdirStore()
    controller = _explorer(tmp_path, store)

    result = await controller.select(project)

    assert isinstance(result, DirectoryEntry)
    assert store.settings["workdir"] == str(project.resolve())
    listing = controller.listing
    assert isinstance(listing, DirectoryListing)
    assert listing.path == project.resolve()
    assert [entry.name for entry in listing.entries] == ["file.txt"]
