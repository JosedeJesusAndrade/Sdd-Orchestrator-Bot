"""Composer molecule: the multi-line pill, morphing send/cancel button and workdir.

The input grows from one to five lines and then scrolls internally; Enter always
inserts a newline (there is no ``on_submit``) and only the circular button
dispatches. One button morphs between send (``ARROW_UPWARD``) and cancel
(``STOP_CIRCLE``) through an ``AnimatedSwitcher``. The workdir sits *outside* and
*below* the pill, in a deliberately muted tone.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import flet as ft

from flet_app.core.design_system import DS

# Number of trailing path segments kept in the muted workdir caption. Two is
# enough to recognize the project (`…\python\Sdd-Orchestrator-Telegram-Bot`)
# while fitting comfortably on a phone.
_MAX_WORKDIR_SEGMENTS = 2

SendHandler = Callable[
    [ft.Event[ft.TextField] | ft.Event[ft.Container] | None],
    Awaitable[None],
]


@dataclass
class Composer:
    """The composer control plus the pieces the screen needs to drive it."""

    control: ft.Container
    field: ft.TextField
    send_button: ft.Container
    icon_switcher: ft.AnimatedSwitcher
    workdir_text: ft.Text
    workdir_caption: ft.Container
    running: bool = False

    def bind(
        self,
        on_send: SendHandler,
        *,
        on_cancel: SendHandler | None = None,
    ) -> None:
        """Wire the circular button to send, or cancel while a prompt runs."""

        async def dispatch(
            event: ft.Event[ft.TextField] | ft.Event[ft.Container] | None,
        ) -> None:
            if self.running and on_cancel is not None:
                await on_cancel(event)
            else:
                await on_send(event)

        self.send_button.on_click = dispatch

    def set_busy(self, busy: bool) -> None:
        """Disable text entry while a prompt is running."""
        self.field.disabled = busy

    def set_running(self, running: bool) -> None:
        """Morph the button between send and cancel and flip its target."""
        self.running = running
        self.icon_switcher.content = ft.Icon(
            ft.Icons.STOP_CIRCLE if running else ft.Icons.ARROW_UPWARD,
            size=DS.sizes.icon_md,
            color=DS.colors.white,
        )
        self.send_button.bgcolor = DS.colors.danger if running else DS.colors.accent
        self.send_button.tooltip = "Cancelar" if running else "Enviar"

    def set_workdir(self, workdir: str) -> None:
        """Update the muted caption under the pill."""
        self.workdir_text.value = shorten_workdir(workdir)
        self.workdir_caption.tooltip = workdir


def shorten_workdir(workdir: str) -> str:
    """Keep only the trailing path segments, the part users care about.

    Flet's ``TextOverflow.ELLIPSIS`` truncates the *end* of the string, which
    hides the project name on a long Windows path. Truncating the *front*
    instead guarantees the active directory stays readable on a phone.
    """
    parts = Path(workdir).parts
    if len(parts) <= _MAX_WORKDIR_SEGMENTS + 1:
        return workdir
    return f"…{os.sep}{os.sep.join(parts[-_MAX_WORKDIR_SEGMENTS:])}"


def build_composer(*, workdir: str) -> Composer:
    """Build the pill composer and the muted workdir caption below it."""
    field = ft.TextField(
        hint_text="Pregunta, escribe o pide algo…",
        expand=True,
        filled=False,
        border=ft.NoInputBorder(),
        bgcolor=ft.Colors.TRANSPARENT,
        color=DS.colors.text_primary,
        cursor_color=DS.colors.accent,
        text_size=DS.typography.body_size,
        hint_style=ft.TextStyle(
            size=DS.typography.body_size,
            color=DS.colors.text_secondary,
        ),
        content_padding=ft.Padding.symmetric(
            horizontal=DS.spacing.xs,
            vertical=DS.spacing.md,
        ),
        multiline=True,
        min_lines=1,
        max_lines=5,
        shift_enter=False,
        keyboard_type=ft.KeyboardType.MULTILINE,
    )

    icon_switcher = ft.AnimatedSwitcher(
        content=ft.Icon(ft.Icons.ARROW_UPWARD, size=DS.sizes.icon_md, color=DS.colors.white),
        duration=DS.motion.send_ms,
        switch_in_curve=DS.motion.curve,
        switch_out_curve=DS.motion.curve,
    )

    send_button = ft.Container(
        width=DS.sizes.send,
        height=DS.sizes.send,
        border_radius=DS.sizes.send / 2,
        bgcolor=DS.colors.accent,
        alignment=ft.Alignment.CENTER,
        tooltip="Enviar",
        ink=True,
        shadow=ft.BoxShadow(
            blur_radius=18,
            color=DS.colors.alpha(DS.colors.accent, 0.4),
        ),
        content=icon_switcher,
    )

    pill = ft.Container(
        bgcolor=DS.colors.surface,
        border=ft.Border.all(1, DS.colors.outline),
        border_radius=DS.radii.pill,
        padding=ft.Padding.symmetric(horizontal=DS.spacing.sm, vertical=DS.spacing.xs),
        content=ft.Row(
            controls=[
                ft.IconButton(
                    icon=ft.Icons.ADD,
                    icon_color=DS.colors.text_secondary,
                    icon_size=DS.sizes.icon_lg,
                    tooltip="Adjuntar (próximamente)",
                ),
                field,
                ft.IconButton(
                    icon=ft.Icons.MIC,
                    icon_color=DS.colors.text_secondary,
                    icon_size=DS.sizes.icon_lg,
                    tooltip="Voz (próximamente)",
                ),
                send_button,
            ],
            spacing=DS.spacing.xs,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )

    workdir_text = ft.Text(
        value=shorten_workdir(workdir),
        size=DS.typography.muted_size,
        color=DS.colors.text_muted,
        text_align=ft.TextAlign.CENTER,
        no_wrap=True,
        overflow=ft.TextOverflow.ELLIPSIS,
    )
    workdir_caption = ft.Container(
        content=workdir_text,
        alignment=ft.Alignment.CENTER,
        padding=ft.Padding.only(top=DS.spacing.sm),
        tooltip=workdir,
    )

    control = ft.Container(
        content=ft.Column(
            controls=[pill, workdir_caption],
            spacing=0,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
    )
    return Composer(
        control=control,
        field=field,
        send_button=send_button,
        icon_switcher=icon_switcher,
        workdir_text=workdir_text,
        workdir_caption=workdir_caption,
    )
