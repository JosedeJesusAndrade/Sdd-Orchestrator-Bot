"""Tests for the explorer screen (P5): breadcrumb, list, CRUD and select.

Two layers, mirroring the split the refactor introduced:

* the VIEW tests drive ``ExplorerScreen`` with fake data + recording callbacks and
  prove it never touches the controller;
* the PRESENTER tests drive ``ExplorerPresenter`` (from ``main``) over a real
  ``ExplorerController`` and pin the async orchestration (list/select/CRUD +
  post-mutation refresh).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any, cast

import flet as ft

from flet_app.core.navigation import ScreenHost
from flet_app.data.explorer import ExplorerController
from flet_app.main import ExplorerPresenter
from flet_app.screens.explorer_screen import ExplorerScreen, build_explorer_screen
from flet_app.ui_strings import (
    EXPLORER_RECENT,
    NON_GIT_CANCEL,
    NON_GIT_CONTINUE,
    workdir_failure_copy,
)
from services.conversation import ConversationKey
from services.workdir_failures import WorkdirFailure, WorkdirFailureKind
from services.workdir_service import (
    DirectoryEntry,
    DirectoryListing,
    WorkdirService,
)
from tests.conversation_keys import conv

_VIEW_SOURCE = Path(__file__).resolve().parents[1] / "flet_app" / "screens" / "explorer_screen.py"
_KEY: ConversationKey = conv("flet:local")


# ── Fakes ──────────────────────────────────────────────────────────────


class FakePage:
    """Minimal ``ft.Page`` stand-in: dialogs, scheduled tasks and updates."""

    def __init__(self) -> None:
        self.update_calls = 0
        self.dialogs: list[ft.AlertDialog] = []
        self.tasks: list[tuple[Callable[..., Any], tuple[object, ...]]] = []

    def update(self, *controls: ft.Control) -> None:
        self.update_calls += 1

    def show_dialog(self, dialog: ft.AlertDialog) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self) -> ft.AlertDialog | None:
        return self.dialogs.pop() if self.dialogs else None

    def run_task(self, handler: Callable[..., Any], *args: object) -> None:
        self.tasks.append((handler, args))

    async def drain(self) -> None:
        while self.tasks:
            handler, args = self.tasks.pop(0)
            await handler(*args)


class Calls:
    """Records every intent the pure view emits."""

    def __init__(self) -> None:
        self.back: list[bool] = []
        self.enter: list[Path] = []
        self.select: list[Path] = []
        self.create: list[tuple[str, bool]] = []
        self.rename: list[tuple[Path, str]] = []
        self.delete: list[Path] = []
        self.retry: list[bool] = []


class FakeWorkdirStore:
    """In-memory ``WorkdirStore`` double for the presenter integration tests."""

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


class FakeView:
    """Recording ``ExplorerView`` for the presenter tests."""

    def __init__(self) -> None:
        self.path: Path | None = None
        self.listings: list[DirectoryListing] = []
        self.recents: list[tuple[Path, ...]] = []
        self.errors: list[WorkdirFailure] = []
        self.loading = 0
        self.loading_states: list[bool] = []

    def render(self, listing: DirectoryListing, recent: tuple[Path, ...] = ()) -> None:
        self.listings.append(listing)
        self.recents.append(tuple(recent))
        self.path = listing.path

    def set_error(self, failure: WorkdirFailure) -> None:
        self.errors.append(failure)

    def set_loading(self, loading: bool = True) -> None:
        self.loading += 1 if loading else 0
        self.loading_states.append(loading)


# ── Helpers ────────────────────────────────────────────────────────────


def _walk(control: object) -> Iterator[object]:
    yield control
    for attr in ("content", "title"):
        child = getattr(control, attr, None)
        if isinstance(child, ft.Control):
            yield from _walk(child)
    for attr in ("controls", "actions"):
        children = getattr(control, attr, None)
        if children:
            for child in children:
                yield from _walk(child)


def _texts(control: object) -> list[str]:
    return [c.value for c in _walk(control) if isinstance(c, ft.Text) and c.value is not None]


def _entry(
    name: str,
    path: Path,
    *,
    is_dir: bool = True,
    is_git: bool = False,
    is_symlink: bool = False,
) -> DirectoryEntry:
    return DirectoryEntry(
        name=name,
        path=path,
        is_dir=is_dir,
        is_git=is_git,
        is_symlink=is_symlink,
    )


def _listing(
    path: Path,
    *,
    entries: tuple[DirectoryEntry, ...] = (),
    ancestors: tuple[Path, ...] | None = None,
    is_git: bool = False,
) -> DirectoryListing:
    resolved = path.resolve()
    return DirectoryListing(
        path=resolved,
        parent=resolved.parent if resolved.parent != resolved else None,
        entries=entries,
        ancestors=ancestors if ancestors is not None else (resolved,),
        is_git=is_git,
        is_writable=True,
    )


def _build(page: FakePage | None = None) -> tuple[ExplorerScreen, FakePage, Calls]:
    state = page if page is not None else FakePage()
    calls = Calls()
    screen = build_explorer_screen(
        cast(ft.Page, state),
        on_back=lambda: calls.back.append(True),
        on_select=calls.select.append,
        on_enter=calls.enter.append,
        on_create=lambda name, init_git: calls.create.append((name, init_git)),
        on_rename=lambda path, new: calls.rename.append((path, new)),
        on_delete=calls.delete.append,
        on_retry=lambda: calls.retry.append(True),
    )
    return screen, state, calls


def _crumb_targets(screen: ExplorerScreen) -> list[object]:
    return [c.data for c in _walk(screen.breadcrumb) if isinstance(c, ft.TextButton)]


def _button(control: object, *, icon: object) -> ft.IconButton:
    return next(c for c in _walk(control) if isinstance(c, ft.IconButton) and c.icon == icon)


def _entry_row(screen: ExplorerScreen, name: str) -> ft.Container:
    return next(
        c
        for c in _walk(screen.entries_view)
        if isinstance(c, ft.Container)
        and isinstance(c.data, DirectoryEntry)
        and c.data.name == name
    )


def _recent_rows(screen: ExplorerScreen) -> list[ft.Container]:
    return [
        c
        for c in _walk(screen.recent_view)
        if isinstance(c, ft.Container) and isinstance(c.data, Path)
    ]


async def _noop_navigate() -> None:
    """Inert post-select navigation hook for presenter tests."""


def _make_presenter(
    tmp_path: Path,
    *,
    on_selected: Callable[[], Awaitable[None]] | None = None,
) -> tuple[ExplorerPresenter, FakeView, FakeWorkdirStore]:
    store = FakeWorkdirStore()
    controller = ExplorerController(WorkdirService(store, home=tmp_path), _KEY)
    view = FakeView()
    presenter = ExplorerPresenter(
        controller,
        view,
        on_selected=on_selected if on_selected is not None else _noop_navigate,
    )
    return presenter, view, store


# ── Pure view: breadcrumb + flat list (EX-01) ──────────────────────────


def test_breadcrumb_matches_listing_ancestors(tmp_path: Path) -> None:
    child = (tmp_path / "proyecto").resolve()
    screen, _, _ = _build()

    screen.render(_listing(child, ancestors=(tmp_path.resolve(), child)))

    assert _crumb_targets(screen) == [tmp_path.resolve(), child]


def test_list_renders_entries_with_git_badge(tmp_path: Path) -> None:
    listing = _listing(
        tmp_path,
        entries=(
            _entry("plain", tmp_path / "plain"),
            _entry("repo", tmp_path / "repo", is_git=True),
        ),
    )
    screen, _, _ = _build()

    screen.render(listing)

    texts = _texts(screen.entries_view)
    assert "plain" in texts
    assert "repo" in texts
    assert "git" in texts


def test_list_marks_a_symlink_entry(tmp_path: Path) -> None:
    listing = _listing(
        tmp_path,
        entries=(_entry("link", tmp_path / "link", is_symlink=True),),
    )
    screen, _, _ = _build()

    screen.render(listing)

    icons = [c.icon for c in _walk(screen.entries_view) if isinstance(c, ft.Icon)]
    assert ft.Icons.LINK in icons


def test_empty_directory_shows_the_empty_copy(tmp_path: Path) -> None:
    screen, _, _ = _build()

    screen.render(_listing(tmp_path))

    assert "Esta carpeta está vacía." in _texts(screen.entries_view)


def test_tapping_a_directory_asks_to_enter_it(tmp_path: Path) -> None:
    child = (tmp_path / "proyecto").resolve()
    screen, page, calls = _build()
    screen.render(_listing(tmp_path, entries=(_entry("proyecto", child),)))

    cast(Callable[[object], None], _entry_row(screen, "proyecto").on_click)(None)

    assert calls.enter == [child]
    assert page.tasks == []


def test_tapping_a_crumb_asks_to_enter_it(tmp_path: Path) -> None:
    child = (tmp_path / "proyecto").resolve()
    screen, _, calls = _build()
    screen.render(_listing(child, ancestors=(tmp_path.resolve(), child)))

    crumb = next(c for c in _walk(screen.breadcrumb) if isinstance(c, ft.TextButton))
    cast(Callable[[object], None], crumb.on_click)(None)

    assert calls.enter == [tmp_path.resolve()]


# ── Pure view: browse vs select (D3 / EX-03) ───────────────────────────


def test_cta_selects_a_git_directory(tmp_path: Path) -> None:
    repo = (tmp_path / "repo").resolve()
    screen, page, calls = _build()
    screen.render(_listing(repo, is_git=True))

    assert screen.use_button.disabled is False

    cast(Callable[[object], None], screen.use_button.on_click)(None)

    assert calls.select == [repo]
    assert page.tasks == []


def test_non_git_confirm_cancel_emits_nothing(tmp_path: Path) -> None:
    plain = (tmp_path / "plain").resolve()
    screen, page, calls = _build()
    screen.render(_listing(plain, is_git=False))

    cast(Callable[[object], None], screen.use_button.on_click)(None)
    assert len(page.dialogs) == 1
    cancel = next(
        c
        for c in _walk(page.dialogs[0])
        if isinstance(c, ft.TextButton) and c.content == NON_GIT_CANCEL
    )
    cast(Callable[[object], None], cancel.on_click)(None)

    assert page.dialogs == []
    assert calls.select == []


def test_non_git_confirm_continue_asks_to_select(tmp_path: Path) -> None:
    plain = (tmp_path / "plain").resolve()
    screen, page, calls = _build()
    screen.render(_listing(plain, is_git=False))

    cast(Callable[[object], None], screen.use_button.on_click)(None)
    cont = next(
        c
        for c in _walk(page.dialogs[0])
        if isinstance(c, ft.FilledButton) and c.content == NON_GIT_CONTINUE
    )
    cast(Callable[[object], None], cont.on_click)(None)

    assert calls.select == [plain]


# ── Pure view: recent shortcuts (top level) ────────────────────────────


def test_recent_section_is_hidden_when_empty(tmp_path: Path) -> None:
    screen, _, _ = _build()

    screen.render(_listing(tmp_path.resolve()))

    assert screen.recent_view.visible is False
    assert screen.recent == ()


def test_recent_section_lists_the_paths_newest_first(tmp_path: Path) -> None:
    first = (tmp_path / "uno").resolve()
    second = (tmp_path / "dos").resolve()
    screen, _, _ = _build()

    screen.render(_listing(tmp_path.resolve()), (first, second))

    assert screen.recent_view.visible is True
    assert screen.recent == (first, second)
    assert EXPLORER_RECENT in _texts(screen.recent_view)
    assert [row.data for row in _recent_rows(screen)] == [first, second]


def test_tapping_a_recent_selects_it(tmp_path: Path) -> None:
    target = (tmp_path / "proyecto").resolve()
    screen, page, calls = _build()
    screen.render(_listing(tmp_path.resolve()), (target,))

    [row] = _recent_rows(screen)
    cast(Callable[[object], None], row.on_click)(None)

    assert calls.select == [target]
    assert calls.enter == []
    assert page.tasks == []


def test_rendering_a_subdirectory_hides_the_recent_section(tmp_path: Path) -> None:
    target = (tmp_path / "proyecto").resolve()
    screen, _, _ = _build()
    screen.render(_listing(tmp_path.resolve()), (target,))

    screen.render(_listing(target))

    assert screen.recent_view.visible is False
    assert screen.recent == ()


# ── Pure view: typed failure copy (EX-04) ──────────────────────────────


def test_failure_renders_copy_by_kind(tmp_path: Path) -> None:
    screen, _, _ = _build()

    screen.set_error(WorkdirFailure(kind=WorkdirFailureKind.OUTSIDE_ROOT, path=tmp_path.parent))

    assert screen.failure is not None
    assert screen.failure.kind is WorkdirFailureKind.OUTSIDE_ROOT
    assert screen.error_holder.visible is True
    assert screen.error_text.value == workdir_failure_copy(WorkdirFailureKind.OUTSIDE_ROOT)


def test_failure_keeps_the_last_listing_browsable(tmp_path: Path) -> None:
    screen, _, _ = _build()
    screen.render(_listing(tmp_path.resolve()))

    screen.set_error(WorkdirFailure(kind=WorkdirFailureKind.OUTSIDE_ROOT, path=tmp_path.parent))

    assert screen.path == tmp_path.resolve()
    assert screen.use_button.disabled is False


def test_loading_disables_the_actions_until_render(tmp_path: Path) -> None:
    screen, _, _ = _build()
    screen.render(_listing(tmp_path.resolve()))

    screen.set_loading()

    assert screen.use_button.disabled is True
    assert screen.new_button.disabled is True

    screen.render(_listing(tmp_path.resolve()))

    assert screen.use_button.disabled is False


# ── Pure view: CRUD dialogs (EX-02) ────────────────────────────────────


def test_new_folder_dialog_asks_to_create(tmp_path: Path) -> None:
    screen, page, calls = _build()
    screen.render(_listing(tmp_path.resolve()))

    cast(Callable[[object], None], screen.new_button.on_click)(None)
    dialog = page.dialogs[-1]
    field = next(c for c in _walk(dialog) if isinstance(c, ft.TextField))
    assert any(isinstance(c, ft.Checkbox) for c in _walk(dialog))
    field.value = "nueva"
    create = next(c for c in _walk(dialog) if isinstance(c, ft.FilledButton))
    cast(Callable[[object], None], create.on_click)(None)

    assert calls.create == [("nueva", False)]


def test_new_folder_dialog_forwards_init_git(tmp_path: Path) -> None:
    screen, page, calls = _build()
    screen.render(_listing(tmp_path.resolve()))

    cast(Callable[[object], None], screen.new_button.on_click)(None)
    dialog = page.dialogs[-1]
    field = next(c for c in _walk(dialog) if isinstance(c, ft.TextField))
    checkbox = next(c for c in _walk(dialog) if isinstance(c, ft.Checkbox))
    field.value = "nueva"
    checkbox.value = True
    create = next(c for c in _walk(dialog) if isinstance(c, ft.FilledButton))
    cast(Callable[[object], None], create.on_click)(None)

    assert calls.create == [("nueva", True)]


def test_rename_dialog_asks_to_rename(tmp_path: Path) -> None:
    target = (tmp_path / "viejo").resolve()
    screen, page, calls = _build()
    screen.render(_listing(tmp_path, entries=(_entry("viejo", target),)))

    cast(Callable[[object], None], _button(screen.entries_view, icon=ft.Icons.EDIT).on_click)(None)
    dialog = page.dialogs[-1]
    field = next(c for c in _walk(dialog) if isinstance(c, ft.TextField))
    assert field.value == "viejo"
    field.value = "nuevo"
    save = next(c for c in _walk(dialog) if isinstance(c, ft.FilledButton))
    cast(Callable[[object], None], save.on_click)(None)

    assert calls.rename == [(target, "nuevo")]


def test_rename_dialog_ignores_an_unchanged_name(tmp_path: Path) -> None:
    target = (tmp_path / "viejo").resolve()
    screen, page, calls = _build()
    screen.render(_listing(tmp_path, entries=(_entry("viejo", target),)))

    cast(Callable[[object], None], _button(screen.entries_view, icon=ft.Icons.EDIT).on_click)(None)
    save = next(c for c in _walk(page.dialogs[-1]) if isinstance(c, ft.FilledButton))
    cast(Callable[[object], None], save.on_click)(None)

    assert calls.rename == []


def test_delete_confirm_asks_to_delete(tmp_path: Path) -> None:
    target = (tmp_path / "basura").resolve()
    screen, page, calls = _build()
    screen.render(_listing(tmp_path, entries=(_entry("basura", target),)))

    cast(
        Callable[[object], None],
        _button(screen.entries_view, icon=ft.Icons.DELETE_OUTLINE).on_click,
    )(None)
    confirm = next(c for c in _walk(page.dialogs[-1]) if isinstance(c, ft.FilledButton))
    cast(Callable[[object], None], confirm.on_click)(None)

    assert calls.delete == [target]


# ── Pure view: retry / back ────────────────────────────────────────────


def test_retry_button_emits_retry() -> None:
    screen, _, calls = _build()
    retry = next(
        c
        for c in _walk(screen.error_holder)
        if isinstance(c, ft.TextButton) and c.icon == ft.Icons.REFRESH
    )

    cast(Callable[[object], None], retry.on_click)(None)

    assert calls.retry == [True]


def test_back_invokes_the_callback() -> None:
    screen, _, calls = _build()

    cast(Callable[[object], None], _button(screen.control, icon=ft.Icons.ARROW_BACK).on_click)(None)

    assert calls.back == [True]


def test_screen_source_has_no_controller_dependency() -> None:
    source = _VIEW_SOURCE.read_text(encoding="utf-8")

    assert "ExplorerController" not in source
    assert "await self._controller" not in source
    assert "await controller" not in source


# ── Presenter wiring (main): list / select / CRUD / retry ──────────────


async def test_presenter_open_lists_the_first_root(tmp_path: Path) -> None:
    presenter, view, _ = _make_presenter(tmp_path)

    await presenter.open()

    assert view.loading == 1
    assert view.errors == []
    assert view.listings[-1].path == tmp_path.resolve()


async def test_presenter_create_refreshes_the_listing(tmp_path: Path) -> None:
    presenter, view, _ = _make_presenter(tmp_path)
    await presenter.open()

    await presenter.create("nueva", False)

    assert (tmp_path / "nueva").is_dir()
    assert "nueva" in [entry.name for entry in view.listings[-1].entries]


async def test_presenter_rename_refreshes_the_listing(tmp_path: Path) -> None:
    (tmp_path / "viejo").mkdir()
    presenter, view, _ = _make_presenter(tmp_path)
    await presenter.open()

    await presenter.rename(tmp_path / "viejo", "nuevo")

    assert (tmp_path / "nuevo").is_dir()
    assert not (tmp_path / "viejo").exists()
    assert "nuevo" in [entry.name for entry in view.listings[-1].entries]


async def test_presenter_delete_refreshes_the_listing(tmp_path: Path) -> None:
    (tmp_path / "basura").mkdir()
    presenter, view, _ = _make_presenter(tmp_path)
    await presenter.open()

    await presenter.delete(tmp_path / "basura")

    assert not (tmp_path / "basura").exists()
    assert "basura" not in [entry.name for entry in view.listings[-1].entries]


async def test_presenter_select_persists_and_notifies(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    selected: list[str] = []

    async def on_selected() -> None:
        selected.append("ok")

    presenter, view, store = _make_presenter(tmp_path, on_selected=on_selected)
    await presenter.load(repo)

    await presenter.select(repo)

    assert store.settings["workdir"] == str(repo.resolve())
    assert selected == ["ok"]
    assert view.listings[-1].path == repo.resolve()


async def test_presenter_select_navigates_back_after_success(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    navigations: list[str] = []

    async def navigate() -> None:
        navigations.append("chat")

    presenter, _, _ = _make_presenter(tmp_path, on_selected=navigate)
    await presenter.load(repo)

    await presenter.select(repo)

    assert navigations == ["chat"]


async def test_presenter_select_failure_does_not_navigate(tmp_path: Path) -> None:
    navigations: list[str] = []

    async def navigate() -> None:
        navigations.append("chat")

    presenter, view, _ = _make_presenter(tmp_path, on_selected=navigate)
    await presenter.open()

    await presenter.select(tmp_path.parent)

    assert view.errors[-1].kind is WorkdirFailureKind.OUTSIDE_ROOT
    assert navigations == []


async def test_presenter_load_clears_loading_on_success_and_failure(tmp_path: Path) -> None:
    presenter, view, _ = _make_presenter(tmp_path)

    await presenter.load(tmp_path)

    assert view.loading_states[-1] is False

    await presenter.load(tmp_path.parent)

    assert view.errors[-1].kind is WorkdirFailureKind.OUTSIDE_ROOT
    assert view.loading_states[-1] is False


async def test_presenter_select_clears_loading_on_every_terminal_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    presenter, view, _ = _make_presenter(tmp_path)
    await presenter.load(repo)
    view.loading_states.clear()

    await presenter.select(repo)

    assert view.loading_states == [True, False]
    assert view.loading_states[-1] is False

    view.loading_states.clear()

    await presenter.select(tmp_path.parent)

    assert view.errors[-1].kind is WorkdirFailureKind.OUTSIDE_ROOT
    assert view.loading_states == [True, False]


async def test_two_consecutive_list_selects_keep_the_cta_enabled(tmp_path: Path) -> None:
    first = tmp_path / "primero"
    first.mkdir()
    (first / ".git").mkdir()
    second = tmp_path / "segundo"
    second.mkdir()
    (second / ".git").mkdir()
    state = FakePage()
    store = FakeWorkdirStore()
    controller = ExplorerController(WorkdirService(store, home=tmp_path), _KEY)
    navigations: list[str] = []

    async def navigate() -> None:
        navigations.append("chat")

    screen = build_explorer_screen(
        cast(ft.Page, state),
        on_back=lambda: None,
        on_select=lambda path: None,
        on_enter=lambda path: None,
        on_create=lambda name, init_git: None,
        on_rename=lambda path, new_name: None,
        on_delete=lambda path: None,
        on_retry=lambda: None,
    )
    presenter = ExplorerPresenter(controller, screen, on_selected=navigate)

    # 1st list select: browse the folder (set_loading) then use the CTA.
    await presenter.load(first)
    assert screen.listing is not None
    assert screen.use_button.disabled is False

    await presenter.select(first)
    assert screen.use_button.disabled is False
    assert navigations == ["chat"]
    assert store.settings["workdir"] == str(first.resolve())

    # Back in the chat, reopen and repeat: the 2nd list select must not block.
    await presenter.load(second)
    assert screen.use_button.disabled is False

    await presenter.select(second)
    assert screen.use_button.disabled is False
    assert navigations == ["chat", "chat"]
    assert store.settings["workdir"] == str(second.resolve())


async def test_non_git_continue_returns_to_chat(tmp_path: Path) -> None:
    plain = (tmp_path / "plain").resolve()
    plain.mkdir()
    state = FakePage()
    chat_control = ft.Container(ft.Text("chat"))
    host = ScreenHost(cast(ft.Page, state), ft.Container(ft.Text("explorer")))

    async def navigate() -> None:
        await host.show(chat_control)

    presenter, _, _ = _make_presenter(tmp_path, on_selected=navigate)
    screen = build_explorer_screen(
        cast(ft.Page, state),
        on_back=lambda: None,
        on_select=lambda path: state.run_task(presenter.select, path),
        on_enter=lambda path: None,
        on_create=lambda name, init_git: None,
        on_rename=lambda path, new_name: None,
        on_delete=lambda path: None,
        on_retry=lambda: None,
    )
    screen.render(_listing(plain, is_git=False))

    cast(Callable[[object], None], screen.use_button.on_click)(None)
    cont = next(
        c
        for c in _walk(state.dialogs[0])
        if isinstance(c, ft.FilledButton) and c.content == NON_GIT_CONTINUE
    )
    cast(Callable[[object], None], cont.on_click)(None)
    await state.drain()

    assert host.control.content is chat_control


async def test_non_git_cancel_does_not_navigate(tmp_path: Path) -> None:
    plain = (tmp_path / "plain").resolve()
    plain.mkdir()
    state = FakePage()
    chat_control = ft.Container(ft.Text("chat"))
    host = ScreenHost(cast(ft.Page, state), ft.Container(ft.Text("explorer")))

    async def navigate() -> None:
        await host.show(chat_control)

    presenter, _, _ = _make_presenter(tmp_path, on_selected=navigate)
    screen = build_explorer_screen(
        cast(ft.Page, state),
        on_back=lambda: None,
        on_select=lambda path: state.run_task(presenter.select, path),
        on_enter=lambda path: None,
        on_create=lambda name, init_git: None,
        on_rename=lambda path, new_name: None,
        on_delete=lambda path: None,
        on_retry=lambda: None,
    )
    screen.render(_listing(plain, is_git=False))

    cast(Callable[[object], None], screen.use_button.on_click)(None)
    cancel = next(
        c
        for c in _walk(state.dialogs[0])
        if isinstance(c, ft.TextButton) and c.content == NON_GIT_CANCEL
    )
    cast(Callable[[object], None], cancel.on_click)(None)
    await state.drain()

    assert host.control.content is not chat_control


async def test_presenter_failure_renders_and_retry_reloads(tmp_path: Path) -> None:
    presenter, view, _ = _make_presenter(tmp_path)
    await presenter.open()

    await presenter.load(tmp_path.parent)

    assert view.errors[-1].kind is WorkdirFailureKind.OUTSIDE_ROOT

    await presenter.retry()

    assert len(view.errors) == 2
    assert view.loading >= 3


async def test_presenter_feeds_recent_folders_at_the_root(tmp_path: Path) -> None:
    target = tmp_path / "proyecto"
    target.mkdir()
    presenter, view, store = _make_presenter(tmp_path)
    store.settings["recent_workdirs"] = [str(target)]

    await presenter.open()

    assert view.recents[-1] == (target,)


async def test_presenter_omits_recent_folders_inside_a_subdirectory(tmp_path: Path) -> None:
    target = tmp_path / "proyecto"
    target.mkdir()
    presenter, view, store = _make_presenter(tmp_path)
    store.settings["recent_workdirs"] = [str(target)]

    await presenter.load(target)

    assert view.recents[-1] == ()
