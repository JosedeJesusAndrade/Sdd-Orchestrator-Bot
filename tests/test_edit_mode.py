"""Tests for the edit-mode state machine and its failure notice (P6, EM-01..EM-04).

The state machine is exercised with fakes: a drawer that records the toggles and
returns preset edits, a rename callable that succeeds/fails, and a FakePage that
captures the SnackBar. The data path itself (persist + reload + overlay) lives in
``test_flet_data_controllers.py``.
"""

from __future__ import annotations

from typing import cast

import flet as ft

from flet_app.core.design_system import DS
from flet_app.edit_mode import EditMode, rename_failure_notice
from flet_app.ui_strings import RENAME_FAILED


class FakeDrawer:
    """Records ``set_edit_mode`` calls and returns preset edits on save."""

    def __init__(self, edits: list[tuple[str, str]] | None = None) -> None:
        self.edits = list(edits or [])
        self.modes: list[bool] = []

    def set_edit_mode(self, editing: bool) -> None:
        self.modes.append(editing)

    def collect_edits(self) -> list[tuple[str, str]]:
        return list(self.edits)


class FakePage:
    """Captures SnackBars handed to ``show_dialog``."""

    def __init__(self) -> None:
        self.snackbars: list[ft.SnackBar] = []

    def show_dialog(self, dialog: ft.DialogControl) -> None:
        if isinstance(dialog, ft.SnackBar):
            self.snackbars.append(dialog)


def _harness(
    drawer: FakeDrawer,
    results: list[bool],
    *,
    page: FakePage | None = None,
) -> tuple[
    EditMode,
    list[tuple[str, str]],
    list[bool],
    list[bool],
]:
    renamed: list[tuple[str, str]] = []
    pencil: list[bool] = []
    refreshed: list[bool] = []

    async def rename(real_id: str, title: str) -> bool:
        renamed.append((real_id, title))
        return results.pop(0)

    async def refresh() -> None:
        refreshed.append(True)

    failure_page = page if page is not None else FakePage()

    def show_failure() -> None:
        rename_failure_notice(cast(ft.Page, failure_page))

    mode = EditMode(
        drawer=drawer,
        rename=rename,
        set_pencil=pencil.append,
        refresh=refresh,
        show_failure=show_failure,
    )
    return mode, renamed, pencil, refreshed


def test_activate_turns_on_the_pencil_and_the_fields() -> None:
    drawer = FakeDrawer()
    mode, _, pencil, _ = _harness(drawer, [])

    mode.activate()

    assert mode.active is True
    assert pencil == [True]
    assert drawer.modes == [True]


async def test_save_persists_changed_rows_then_leaves_edit_mode() -> None:
    drawer = FakeDrawer([("ses_1", "Uno"), ("ses_2", "Dos")])
    mode, renamed, pencil, refreshed = _harness(drawer, [True, True])

    mode.activate()
    ok = await mode.save()

    assert ok is True
    assert renamed == [("ses_1", "Uno"), ("ses_2", "Dos")]
    assert mode.active is False
    assert pencil == [True, False]
    assert drawer.modes == [True, False]
    assert refreshed == [True]


async def test_save_with_no_edits_just_leaves_edit_mode() -> None:
    drawer = FakeDrawer()
    mode, renamed, pencil, refreshed = _harness(drawer, [])

    mode.activate()
    ok = await mode.save()

    assert ok is True
    assert renamed == []
    assert mode.active is False
    assert pencil == [True, False]
    assert refreshed == [True]


async def test_save_failure_shows_snackbar_and_keeps_edit_mode() -> None:
    page = FakePage()
    drawer = FakeDrawer([("ses_1", "Uno")])
    mode, renamed, pencil, refreshed = _harness(drawer, [False], page=page)

    mode.activate()
    ok = await mode.save()

    assert ok is False
    assert renamed == [("ses_1", "Uno")]
    assert mode.active is True, "the user must be able to retry"
    assert pencil == [True], "the pencil stays CHECK (no EDIT flip)"
    assert drawer.modes == [True], "the inline fields are kept"
    assert refreshed == [], "no reload that would drop the typed value"

    snack = page.snackbars[-1]
    assert cast(ft.Text, snack.content).value == RENAME_FAILED
    assert snack.duration == 3000
    assert snack.behavior == ft.SnackBarBehavior.FLOATING
    assert snack.bgcolor == DS.colors.surface_raised


async def test_one_failure_among_successes_keeps_edit_mode() -> None:
    page = FakePage()
    drawer = FakeDrawer([("ses_1", "Uno"), ("ses_2", "Dos")])
    mode, renamed, _, refreshed = _harness(drawer, [True, False], page=page)

    mode.activate()
    ok = await mode.save()

    assert ok is False
    assert renamed == [("ses_1", "Uno"), ("ses_2", "Dos")]
    assert mode.active is True
    assert refreshed == []
    assert cast(ft.Text, page.snackbars[-1].content).value == RENAME_FAILED


def test_rename_failure_notice_is_a_floating_surface_snackbar() -> None:
    page = FakePage()

    rename_failure_notice(cast(ft.Page, page))

    snack = page.snackbars[-1]
    assert cast(ft.Text, snack.content).value == RENAME_FAILED
    assert snack.duration == 3000
    assert snack.behavior == ft.SnackBarBehavior.FLOATING
    assert snack.bgcolor == DS.colors.surface_raised
