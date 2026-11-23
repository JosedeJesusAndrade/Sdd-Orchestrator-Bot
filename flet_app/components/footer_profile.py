"""Fixed drawer footer: avatar initial, truncated profile name, overflow dots."""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from flet_app.core.design_system import DS


def _initial(name: str) -> str:
    """First visible character of ``name`` uppercased, ``?`` when blank."""
    stripped = name.strip()
    return stripped[:1].upper() if stripped else "?"


def footer_profile(
    name: str,
    *,
    on_tap: Callable[[], None] | None = None,
) -> ft.Control:
    """Render the profile strip pinned to the bottom of the drawer."""
    avatar = ft.CircleAvatar(
        radius=DS.sizes.avatar // 2,
        bgcolor=DS.colors.accent,
        content=ft.Text(
            value=_initial(name),
            size=DS.typography.label_size,
            weight=DS.typography.title_weight,
            color=DS.colors.white,
        ),
    )

    name_text = ft.Text(
        value=name,
        size=DS.typography.label_size,
        color=DS.colors.text_primary,
        max_lines=1,
        overflow=ft.TextOverflow.ELLIPSIS,
        expand=True,
    )

    dots = ft.Icon(
        ft.Icons.MORE_HORIZ,
        size=DS.typography.body_size,
        color=DS.colors.text_secondary,
    )

    def handle_tap(_: ft.Event[ft.Container]) -> None:
        if on_tap is not None:
            on_tap()

    return ft.Container(
        content=ft.Row(
            controls=[avatar, name_text, dots],
            spacing=DS.spacing.sm,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(
            horizontal=DS.spacing.lg,
            vertical=DS.spacing.md,
        ),
        border=ft.Border.only(top=ft.BorderSide(1, DS.colors.outline)),
        ink=on_tap is not None,
        on_click=handle_tap if on_tap is not None else None,
    )
