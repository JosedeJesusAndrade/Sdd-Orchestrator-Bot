"""Top bar molecule: drawer toggle, model picker, new chat and edit.

Pure presentational: it renders the four header controls and forwards each tap
to a callback. The user avatar lives ONLY in the drawer footer. No service is
called here; the model label and the pencil icon (which reflects edit mode) are
the only mutable pieces.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import flet as ft

from flet_app.core.design_system import DS

_MODEL_DISPLAY_NAMES: dict[str, str] = {
    "deepseek/deepseek-v4-pro": "DeepSeek V4 Pro",
    "deepseek/deepseek-flash": "DeepSeek Flash",
}


def model_display_name(model: str) -> str:
    """Turn a provider model id into a short, human label."""
    known = _MODEL_DISPLAY_NAMES.get(model)
    if known is not None:
        return known
    tail = model.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").strip()
    return tail.title() if tail else model


def _edit_icon(icon: ft.IconData) -> ft.Icon:
    """The pencil glyph inside the CHECK<->EDIT ``AnimatedSwitcher``."""
    return ft.Icon(icon, size=DS.sizes.icon_md, color=DS.colors.text_primary)


@dataclass
class TopBar:
    """The assembled bar plus the mutable model label and pencil button."""

    control: ft.Container
    model_text: ft.Text
    edit_button: ft.IconButton
    edit_icon_switcher: ft.AnimatedSwitcher

    def set_model(self, model: str) -> None:
        self.model_text.value = model_display_name(model)

    def set_edit_mode(self, editing: bool) -> None:
        """Reflect edit mode on the pencil (CHECK<->EDIT morph + tooltip).

        The icon swap goes through the ``AnimatedSwitcher`` (``fast_ms``) rather
        than mutating an ``IconButton.icon`` literal, so the toggle animates.
        """
        icon = ft.Icons.CHECK if editing else ft.Icons.EDIT
        self.edit_icon_switcher.content = _edit_icon(icon)
        self.edit_button.tooltip = "Listo" if editing else "Renombrar sesiones"


def build_top_bar(
    model: str,
    *,
    on_toggle: Callable[[], None] | None = None,
    on_open_model: Callable[[], None] | None = None,
    on_new_chat: Callable[[], None] | None = None,
    on_edit: Callable[[], None] | None = None,
) -> TopBar:
    """Build the header for the active ``model`` (callbacks, no services)."""
    model_text = ft.Text(
        value=model_display_name(model),
        size=DS.typography.title_size,
        weight=DS.typography.title_weight,
        color=DS.colors.text_primary,
    )

    def handle_model(_: ft.Event[ft.Container]) -> None:
        if on_open_model is not None:
            on_open_model()

    model_selector = ft.Container(
        content=ft.Row(
            controls=[
                model_text,
                ft.Icon(
                    ft.Icons.EXPAND_MORE,
                    size=DS.sizes.icon_sm,
                    color=DS.colors.text_secondary,
                ),
            ],
            spacing=DS.spacing.xs,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(horizontal=DS.spacing.sm, vertical=DS.spacing.xs),
        border_radius=DS.radii.small,
        ink=on_open_model is not None,
        tooltip="Elegir modelo",
        on_click=handle_model,
    )

    def handle_toggle(_: ft.Event[ft.IconButton]) -> None:
        if on_toggle is not None:
            on_toggle()

    def handle_new_chat(_: ft.Event[ft.IconButton]) -> None:
        if on_new_chat is not None:
            on_new_chat()

    def handle_edit(_: ft.Event[ft.IconButton]) -> None:
        if on_edit is not None:
            on_edit()

    edit_icon_switcher = ft.AnimatedSwitcher(
        content=_edit_icon(ft.Icons.EDIT),
        duration=DS.motion.fast_ms,
        switch_in_curve=DS.motion.curve,
        switch_out_curve=DS.motion.curve,
    )

    edit_button = ft.IconButton(
        icon=edit_icon_switcher,
        tooltip="Renombrar sesiones",
        disabled=on_edit is None,
        on_click=handle_edit,
    )

    row = ft.Row(
        controls=[
            ft.IconButton(
                icon=ft.Icons.MENU,
                icon_color=DS.colors.text_primary,
                icon_size=DS.sizes.icon_lg,
                tooltip="Sesiones",
                disabled=on_toggle is None,
                on_click=handle_toggle,
            ),
            model_selector,
            ft.Container(expand=True),
            ft.IconButton(
                icon=ft.Icons.ADD_COMMENT,
                icon_color=DS.colors.text_primary,
                icon_size=DS.sizes.icon_md,
                tooltip="Nuevo chat",
                disabled=on_new_chat is None,
                on_click=handle_new_chat,
            ),
            edit_button,
        ],
        spacing=DS.spacing.sm,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # Slightly more top air than the Slice 1 symmetric ``md`` (12 -> 13, ~10%)
    # so the bar clears the Android status bar even without an inset.
    vertical = DS.spacing.md + DS.spacing.xs // 4
    control = ft.Container(
        content=row,
        padding=ft.Padding.only(
            left=DS.spacing.xl,
            right=DS.spacing.xl,
            top=vertical,
            bottom=DS.spacing.md,
        ),
    )
    return TopBar(
        control=control,
        model_text=model_text,
        edit_button=edit_button,
        edit_icon_switcher=edit_icon_switcher,
    )
