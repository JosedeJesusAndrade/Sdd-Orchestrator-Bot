"""Tests for the explorer screen (P5): breadcrumb, list, CRUD and select.

Drives the real ``ExplorerController`` over a temporary ``home``; only ``ft.Page``
is faked (dialogs + scheduled tasks) so no Flet client is needed. The screen is
the VIEW: its behavior is pinned here while the controller/DTOs keep their own
suites.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any, cast

import flet as ft

from flet_app.data.explorer import ExplorerController
from flet_app.screens.explorer_screen import ExplorerScreen, build_explorer_screen
from flet_app.ui_strings import NON_GIT_CANCEL, NON_GIT_CONTINUE, workdir_failure_copy
from services.conversation import ConversationKey
from services.workdir_failures import WorkdirFailureKind
from services.workdir_service import DirectoryEntry, DirectoryListing, WorkdirService
from tests.conversation_keys import conv

_KEY: ConversationKey = conv("flet:local")


# ── Fakes ──────────────────────────────────────────────────────────────


class FakeWorkdirStore:
    """In-memory ``WorkdirStore`` double (mirrors the controller suite's fake)."""

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


class FakePage:
    """Minimal ``ft.Page`` stand-in: dialogs, scheduled tasks and updates."""

    def __init__(self, *, width: float = 390, height: float = 844) -> None:
        self.width = width
        self.height = height
        self.update_calls = 0
        self.dialogs: list[ft.AlertDialog] = []
        self.tasks: list[tuple[Callable[..., Awaitable[None]], tuple[object, ...]]] = []

    def update(self, *controls: ft.Control) -> None:
        self.update_calls += 1

    def show_dialog(self, dialog: ft.AlertDialog) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self) -> ft.AlertDialog | None:
        return self.dialogs.pop() if self.dialogs else None

    def run_task(self, handler: Callable[..., Awaitable[None]], *args: object) -> None:
        self.tasks.append((handler, args))

    async def drain(self) -> None:
        while self.tasks:
            handler, args = self.tasks.pop(0)
            await handler(*args)


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


async def _make(
    tmp_path: Path,
    *,
    on_select: Callable[[], None] | None = None,
    on_back: Callable[[], None] | None = None,
) -> tuple[ExplorerScreen, FakePage, FakeWorkdirStore, ExplorerController]:
    store = FakeWorkdirStore()
    controller = ExplorerController(WorkdirService(store, home=tmp_path), _KEY)
    page = FakePage()
    screen = build_explorer_screen(
        cast(ft.Page, page),
        controller,
        on_back=on_back if on_back is not None else (lambda: None),
        on_select=on_select if on_select is not None else (lambda: None),
    )
    return screen, page, store, controller


def _crumb_targets(screen: ExplorerScreen) -> list[object]:
    return [c.data for c in _walk(screen.breadcrumb) if isinstance(c, ft.TextButton)]


# ── Breadcrumb + flat list (EX-01) ─────────────────────────────────────


async def test_breadcrumb_matches_listing_ancestors(tmp_path: Path) -> None:
    child = tmp_path / "proyecto"
    child.mkdir()
    screen, _, _, _ = await _make(tmp_path)

    await screen.load(child)

    assert _crumb_targets(screen) == [tmp_path.resolve(), child.resolve()]


async def test_list_renders_entries_with_git_badge(tmp_path: Path) -> None:
    (tmp_path / "plain").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    screen, _, _, _ = await _make(tmp_path)

    await screen.load(tmp_path)

    texts = _texts(screen.entries_view)
    assert "plain" in texts
    assert "repo" in texts
    assert "git" in texts


async def test_list_marks_a_symlink_entry(tmp_path: Path) -> None:
    screen, _, _, _ = await _make(tmp_path)
    listing = DirectoryListing(
        path=tmp_path,
        parent=None,
        entries=(
            DirectoryEntry(
                name="link",
                path=tmp_path / "link",
                is_dir=True,
                is_git=False,
                is_symlink=True,
            ),
        ),
        ancestors=(tmp_path.resolve(),),
        is_git=False,
        is_writable=True,
    )

    screen.render(listing)

    icons = [c.icon for c in _walk(screen.entries_view) if isinstance(c, ft.Icon)]
    assert ft.Icons.LINK in icons


async def test_empty_directory_shows_the_empty_copy(tmp_path: Path) -> None:
    screen, _, _, _ = await _make(tmp_path)

    await screen.load(tmp_path)

    assert "Esta carpeta está vacía." in _texts(screen.entries_view)


async def test_tapping_a_directory_enters_it(tmp_path: Path) -> None:
    child = tmp_path / "proyecto"
    child.mkdir()
    screen, page, _, _ = await _make(tmp_path)
    await screen.load(tmp_path)

    row = next(
        c
        for c in _walk(screen.entries_view)
        if isinstance(c, ft.Container)
        and isinstance(c.data, DirectoryEntry)
        and c.data.name == "proyecto"
    )
    cast(Callable[[object], None], row.on_click)(None)
    await page.drain()

    assert screen.path == child.resolve()
    assert child.resolve() in _crumb_targets(screen)


# ── Browse vs select (D3 / EX-03) ──────────────────────────────────────


async def test_cta_selects_a_git_directory_and_notifies(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    selected: list[str] = []
    screen, page, store, _ = await _make(tmp_path, on_select=lambda: selected.append("ok"))
    await screen.load(repo)

    assert screen.listing is not None
    assert screen.listing.is_git is True
    assert screen.use_button.disabled is False

    cast(Callable[[object], None], screen.use_button.on_click)(None)
    await page.drain()

    assert selected == ["ok"]
    assert store.settings["workdir"] == str(repo.resolve())


async def test_non_git_confirm_cancel_persists_nothing(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    screen, page, store, _ = await _make(tmp_path)
    await screen.load(plain)

    assert screen.listing is not None
    assert screen.listing.is_git is False

    cast(Callable[[object], None], screen.use_button.on_click)(None)
    assert len(page.dialogs) == 1
    cancel = next(
        c
        for c in _walk(page.dialogs[0])
        if isinstance(c, ft.TextButton) and c.content == NON_GIT_CANCEL
    )
    cast(Callable[[object], None], cancel.on_click)(None)
    await page.drain()

    assert page.dialogs == []
    assert store.writes == []
    assert "workdir" not in store.settings


async def test_non_git_confirm_continue_persists(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    selected: list[str] = []
    screen, page, store, _ = await _make(tmp_path, on_select=lambda: selected.append("ok"))
    await screen.load(plain)

    cast(Callable[[object], None], screen.use_button.on_click)(None)
    cont = next(
        c
        for c in _walk(page.dialogs[0])
        if isinstance(c, ft.FilledButton) and c.content == NON_GIT_CONTINUE
    )
    cast(Callable[[object], None], cont.on_click)(None)
    await page.drain()

    assert store.settings["workdir"] == str(plain.resolve())
    assert selected == ["ok"]


# ── Typed failure copy (EX-04) ─────────────────────────────────────────


async def test_failure_renders_copy_by_kind(tmp_path: Path) -> None:
    screen, _, _, _ = await _make(tmp_path)

    await screen.load(tmp_path.parent)

    assert screen.failure is not None
    assert screen.failure.kind is WorkdirFailureKind.OUTSIDE_ROOT
    assert screen.error_holder.visible is True
    assert screen.error_text.value == workdir_failure_copy(WorkdirFailureKind.OUTSIDE_ROOT)


async def test_failure_keeps_the_last_listing_browsable(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    screen, _, _, _ = await _make(tmp_path)
    await screen.load(tmp_path)

    await screen.load(tmp_path.parent)

    assert screen.path == tmp_path.resolve()
    assert screen.use_button.disabled is False


# ── CRUD dialogs (EX-02) ───────────────────────────────────────────────


async def test_new_folder_dialog_creates_a_directory(tmp_path: Path) -> None:
    screen, page, _, _ = await _make(tmp_path)
    await screen.load(tmp_path)

    cast(Callable[[object], None], screen.new_button.on_click)(None)
    dialog = page.dialogs[-1]
    assert any(isinstance(c, ft.TextField) for c in _walk(dialog))
    assert any(isinstance(c, ft.Checkbox) for c in _walk(dialog))

    field = next(c for c in _walk(dialog) if isinstance(c, ft.TextField))
    field.value = "nueva"
    create = next(c for c in _walk(dialog) if isinstance(c, ft.FilledButton))
    cast(Callable[[object], None], create.on_click)(None)
    await page.drain()

    assert (tmp_path / "nueva").is_dir()
    assert "nueva" in _texts(screen.entries_view)


async def test_rename_dialog_renames_the_directory(tmp_path: Path) -> None:
    (tmp_path / "viejo").mkdir()
    screen, page, _, _ = await _make(tmp_path)
    await screen.load(tmp_path)

    rename_button = next(
        c
        for c in _walk(screen.entries_view)
        if isinstance(c, ft.IconButton) and c.icon == ft.Icons.EDIT
    )
    cast(Callable[[object], None], rename_button.on_click)(None)
    dialog = page.dialogs[-1]
    field = next(c for c in _walk(dialog) if isinstance(c, ft.TextField))
    assert field.value == "viejo"

    field.value = "nuevo"
    save = next(c for c in _walk(dialog) if isinstance(c, ft.FilledButton))
    cast(Callable[[object], None], save.on_click)(None)
    await page.drain()

    assert (tmp_path / "nuevo").is_dir()
    assert not (tmp_path / "viejo").exists()
    assert "nuevo" in _texts(screen.entries_view)


async def test_delete_confirm_deletes_the_directory(tmp_path: Path) -> None:
    (tmp_path / "basura").mkdir()
    screen, page, _, _ = await _make(tmp_path)
    await screen.load(tmp_path)

    delete_button = next(
        c
        for c in _walk(screen.entries_view)
        if isinstance(c, ft.IconButton) and c.icon == ft.Icons.DELETE_OUTLINE
    )
    cast(Callable[[object], None], delete_button.on_click)(None)
    confirm = next(c for c in _walk(page.dialogs[-1]) if isinstance(c, ft.FilledButton))
    cast(Callable[[object], None], confirm.on_click)(None)
    await page.drain()

    assert not (tmp_path / "basura").exists()
    assert "basura" not in _texts(screen.entries_view)


# ── Back to chat ───────────────────────────────────────────────────────


async def test_back_invokes_the_callback(tmp_path: Path) -> None:
    calls: list[str] = []
    screen, _, _, _ = await _make(tmp_path, on_back=lambda: calls.append("back"))

    back = next(
        c
        for c in _walk(screen.control)
        if isinstance(c, ft.IconButton) and c.icon == ft.Icons.ARROW_BACK
    )
    cast(Callable[[object], None], back.on_click)(None)

    assert calls == ["back"]
