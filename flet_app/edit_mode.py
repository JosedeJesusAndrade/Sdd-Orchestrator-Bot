"""Edit-mode UI state: the pencil, the inline fields and the explicit save (P6).

This is the UI half of the edit mode; the data half is
``SidebarController.rename`` (P2). The split is deliberate (EM-01..EM-04):

- ``activate`` turns the pencil into CHECK and swaps the bot-bound titles for
  inline ``TextField``s (orphans stay read-only — the UI half of the guard).
- ``save`` is the ONLY writer: each non-empty, changed title goes through the
  injected async ``rename`` (the data controller), then edit mode leaves and the
  drawer refreshes. There is no autosave and no per-keystroke write.
- a failed rename shows a SnackBar and KEEPS edit mode on so the user can retry
  with the typed values intact (the failure is never silent).

It knows nothing about ``SessionService``/``SessionStore`` — only the injected
``rename`` callable. Pure orchestration, unit-tested with fakes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

import flet as ft

from flet_app.core.design_system import DS
from flet_app.ui_strings import RENAME_FAILED

#: ``(real_id, title) -> persisted`` — bound to ``SidebarController.rename``.
RenameFn = Callable[[str, str], Awaitable[bool]]


class EditableDrawer(Protocol):
    """The drawer surface edit mode drives (satisfied by ``Drawer``)."""

    def set_edit_mode(self, editing: bool) -> None: ...

    def collect_edits(self) -> list[tuple[str, str]]: ...


def rename_failure_notice(page: ft.Page) -> None:
    """Show the non-blocking failure toast; edit mode stays on (EM-01)."""
    page.show_dialog(
        ft.SnackBar(
            content=ft.Text(value=RENAME_FAILED),
            duration=3000,
            behavior=ft.SnackBarBehavior.FLOATING,
            bgcolor=DS.colors.surface_raised,
        )
    )


class EditMode:
    """The edit-mode state machine over a drawer, a rename and a refresh."""

    def __init__(
        self,
        *,
        drawer: EditableDrawer,
        rename: RenameFn,
        set_pencil: Callable[[bool], None],
        refresh: Callable[[], Awaitable[None]],
        show_failure: Callable[[], None],
    ) -> None:
        self._drawer = drawer
        self._rename = rename
        self._set_pencil = set_pencil
        self._refresh = refresh
        self._show_failure = show_failure
        self._active = False

    @property
    def active(self) -> bool:
        """Whether the pencil is currently ON."""
        return self._active

    def activate(self) -> None:
        """Pencil ON: reveal the inline fields on the bot-bound rows."""
        self._active = True
        self._set_pencil(True)
        self._drawer.set_edit_mode(True)

    async def save(self) -> bool:
        """Pencil OFF: persist the changed titles, then leave edit mode.

        On any failed rename the SnackBar is shown and edit mode stays active
        (the drawer keeps its fields), so the user can fix the input and retry.
        """
        failed = False
        for real_id, title in self._drawer.collect_edits():
            if not await self._rename(real_id, title):
                failed = True

        if failed:
            self._show_failure()
            return False

        self._active = False
        self._set_pencil(False)
        self._drawer.set_edit_mode(False)
        await self._refresh()
        return True
