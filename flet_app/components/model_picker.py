"""Grouped model picker ``AlertDialog`` (MP-01..MP-03).

Pure presentational: it renders the loaded ``ModelCatalogSnapshot`` grouped by
provider, marks the conv-active model with a check, and reports the chosen id
through ``on_select``. The empty/degraded catalog is a valid snapshot and shows
the exact ``No hay modelos disponibles.`` copy (plus an optional retry). No
service is called and no preference is persisted here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import flet as ft

from flet_app.components.section_header import section_header
from flet_app.core.design_system import DS
from flet_app.ui_strings import MODELS_EMPTY, RETRY

if TYPE_CHECKING:
    from services.model_catalog import ModelCatalogSnapshot, ModelOption


def _option_tile(
    option: ModelOption,
    *,
    on_select: Callable[[str], None],
) -> ft.Control:
    """One selectable model row; a check marks the conversation's model."""

    def handle_tap(_: ft.Event[ft.ListTile]) -> None:
        on_select(option.id)

    return ft.ListTile(
        title=ft.Text(
            value=option.label,
            size=DS.typography.body_size,
            color=DS.colors.text_primary,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        ),
        subtitle=ft.Text(
            value=option.provider,
            size=DS.typography.meta_size,
            color=DS.colors.text_secondary,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        ),
        trailing=(
            ft.Icon(ft.Icons.CHECK, size=DS.typography.body_size, color=DS.colors.accent)
            if option.is_default
            else None
        ),
        dense=True,
        on_click=handle_tap,
        data=option.id,
    )


def _empty_body(on_retry: Callable[[], None] | None) -> list[ft.Control]:
    controls: list[ft.Control] = [
        ft.Container(
            padding=ft.Padding.symmetric(vertical=DS.spacing.lg),
            content=ft.Text(
                value=MODELS_EMPTY,
                size=DS.typography.body_size,
                color=DS.colors.text_secondary,
                text_align=ft.TextAlign.CENTER,
            ),
        )
    ]
    if on_retry is not None:

        def handle_retry(_: ft.Event[ft.Button]) -> None:
            on_retry()

        controls.append(
            ft.FilledButton(
                content=RETRY,
                icon=ft.Icons.REFRESH,
                on_click=handle_retry,
            )
        )
    return controls


def model_picker(
    snapshot: ModelCatalogSnapshot,
    *,
    on_select: Callable[[str], None],
    on_retry: Callable[[], None] | None = None,
) -> ft.AlertDialog:
    """Build the grouped model chooser dialog for ``snapshot``."""
    controls: list[ft.Control] = []
    if snapshot.options:
        for group in snapshot.groups:
            controls.append(section_header(group.label))
            controls.extend(_option_tile(option, on_select=on_select) for option in group.options)
    else:
        controls.extend(_empty_body(on_retry))

    return ft.AlertDialog(
        modal=True,
        content=ft.Column(
            controls=controls,
            spacing=0,
            tight=True,
            scroll=ft.ScrollMode.AUTO,
        ),
    )
