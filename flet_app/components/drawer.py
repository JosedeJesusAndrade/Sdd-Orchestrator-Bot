"""Page-level ``NavigationDrawer`` body: four zones + a fixed footer (SH-03/04).

Layout, top to bottom:

1. options (``Nuevo chat`` / ``Explorador de directorios`` / disabled ``Ajustes``)
2. an exact 24px pure gap
3. the search placeholder (single-pass shimmer)
4. the date-bucketed, virtualized session list (``ft.ListView``, scrolls)
5. a fixed footer (avatar initial + profile name + overflow dots)

Pure presentational: every action is a callback; no service is called here.

A3 spike — drawer height
------------------------
``NavigationDrawer.controls`` are laid out by Flutter inside a vertical
``ListView`` (unbounded main-axis). A single child with ``expand=True`` therefore
has no height to expand into and collapses (the fixed footer would vanish). The
chosen fallback is a sized wrapper: ``content.height`` is pinned to
``page.height`` at build time and kept in sync through :meth:`Drawer.set_height`
on ``on_resize``. That gives the inner session ``ListView`` a definite height,
so ``expand=True`` works and the footer stays pinned. The wrapper does **not**
rely on ``expand=True`` inside the native drawer.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import flet as ft

from flet_app.components.footer_profile import footer_profile
from flet_app.components.search_placeholder import SearchPlaceholder, search_placeholder
from flet_app.components.section_header import section_header
from flet_app.components.sidebar_tile import session_tile
from flet_app.core.design_system import DS
from flet_app.core.session_buckets import bucket_sessions
from flet_app.data.sidebar import SidebarSnapshot
from flet_app.data.state import LoadError
from flet_app.ui_strings import (
    OPTION_EXPLORER,
    OPTION_NEW_CHAT,
    OPTION_SETTINGS,
    SESSIONS_EMPTY,
    SESSIONS_ERROR,
    TOAST_COMING_SOON,
)

_FALLBACK_WIDTH = 360.0
_FALLBACK_HEIGHT = 720.0


def _dimension(page: ft.Page, name: str, fallback: float) -> float:
    """Read ``page.<name>`` as a float, falling back when unset/None."""
    value = getattr(page, name, fallback)
    return float(value) if value is not None else fallback


def _now_ms() -> int:
    return int(time.time() * 1000)


def _option_row(
    icon: ft.IconData,
    label: str,
    on_tap: Callable[[], None] | None,
) -> ft.Control:
    """One drawer option. ``on_tap=None`` renders an inert, muted row."""
    enabled = on_tap is not None
    color = DS.colors.text_primary if enabled else DS.colors.text_muted

    def handle_tap(_: ft.Event[ft.Container]) -> None:
        if on_tap is not None:
            on_tap()

    return ft.Container(
        content=ft.Row(
            controls=[
                ft.Icon(icon, size=DS.typography.label_size, color=color),
                ft.Text(
                    value=label,
                    size=DS.typography.label_size,
                    color=color,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    expand=True,
                ),
            ],
            spacing=DS.spacing.md,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(
            horizontal=DS.spacing.md,
            vertical=DS.spacing.sm,
        ),
        border_radius=DS.radii.tile,
        ink=enabled,
        on_click=handle_tap if enabled else None,
        tooltip=None if enabled else TOAST_COMING_SOON,
    )


def _status_line(message: str) -> ft.Control:
    """A muted centered placeholder for the empty/error session states."""
    return ft.Container(
        padding=ft.Padding.symmetric(
            horizontal=DS.spacing.lg,
            vertical=DS.spacing.xl,
        ),
        alignment=ft.Alignment.CENTER,
        content=ft.Text(
            value=message,
            size=DS.typography.body_size,
            color=DS.colors.text_secondary,
            text_align=ft.TextAlign.CENTER,
        ),
    )


def _session_controls(
    snapshot: SidebarSnapshot,
    *,
    edit_mode: bool,
    on_select: Callable[[str], None],
    edit_refs: dict[str, ft.Ref[ft.TextField]],
) -> list[ft.Control]:
    """Build the bucketed session section (headers + tiles) for ``snapshot``.

    While edit mode is on, every bot-bound row gets its own ``ft.Ref`` stored in
    ``edit_refs`` (keyed by ``real_id``) so the save path can read the typed
    value. Orphans get no field and no ref (EM-02, the UI half of the guard).
    """
    if isinstance(snapshot.state, LoadError):
        return [_status_line(SESSIONS_ERROR)]

    buckets = bucket_sessions(snapshot.sessions, _now_ms())
    if not buckets:
        return [_status_line(SESSIONS_EMPTY)]

    def select_handler(real_id: str) -> Callable[[], None]:
        def handler() -> None:
            on_select(real_id)

        return handler

    controls: list[ft.Control] = []
    for label, sessions in buckets:
        controls.append(section_header(label))
        for session in sessions:
            title_ref: ft.Ref[ft.TextField] | None = None
            if edit_mode and session.adopted_name is not None:
                title_ref = ft.Ref[ft.TextField]()
                edit_refs[session.real_id] = title_ref
            controls.append(
                session_tile(
                    session,
                    active=session.real_id == snapshot.active_real_id,
                    on_tap=select_handler(session.real_id),
                    edit_mode=edit_mode,
                    title_ref=title_ref,
                )
            )
    return controls


@dataclass
class Drawer:
    """The assembled drawer plus the handles the shell drives (P4 wiring)."""

    control: ft.NavigationDrawer
    content: ft.Container
    sessions_view: ft.ListView
    search: SearchPlaceholder
    footer: ft.Container
    on_select: Callable[[str], None]
    on_open_settings: Callable[[], None] | None
    snapshot: SidebarSnapshot
    edit_mode: bool = False
    edit_refs: dict[str, ft.Ref[ft.TextField]] = field(default_factory=dict)

    def render(self, snapshot: SidebarSnapshot) -> None:
        """Re-render only the mutable sections (sessions + footer) for a load."""
        self.snapshot = snapshot
        self.edit_refs.clear()
        self.sessions_view.controls = _session_controls(
            snapshot,
            edit_mode=self.edit_mode,
            on_select=self.on_select,
            edit_refs=self.edit_refs,
        )
        self.footer.content = footer_profile(
            snapshot.profile_name,
            on_tap=self.on_open_settings,
        )

    def collect_edits(self) -> list[tuple[str, str]]:
        """Return ``(real_id, new_title)`` for non-empty, changed inline fields.

        The save path only persists real edits: unchanged titles and blank
        inputs are skipped (EM-01/EM-04). A ``None`` ref means the field was
        unmounted, so it contributes nothing.
        """
        edits: list[tuple[str, str]] = []
        for real_id, ref in self.edit_refs.items():
            title_field = ref.current
            if title_field is None:
                continue
            new_title = (title_field.value or "").strip()
            original = title_field.data if isinstance(title_field.data, str) else ""
            if new_title and new_title != original:
                edits.append((real_id, new_title))
        return edits

    def set_edit_mode(self, editing: bool) -> None:
        """Toggle edit mode and re-render the session rows accordingly."""
        self.edit_mode = editing
        self.render(self.snapshot)

    def set_width(self, width: float) -> None:
        """Track ``page.width`` on resize (100%-wide drawer, SH-01)."""
        self.control.width = width

    def set_height(self, height: float) -> None:
        """Track ``page.height`` on resize to keep the fixed footer pinned (A3)."""
        self.content.height = height


def build_drawer(
    page: ft.Page,
    snapshot: SidebarSnapshot,
    *,
    on_toggle: Callable[[], None],
    on_select: Callable[[str], None],
    on_new_chat: Callable[[], None],
    on_open_explorer: Callable[[], None],
    on_open_settings: Callable[[], None] | None,
    on_edit: Callable[[], None],
    on_search: Callable[[], None] | None = None,
) -> Drawer:
    """Build the page-level drawer for ``snapshot`` (once; re-render on load)."""
    width = _dimension(page, "width", _FALLBACK_WIDTH)
    height = _dimension(page, "height", _FALLBACK_HEIGHT)

    search = search_placeholder(on_search if on_search is not None else _noop)

    options = ft.Column(
        controls=[
            _option_row(ft.Icons.ADD, OPTION_NEW_CHAT, on_new_chat),
            _option_row(ft.Icons.FOLDER_OPEN, OPTION_EXPLORER, on_open_explorer),
            _option_row(ft.Icons.SETTINGS, OPTION_SETTINGS, None),
        ],
        spacing=DS.spacing.xxs,
    )

    header = ft.Container(
        padding=ft.Padding.only(
            left=DS.spacing.md,
            right=DS.spacing.md,
            top=DS.spacing.md,
        ),
        content=ft.Column(
            controls=[
                options,
                ft.Container(height=DS.spacing.xl),
                search.control,
            ],
            spacing=0,
        ),
    )

    sessions_view = ft.ListView(
        expand=True,
        spacing=DS.spacing.xxs,
        padding=ft.Padding.symmetric(
            horizontal=DS.spacing.sm,
            vertical=DS.spacing.sm,
        ),
    )

    footer = ft.Container(content=footer_profile(snapshot.profile_name, on_tap=on_open_settings))

    content = ft.Container(
        height=height,
        bgcolor=DS.colors.drawer_bg,
        content=ft.Column(
            controls=[header, sessions_view, footer],
            spacing=0,
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
    )

    def handle_dismiss(_: ft.Event[ft.NavigationDrawer] | None = None) -> None:
        on_toggle()

    control = ft.NavigationDrawer(
        controls=[content],
        width=width,
        bgcolor=DS.colors.drawer_bg,
        elevation=0,
        on_dismiss=handle_dismiss,
    )

    drawer = Drawer(
        control=control,
        content=content,
        sessions_view=sessions_view,
        search=search,
        footer=footer,
        on_select=on_select,
        on_open_settings=on_open_settings,
        snapshot=snapshot,
    )
    drawer.render(snapshot)
    # ``on_edit`` is consumed by P4's edit-mode wiring (top-bar pencil); the
    # drawer only needs to expose ``set_edit_mode`` here, so it stays accepted
    # but unused until then.
    return drawer


def _noop() -> None:
    """Inert search tap until P4 wires the ``Próximamente`` toast."""
