"""Thin status strip: workdir · branch · model · uptime + refresh (SS-01..SS-03).

Pure presentational row. It renders the loaded ``StatusSnapshot`` (or an
all-dashes row when ``None``/degraded), never polls, and only invokes the
``on_refresh`` callback. The ``LoadState`` is the caller's concern: P4 passes a
``None`` snapshot on a failure so the strip degrades to ``—``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import flet as ft

from flet_app.core.design_system import DS

if TYPE_CHECKING:
    from services.status_service import StatusSnapshot

_DASH = "\u2014"


def format_uptime(seconds: int | None) -> str:
    """Compact ``d/h/m`` uptime, or an em dash when unknown."""
    if seconds is None:
        return _DASH
    if seconds <= 0:
        return "0m"

    minutes = seconds // 60
    hours = minutes // 60
    days = hours // 24

    if days:
        return f"{days}d {hours % 24}h"
    if hours:
        return f"{hours}h {minutes % 60}m"
    return f"{minutes}m"


def _leaf(path: str) -> str:
    """Last path segment (either separator) so the thin row stays readable."""
    normalized = path.replace("\\", "/").rstrip("/")
    tail = normalized.rsplit("/", 1)[-1]
    return tail or path


def _field(value: str | None) -> str:
    return value.strip() if value and value.strip() else _DASH


def status_strip(
    snapshot: StatusSnapshot | None,
    *,
    on_refresh: Callable[[], None],
) -> ft.Control:
    """Render the four status fields plus a refresh button in one thin row."""
    if snapshot is None:
        parts = [_DASH, _DASH, _DASH, _DASH]
    else:
        parts = [
            _field(_leaf(snapshot.workdir) if snapshot.workdir else None),
            _field(snapshot.branch),
            _field(snapshot.model),
            format_uptime(snapshot.uptime_seconds),
        ]

    def handle_refresh(_: ft.Event[ft.IconButton]) -> None:
        on_refresh()

    return ft.Container(
        height=DS.sizes.status_bar,
        padding=ft.Padding.symmetric(horizontal=DS.spacing.xl),
        content=ft.Row(
            controls=[
                ft.Text(
                    value=" · ".join(parts),
                    size=DS.typography.muted_size,
                    color=DS.colors.text_secondary,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    expand=True,
                ),
                ft.IconButton(
                    icon=ft.Icons.REFRESH,
                    icon_size=DS.typography.body_size,
                    icon_color=DS.colors.text_secondary,
                    tooltip="Actualizar estado",
                    on_click=handle_refresh,
                    width=DS.sizes.status_bar,
                    height=DS.sizes.status_bar,
                ),
            ],
            spacing=DS.spacing.xs,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )
