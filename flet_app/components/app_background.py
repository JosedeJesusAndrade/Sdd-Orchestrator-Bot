"""Single shared app shell: flat canvas, optional welcome gradient + glows.

Both the chat and the conversations screens used to duplicate this
``Container``/``Stack``/glow/``SafeArea`` tree. They now share one helper so the
surface stays a single source of truth (DS-02). The gradient and its two radial
glows are opt-in (``gradient=True``) — the chat only wants them on the welcome
state; the flat ``DS.colors.background`` canvas is the default (CH-01).
"""

from __future__ import annotations

import flet as ft

from flet_app.core.design_system import DS


def _glows() -> list[ft.Control]:
    """The lavender (top-right) and blue (bottom-left) corner halos."""
    return [
        ft.Container(
            width=520,
            height=520,
            top=-200,
            right=-180,
            border_radius=260,
            gradient=DS.gradients.lavender_glow(),
            ignore_interactions=True,
        ),
        ft.Container(
            width=420,
            height=420,
            bottom=-220,
            left=-140,
            border_radius=210,
            gradient=DS.gradients.blue_glow(),
            ignore_interactions=True,
        ),
    ]


def app_background(content: ft.Control, *, gradient: bool = False) -> ft.Container:
    """Wrap ``content`` in the app shell, optionally with the welcome gradient.

    Always applies ``SafeArea`` so the chrome clears the notch/status bar on a
    real APK; glows ride along only when ``gradient`` is requested.
    """
    layers: list[ft.Control] = _glows() if gradient else []
    layers.append(
        ft.SafeArea(
            content=content,
            expand=True,
            avoid_intrusions_top=True,
            avoid_intrusions_bottom=True,
            avoid_intrusions_left=False,
            avoid_intrusions_right=False,
        )
    )

    return ft.Container(
        expand=True,
        bgcolor=DS.colors.background,
        gradient=DS.gradients.background() if gradient else None,
        content=ft.Stack(
            expand=True,
            fit=ft.StackFit.EXPAND,
            controls=layers,
        ),
    )
