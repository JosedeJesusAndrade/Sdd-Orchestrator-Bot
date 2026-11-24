"""Tests for the page-level drawer shell: zones, callbacks and the A3 height."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import cast

import flet as ft

from flet_app.components.drawer import Drawer, build_drawer
from flet_app.core.design_system import DS
from flet_app.data.sidebar import SidebarSnapshot
from flet_app.data.state import (
    LoadEmpty,
    LoadError,
    LoadReady,
    LoadState,
    SidebarFailure,
)
from flet_app.ui_strings import SESSIONS_EMPTY, SESSIONS_ERROR
from services.agent_sessions import ConversationSummary


class FakePage:
    """Minimal ``ft.Page`` stand-in: size plus the calls the drawer needs."""

    def __init__(self, *, width: float = 390, height: float = 844) -> None:
        self.width = width
        self.height = height
        self.update_calls = 0
        self.drawer: object | None = None

    def update(self, *controls: ft.Control) -> None:
        self.update_calls += 1


def _summary(
    *,
    real_id: str = "ses_abc123",
    title: str = "Saludo desde la app Flet",
    message_count: int = 3,
    adopted_name: str | None = "saludo-desde-la-app-flet",
) -> ConversationSummary:
    now_ms = int(time.time() * 1000)
    return ConversationSummary(
        real_id=real_id,
        title=title,
        created_at=now_ms - 60_000,
        updated_at=now_ms - 120_000,
        message_count=message_count,
        directory="C:/repo",
        agent="opencode",
        is_active=False,
        adopted_name=adopted_name,
    )


def _snapshot(
    *summaries: ConversationSummary,
    active: str | None = None,
    state: LoadState[None] | None = None,
    profile_name: str = "marie",
) -> SidebarSnapshot:
    return SidebarSnapshot(
        expanded=True,
        sessions=tuple(summaries),
        active_real_id=active,
        workdir="C:/repo",
        model="deepseek/flash",
        state=state if state is not None else LoadReady(None),
        error=None,
        profile_name=profile_name,
    )


def _walk(control: object) -> Iterator[object]:
    yield control
    content = getattr(control, "content", None)
    if content is not None and not isinstance(content, str):
        yield from _walk(content)
    children = getattr(control, "controls", None)
    if children:
        for child in children:
            yield from _walk(child)


def _texts(control: object) -> list[str]:
    return [c.value for c in _walk(control) if isinstance(c, ft.Text) and c.value is not None]


def _build(snapshot: SidebarSnapshot, page: FakePage | None = None) -> tuple[Drawer, FakePage]:
    page = page or FakePage()
    drawer = build_drawer(
        cast(ft.Page, page),
        snapshot,
        on_toggle=lambda: None,
        on_select=lambda _rid: None,
        on_new_chat=lambda: None,
        on_open_explorer=lambda: None,
        on_open_settings=lambda: None,
    )
    return drawer, page


def test_build_drawer_is_navigation_drawer_at_page_width() -> None:
    drawer, page = _build(_snapshot(_summary()))

    assert isinstance(drawer.control, ft.NavigationDrawer)
    assert drawer.control.width == page.width
    assert drawer.control.controls == [drawer.content]
    assert drawer.control.bgcolor == DS.colors.drawer_bg


def test_drawer_height_is_pinned_to_page_height() -> None:
    """A3: the sized wrapper keeps expand=True + fixed footer from collapsing."""
    drawer, page = _build(_snapshot(_summary()))

    assert drawer.content.height == page.height
    assert drawer.content.height is not None

    drawer.set_height(500)
    assert drawer.content.height == 500

    drawer.set_width(500)
    assert drawer.control.width == 500


# â”€â”€ <100ms guards (SH-05 / FD-05) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def test_drawer_sessions_are_virtualized_in_a_listview() -> None:
    """The session list is an ``ft.ListView`` so it virtualizes (SH-04)."""
    drawer, _ = _build(_snapshot(_summary()))

    assert isinstance(drawer.sessions_view, ft.ListView)


def test_drawer_content_is_prebuilt_at_startup() -> None:
    """The tree is built once; opening only reveals it (no build on open)."""
    drawer, _ = _build(_snapshot(_summary(title="Pre-armada")))

    assert drawer.sessions_view.controls
    assert "Pre-armada" in _texts(drawer.sessions_view)


def test_drawer_render_does_not_flush_per_row() -> None:
    """``render`` mutates controls but never updates: the shell batches once."""
    page = FakePage()
    drawer, page = _build(_snapshot(_summary(real_id="ses_1")), page)

    drawer.render(_snapshot(_summary(real_id="ses_2"), _summary(real_id="ses_3")))

    assert page.update_calls == 0


def test_drawer_follows_a_window_resize() -> None:
    """SH-01/A3: width + height track ``page.width``/``page.height`` on resize."""
    drawer, page = _build(_snapshot(_summary()))

    page.width, page.height = 1280, 720
    drawer.set_width(page.width)
    drawer.set_height(page.height)

    assert drawer.control.width == 1280
    assert drawer.content.height == 720


def test_drawer_shimmer_sweeps_once_per_open_and_rearms_on_dismiss() -> None:
    """W1/D4: the shell drives one sweep on open and re-arms it on dismiss."""
    drawer, _ = _build(_snapshot(_summary()))

    assert drawer.search.played is False
    drawer.begin_shimmer()
    assert drawer.search.played is True
    assert drawer.search.shimmer.animate_offset is not None
    swept = drawer.search.shimmer.offset

    drawer.begin_shimmer()
    assert drawer.search.shimmer.offset == swept

    drawer.end_shimmer()
    assert drawer.search.played is False
    assert drawer.search.shimmer.animate_offset is None


def test_drawer_zone_order_and_24px_gap() -> None:
    drawer, _ = _build(_snapshot(_summary()))

    column = cast(ft.Column, drawer.content.content)
    assert len(column.controls) == 3
    assert column.controls[1] is drawer.sessions_view
    assert column.controls[2] is drawer.footer

    header = cast(ft.Container, column.controls[0])
    header_column = cast(ft.Column, header.content)
    options, gap, search = header_column.controls

    assert isinstance(options, ft.Column)
    assert isinstance(gap, ft.Container)
    assert gap.height == DS.spacing.xl == 24
    assert search is drawer.search.control


def test_drawer_options_wire_new_chat_explorer_and_settings_toast() -> None:
    calls: list[str] = []
    drawer = build_drawer(
        cast(ft.Page, FakePage()),
        _snapshot(_summary()),
        on_toggle=lambda: None,
        on_select=lambda _rid: None,
        on_new_chat=lambda: calls.append("new"),
        on_open_explorer=lambda: calls.append("explorer"),
        on_open_settings=lambda: calls.append("settings"),
    )

    column = cast(ft.Column, drawer.content.content)
    header = cast(ft.Container, column.controls[0])
    header_column = cast(ft.Column, header.content)
    options = cast(ft.Column, header_column.controls[0])
    new_chat, explorer, settings = options.controls

    assert _texts(new_chat) == ["Nuevo chat"]
    assert _texts(explorer) == ["Explorador de directorios"]
    assert _texts(settings) == ["Ajustes"]
    # W5: Ajustes keeps the muted "disabled" look but its tap must NOT be silent.
    settings_label = next(
        c for c in _walk(settings) if isinstance(c, ft.Text) and c.value == "Ajustes"
    )
    assert settings_label.color == DS.colors.text_muted
    assert cast(ft.Container, settings).on_click is not None

    cast(Callable[[object], None], cast(ft.Container, new_chat).on_click)(None)
    cast(Callable[[object], None], cast(ft.Container, explorer).on_click)(None)
    cast(Callable[[object], None], cast(ft.Container, settings).on_click)(None)
    assert calls == ["new", "explorer", "settings"]


def test_drawer_settings_is_inert_without_a_callback() -> None:
    calls: list[str] = []
    drawer = build_drawer(
        cast(ft.Page, FakePage()),
        _snapshot(_summary()),
        on_toggle=lambda: None,
        on_select=lambda _rid: None,
        on_new_chat=lambda: calls.append("new"),
        on_open_explorer=lambda: calls.append("explorer"),
        on_open_settings=None,
    )

    column = cast(ft.Column, drawer.content.content)
    header = cast(ft.Container, column.controls[0])
    options = cast(ft.Column, cast(ft.Column, header.content).controls[0])
    _, _, settings = options.controls

    assert cast(ft.Container, settings).on_click is None
    assert cast(ft.Container, settings).tooltip == "Próximamente"


def test_drawer_dismiss_calls_toggle_once() -> None:
    toggles: list[str] = []
    drawer = build_drawer(
        cast(ft.Page, FakePage()),
        _snapshot(_summary()),
        on_toggle=lambda: toggles.append("t"),
        on_select=lambda _rid: None,
        on_new_chat=lambda: None,
        on_open_explorer=lambda: None,
        on_open_settings=lambda: None,
    )

    cast(Callable[[object], None], drawer.control.on_dismiss)(None)
    assert toggles == ["t"]


def test_drawer_session_tap_selects_the_real_id() -> None:
    selected: list[str] = []
    session = _summary()
    drawer = build_drawer(
        cast(ft.Page, FakePage()),
        _snapshot(session, active=session.real_id),
        on_toggle=lambda: None,
        on_select=selected.append,
        on_new_chat=lambda: None,
        on_open_explorer=lambda: None,
        on_open_settings=lambda: None,
    )

    tile = next(
        c for c in _walk(drawer.sessions_view) if isinstance(c, ft.Container) and c.data is session
    )
    assert tile.bgcolor == DS.colors.surface
    cast(Callable[[object], None], tile.on_click)(None)
    assert selected == [session.real_id]


def test_drawer_render_swaps_the_session_list_and_footer() -> None:
    first = _summary(real_id="ses_1", title="Primera")
    second = _summary(real_id="ses_2", title="Segunda")
    drawer, _ = _build(_snapshot(first))

    assert "Primera" in _texts(drawer.sessions_view)

    drawer.render(_snapshot(second, profile_name="ana"))

    assert "Segunda" in _texts(drawer.sessions_view)
    assert "Primera" not in _texts(drawer.sessions_view)
    assert "ana" in _texts(drawer.footer)


def test_drawer_empty_and_error_states() -> None:
    empty, _ = _build(_snapshot(state=LoadEmpty()))
    assert SESSIONS_EMPTY in _texts(empty.sessions_view)

    failed, _ = _build(_snapshot(state=LoadError(SidebarFailure(kind="workdir"))))
    assert SESSIONS_ERROR in _texts(failed.sessions_view)


def test_drawer_edit_mode_renders_fields_for_bot_bound_rows() -> None:
    drawer, _ = _build(_snapshot(_summary()))

    assert not any(isinstance(c, ft.TextField) for c in _walk(drawer.sessions_view))
    drawer.set_edit_mode(True)
    assert any(isinstance(c, ft.TextField) for c in _walk(drawer.sessions_view))
    drawer.set_edit_mode(False)
    assert not any(isinstance(c, ft.TextField) for c in _walk(drawer.sessions_view))


def test_drawer_edit_mode_leaves_orphans_read_only() -> None:
    bound = _summary(real_id="ses_1", title="Con nombre", adopted_name="chat-1")
    orphan = _summary(real_id="ses_2", title="Sin nombre", adopted_name=None)
    drawer, _ = _build(_snapshot(bound, orphan))

    drawer.set_edit_mode(True)

    fields = [c for c in _walk(drawer.sessions_view) if isinstance(c, ft.TextField)]
    assert len(fields) == 1, "only the bot-bound row becomes editable (EM-02)"
    assert set(drawer.edit_refs) == {"ses_1"}
    assert "Sin nombre" in _texts(drawer.sessions_view)

    drawer.set_edit_mode(False)
    assert drawer.edit_refs == {}


def test_drawer_collect_edits_skips_blank_and_unchanged_rows() -> None:
    first = _summary(real_id="ses_1", title="Primera")
    second = _summary(real_id="ses_2", title="Segunda")
    drawer, _ = _build(_snapshot(first, second))

    drawer.set_edit_mode(True)
    assert set(drawer.edit_refs) == {"ses_1", "ses_2"}

    first_field = drawer.edit_refs["ses_1"].current
    second_field = drawer.edit_refs["ses_2"].current
    assert first_field is not None
    assert second_field is not None
    first_field.value = "  Primera editada  "
    second_field.value = "   "

    assert drawer.collect_edits() == [("ses_1", "Primera editada")]

    first_field.value = "Primera"
    assert drawer.collect_edits() == []
