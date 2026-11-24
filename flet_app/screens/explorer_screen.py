"""Explorer screen: breadcrumb + flat list + CRUD + select (EX-01..EX-04).

This is the VIEW of the workdir explorer. All data arrives through the injected
:class:`~flet_app.data.explorer.ExplorerController` (the pure data layer); the
screen builds no service and owns no copy — strings come from
``flet_app.ui_strings`` and failures are mapped through
:func:`~flet_app.ui_strings.workdir_failure_copy` keyed by ``WorkdirFailureKind``.

Orthogonality (D3): tapping a directory row ENTERS it (browse); only the
``Usar esta carpeta`` CTA SELECTS the current directory as the workdir, and a
non-git directory only persists after the ``Continuar`` confirmation.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import flet as ft

from flet_app.components.app_background import app_background
from flet_app.core.design_system import DS
from flet_app.data.explorer import ExplorerController
from flet_app.ui_strings import (
    CANCEL,
    EXPLORER_BACK,
    EXPLORER_CREATE,
    EXPLORER_DELETE,
    EXPLORER_DELETE_PROMPT,
    EXPLORER_DELETE_TITLE,
    EXPLORER_EMPTY,
    EXPLORER_FOLDER_NAME,
    EXPLORER_GIT_BADGE,
    EXPLORER_INIT_GIT,
    EXPLORER_NEW_FOLDER,
    EXPLORER_RENAME,
    EXPLORER_RENAME_TITLE,
    EXPLORER_SAVE,
    EXPLORER_TITLE,
    EXPLORER_USE_FOLDER,
    NON_GIT_CANCEL,
    NON_GIT_CONTINUE,
    NON_GIT_PROMPT,
    NON_GIT_TITLE,
    RETRY,
    workdir_failure_copy,
)
from services.workdir_service import DirectoryEntry, DirectoryListing, WorkdirFailure


class ExplorerScreen:
    """The assembled explorer plus the handles ``main`` drives (P5 wiring)."""

    def __init__(
        self,
        page: ft.Page,
        controller: ExplorerController,
        *,
        on_back: Callable[[], None],
        on_select: Callable[[], None],
    ) -> None:
        self._page = page
        self._controller = controller
        self._on_back = on_back
        self._on_select = on_select

        self._listing: DirectoryListing | None = None
        self._failure: WorkdirFailure | None = None
        self._path: Path | None = None
        self._attempt: Path | None = None

        self.breadcrumb = ft.Row(
            controls=[],
            spacing=DS.spacing.xxs,
            scroll=ft.ScrollMode.AUTO,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self.entries_view = ft.ListView(
            expand=True,
            spacing=DS.spacing.xxs,
            padding=ft.Padding.symmetric(vertical=DS.spacing.sm),
        )

        self.error_text = ft.Text(
            value="",
            size=DS.typography.body_size,
            color=DS.colors.danger,
        )
        self.error_holder = ft.Container(
            visible=False,
            padding=ft.Padding.symmetric(
                horizontal=DS.spacing.xl,
                vertical=DS.spacing.md,
            ),
            content=ft.Column(
                controls=[
                    self.error_text,
                    ft.TextButton(
                        content=RETRY,
                        icon=ft.Icons.REFRESH,
                        on_click=self._handle_retry,
                    ),
                ],
                spacing=DS.spacing.xs,
                horizontal_alignment=ft.CrossAxisAlignment.START,
            ),
        )

        self.new_button = ft.TextButton(
            content=EXPLORER_NEW_FOLDER,
            icon=ft.Icons.CREATE_NEW_FOLDER,
            on_click=self._handle_new,
        )
        self.use_button = ft.FilledButton(
            content=EXPLORER_USE_FOLDER,
            icon=ft.Icons.CHECK,
            disabled=True,
            expand=True,
            on_click=self._handle_use,
        )

        self.control = self._compose()

    # ── Read-only state (for verification/tests) ───────────────────────

    @property
    def listing(self) -> DirectoryListing | None:
        """The last successful listing, if any."""
        return self._listing

    @property
    def failure(self) -> WorkdirFailure | None:
        """The last typed failure, if any."""
        return self._failure

    @property
    def path(self) -> Path | None:
        """The directory currently being browsed."""
        return self._path

    # ── Loading / rendering ────────────────────────────────────────────

    async def load(self, path: Path | str) -> None:
        """List ``path`` through the controller and render the result."""
        target = Path(path)
        self._attempt = target
        result = await self._controller.list_dir(target)
        self.render(result)

    def render(self, result: DirectoryListing | WorkdirFailure) -> None:
        """Repaint the breadcrumb + list (or the typed failure copy)."""
        if isinstance(result, WorkdirFailure):
            self._render_failure(result)
        else:
            self._render_listing(result)
        self._page.update()

    def _render_listing(self, listing: DirectoryListing) -> None:
        self._listing = listing
        self._failure = None
        self._path = listing.path
        self._attempt = listing.path
        self._render_breadcrumb(listing.ancestors)
        self._render_entries(listing)
        self.error_holder.visible = False
        self.entries_view.visible = True
        self._set_actions_enabled(True)

    def _render_failure(self, failure: WorkdirFailure) -> None:
        self._failure = failure
        self.error_text.value = workdir_failure_copy(failure.kind)
        self.error_holder.visible = True
        # A failed navigation keeps the last good directory browsable; only a
        # failure with no listing at all leaves the actions disabled.
        self.entries_view.visible = self._listing is not None
        self._set_actions_enabled(self._listing is not None)

    def _render_breadcrumb(self, ancestors: tuple[Path, ...]) -> None:
        controls: list[ft.Control] = []
        last = len(ancestors) - 1
        for index, ancestor in enumerate(ancestors):
            if index:
                controls.append(
                    ft.Icon(
                        ft.Icons.CHEVRON_RIGHT,
                        size=DS.typography.meta_size,
                        color=DS.colors.text_muted,
                    )
                )
            controls.append(self._crumb(ancestor, current=index == last))
        self.breadcrumb.controls = controls

    def _crumb(self, path: Path, *, current: bool) -> ft.Control:
        def handle_tap(_: ft.Event[ft.TextButton]) -> None:
            self._page.run_task(self._enter, path)

        return ft.TextButton(
            content=ft.Text(
                value=path.name or str(path),
                size=DS.typography.label_size,
                color=DS.colors.text_primary if current else DS.colors.text_secondary,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
            ),
            data=path,
            on_click=handle_tap,
        )

    def _render_entries(self, listing: DirectoryListing) -> None:
        if not listing.entries:
            self.entries_view.controls = [self._empty_line()]
            return
        self.entries_view.controls = [self._entry_row(entry) for entry in listing.entries]

    def _empty_line(self) -> ft.Control:
        return ft.Container(
            padding=ft.Padding.symmetric(
                horizontal=DS.spacing.xl,
                vertical=DS.spacing.xl,
            ),
            content=ft.Text(
                value=EXPLORER_EMPTY,
                size=DS.typography.body_size,
                color=DS.colors.text_secondary,
            ),
        )

    def _entry_row(self, entry: DirectoryEntry) -> ft.Control:
        trailing: list[ft.Control] = []
        if entry.is_git:
            trailing.append(self._git_badge())
        if entry.is_symlink:
            trailing.append(
                ft.Icon(ft.Icons.LINK, size=DS.typography.meta_size, color=DS.colors.text_muted)
            )
        if entry.is_dir:
            trailing.append(
                ft.IconButton(
                    icon=ft.Icons.EDIT,
                    icon_size=DS.typography.body_size,
                    icon_color=DS.colors.text_secondary,
                    tooltip=EXPLORER_RENAME,
                    on_click=self._rename_handler(entry),
                )
            )
            trailing.append(
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE,
                    icon_size=DS.typography.body_size,
                    icon_color=DS.colors.danger,
                    tooltip=EXPLORER_DELETE,
                    on_click=self._delete_handler(entry),
                )
            )

        def handle_enter(_: ft.Event[ft.Container]) -> None:
            self._page.run_task(self._enter, entry.path)

        return ft.Container(
            data=entry,
            padding=ft.Padding.symmetric(
                horizontal=DS.spacing.lg,
                vertical=DS.spacing.sm,
            ),
            border_radius=DS.radii.tile,
            ink=entry.is_dir,
            on_click=handle_enter if entry.is_dir else None,
            content=ft.Row(
                controls=[
                    ft.Icon(
                        ft.Icons.FOLDER if entry.is_dir else ft.Icons.INSERT_DRIVE_FILE,
                        size=DS.typography.label_size,
                        color=DS.colors.text_secondary,
                    ),
                    ft.Text(
                        value=entry.name,
                        size=DS.typography.label_size,
                        color=DS.colors.text_primary,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                        expand=True,
                    ),
                    *trailing,
                ],
                spacing=DS.spacing.sm,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _git_badge(self) -> ft.Control:
        return ft.Container(
            padding=ft.Padding.symmetric(
                horizontal=DS.spacing.sm,
                vertical=DS.spacing.xxs,
            ),
            border_radius=DS.radii.small,
            bgcolor=DS.colors.surface,
            content=ft.Text(
                value=EXPLORER_GIT_BADGE,
                size=DS.typography.meta_size,
                color=DS.colors.accent_soft,
            ),
        )

    def _set_actions_enabled(self, enabled: bool) -> None:
        self.use_button.disabled = not enabled
        self.new_button.disabled = not enabled

    # ── Browse vs select (D3) ──────────────────────────────────────────

    async def _enter(self, path: Path) -> None:
        await self.load(path)

    async def _select(self) -> None:
        path = self._path
        if path is None:
            return
        result = await self._controller.select(path)
        if isinstance(result, WorkdirFailure):
            self.render(result)
            return
        self._on_select()
        listing = self._controller.listing
        if listing is not None:
            self.render(listing)

    def _use_current(self) -> None:
        """CTA: SELECT the current directory (non-git asks first)."""
        listing = self._listing
        if listing is None:
            return
        if listing.is_git:
            self._page.run_task(self._select)
            return
        self._open_non_git_confirm()

    def _open_non_git_confirm(self) -> None:
        def handle_cancel(_: ft.Event[ft.TextButton]) -> None:
            self._page.pop_dialog()

        def handle_continue(_: ft.Event[ft.Button]) -> None:
            self._page.pop_dialog()
            self._page.run_task(self._select)

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(value=NON_GIT_TITLE),
            content=ft.Text(value=NON_GIT_PROMPT),
            actions=[
                ft.TextButton(content=NON_GIT_CANCEL, on_click=handle_cancel),
                ft.FilledButton(content=NON_GIT_CONTINUE, on_click=handle_continue),
            ],
        )
        self._page.show_dialog(dialog)

    # ── CRUD (EX-02) ───────────────────────────────────────────────────

    async def _create(self, parent: Path, name: str, init_git: bool) -> None:
        result = await self._controller.create_child(parent, name, init_git=init_git)
        self._render_mutation(result)

    async def _rename(self, path: Path, new_name: str) -> None:
        result = await self._controller.rename(path, new_name)
        self._render_mutation(result)

    async def _delete(self, path: Path) -> None:
        result = await self._controller.delete(path)
        self._render_mutation(result)

    async def _retry(self) -> None:
        if self._attempt is not None:
            await self.load(self._attempt)

    def _render_mutation(self, result: DirectoryEntry | WorkdirFailure) -> None:
        if isinstance(result, WorkdirFailure):
            self.render(result)
            return
        listing = self._controller.listing
        if listing is not None:
            self.render(listing)

    def _open_create(self) -> None:
        parent = self._path
        if parent is None:
            return
        name_field = ft.TextField(label=EXPLORER_FOLDER_NAME, autofocus=True)
        init_git = ft.Checkbox(label=EXPLORER_INIT_GIT, value=False)

        def handle_cancel(_: ft.Event[ft.TextButton]) -> None:
            self._page.pop_dialog()

        def handle_create(_: ft.Event[ft.Button]) -> None:
            name = (name_field.value or "").strip()
            self._page.pop_dialog()
            if name:
                self._page.run_task(self._create, parent, name, bool(init_git.value))

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(value=EXPLORER_NEW_FOLDER),
            content=ft.Column(
                controls=[name_field, init_git],
                spacing=DS.spacing.sm,
                tight=True,
            ),
            actions=[
                ft.TextButton(content=CANCEL, on_click=handle_cancel),
                ft.FilledButton(content=EXPLORER_CREATE, on_click=handle_create),
            ],
        )
        self._page.show_dialog(dialog)

    def _open_rename(self, path: Path, current_name: str) -> None:
        name_field = ft.TextField(
            label=EXPLORER_FOLDER_NAME,
            value=current_name,
            autofocus=True,
        )

        def handle_cancel(_: ft.Event[ft.TextButton]) -> None:
            self._page.pop_dialog()

        def handle_save(_: ft.Event[ft.Button]) -> None:
            new_name = (name_field.value or "").strip()
            self._page.pop_dialog()
            if new_name and new_name != current_name:
                self._page.run_task(self._rename, path, new_name)

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(value=EXPLORER_RENAME_TITLE),
            content=name_field,
            actions=[
                ft.TextButton(content=CANCEL, on_click=handle_cancel),
                ft.FilledButton(content=EXPLORER_SAVE, on_click=handle_save),
            ],
        )
        self._page.show_dialog(dialog)

    def _open_delete(self, path: Path, name: str) -> None:
        def handle_cancel(_: ft.Event[ft.TextButton]) -> None:
            self._page.pop_dialog()

        def handle_delete(_: ft.Event[ft.Button]) -> None:
            self._page.pop_dialog()
            self._page.run_task(self._delete, path)

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(value=EXPLORER_DELETE_TITLE),
            content=ft.Text(value=EXPLORER_DELETE_PROMPT.format(name=name)),
            actions=[
                ft.TextButton(content=CANCEL, on_click=handle_cancel),
                ft.FilledButton(content=EXPLORER_DELETE, on_click=handle_delete),
            ],
        )
        self._page.show_dialog(dialog)

    # ── Event handlers (sync -> run_task) ──────────────────────────────

    def _handle_retry(self, _: ft.Event[ft.TextButton]) -> None:
        self._page.run_task(self._retry)

    def _handle_new(self, _: ft.Event[ft.TextButton]) -> None:
        self._open_create()

    def _handle_use(self, _: ft.Event[ft.Button]) -> None:
        self._use_current()

    def _handle_back(self, _: ft.Event[ft.IconButton]) -> None:
        self._on_back()

    def _rename_handler(self, entry: DirectoryEntry) -> Callable[[ft.Event[ft.IconButton]], None]:
        def handler(_: ft.Event[ft.IconButton]) -> None:
            self._open_rename(entry.path, entry.name)

        return handler

    def _delete_handler(self, entry: DirectoryEntry) -> Callable[[ft.Event[ft.IconButton]], None]:
        def handler(_: ft.Event[ft.IconButton]) -> None:
            self._open_delete(entry.path, entry.name)

        return handler

    # ── Composition ────────────────────────────────────────────────────

    def _compose(self) -> ft.Container:
        header = ft.Container(
            padding=ft.Padding.only(
                left=DS.spacing.sm,
                right=DS.spacing.xl,
                top=DS.spacing.sm,
                bottom=DS.spacing.sm,
            ),
            content=ft.Row(
                controls=[
                    ft.IconButton(
                        icon=ft.Icons.ARROW_BACK,
                        icon_color=DS.colors.text_primary,
                        icon_size=DS.sizes.icon_lg,
                        tooltip=EXPLORER_BACK,
                        on_click=self._handle_back,
                    ),
                    ft.Text(
                        value=EXPLORER_TITLE,
                        size=DS.typography.title_size,
                        weight=DS.typography.title_weight,
                        color=DS.colors.text_primary,
                    ),
                ],
                spacing=DS.spacing.sm,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

        breadcrumb_holder = ft.Container(
            padding=ft.Padding.only(
                left=DS.spacing.xl,
                right=DS.spacing.xl,
                bottom=DS.spacing.xs,
            ),
            content=self.breadcrumb,
        )

        toolbar = ft.Container(
            padding=ft.Padding.symmetric(horizontal=DS.spacing.lg),
            content=ft.Row(
                controls=[self.new_button],
                alignment=ft.MainAxisAlignment.START,
            ),
        )

        footer = ft.Container(
            padding=ft.Padding.only(
                left=DS.spacing.xl,
                right=DS.spacing.xl,
                top=DS.spacing.sm,
                bottom=DS.spacing.md,
            ),
            content=ft.Row(
                controls=[self.use_button],
                alignment=ft.MainAxisAlignment.CENTER,
            ),
        )

        body = ft.Column(
            controls=[
                header,
                breadcrumb_holder,
                toolbar,
                self.error_holder,
                self.entries_view,
                footer,
            ],
            expand=True,
            spacing=0,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        return app_background(body)


def build_explorer_screen(
    page: ft.Page,
    controller: ExplorerController,
    *,
    on_back: Callable[[], None],
    on_select: Callable[[], None],
) -> ExplorerScreen:
    """Build the explorer view over ``controller`` (callbacks, no services)."""
    return ExplorerScreen(page, controller, on_back=on_back, on_select=on_select)
