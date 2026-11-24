"""Search pill with a single-pass shimmer (SH-03 / D4).

The tap is a plain ``on_tap()`` callback (the shell shows the ``Próximamente``
toast). The shimmer is one sweep per drawer open — the shell calls
:meth:`SearchPlaceholder.sweep` on open (via ``Drawer.begin_shimmer``), which
starts it exactly once (and is a no-op under ``REDUCED_MOTION``), and
:meth:`SearchPlaceholder.reset` re-arms it on dismiss for the next open. No
timer, no background task, zero I/O.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import flet as ft

from flet_app.core.design_system import DS, REDUCED_MOTION

SEARCH_HINT = "Buscar en las conversaciones…"

_SHADOW_WIDTH = DS.spacing.xxl
_REST_OFFSET = ft.Offset(-1, 0)
_SWEEP_OFFSET = ft.Offset(6, 0)


def _pill(on_tap: Callable[[], None]) -> ft.Container:
    """The rounded search surface: magnifier icon + muted hint text."""

    def handle_tap(_: ft.Event[ft.Container]) -> None:
        on_tap()

    return ft.Container(
        bgcolor=DS.colors.surface,
        border=ft.Border.all(1, DS.colors.outline),
        border_radius=DS.radii.pill,
        padding=ft.Padding.symmetric(
            horizontal=DS.spacing.md,
            vertical=DS.spacing.sm,
        ),
        ink=True,
        on_click=handle_tap,
        content=ft.Row(
            controls=[
                ft.Icon(
                    ft.Icons.SEARCH,
                    size=DS.typography.label_size,
                    color=DS.colors.text_secondary,
                ),
                ft.Text(
                    value=SEARCH_HINT,
                    size=DS.typography.label_size,
                    color=DS.colors.text_secondary,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    expand=True,
                ),
            ],
            spacing=DS.spacing.sm,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )


@dataclass
class SearchPlaceholder:
    """The pill plus the controllable single-pass shimmer overlay."""

    control: ft.Stack
    shimmer: ft.Container
    played: bool = False

    def reset(self) -> None:
        """Re-arm the shimmer so the next open can sweep once more."""
        self.played = False
        self.shimmer.animate_offset = None
        self.shimmer.offset = _REST_OFFSET

    def sweep(self) -> None:
        """Run the single shimmer pass (idempotent per open)."""
        if self.played or REDUCED_MOTION:
            return
        self.played = True
        self.shimmer.animate_offset = ft.Animation(
            DS.motion.slow_ms,
            DS.motion.curve,
        )
        self.shimmer.offset = _SWEEP_OFFSET


def search_placeholder(on_tap: Callable[[], None]) -> SearchPlaceholder:
    """Build the search pill and its one-shot shimmer overlay."""
    shimmer = ft.Container(
        width=_SHADOW_WIDTH,
        top=0,
        bottom=0,
        left=0,
        offset=_REST_OFFSET,
        ignore_interactions=True,
        gradient=ft.LinearGradient(
            begin=ft.Alignment.CENTER_LEFT,
            end=ft.Alignment.CENTER_RIGHT,
            colors=[
                DS.colors.alpha(DS.colors.white, 0.0),
                DS.colors.alpha(DS.colors.white, 0.06),
                DS.colors.alpha(DS.colors.white, 0.0),
            ],
        ),
    )
    control = ft.Stack(
        controls=[_pill(on_tap), shimmer],
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
    )
    return SearchPlaceholder(control=control, shimmer=shimmer)
