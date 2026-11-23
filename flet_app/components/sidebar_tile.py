"""Session tile + the pure relative-time / message-count helpers (SH-04 / EM-04).

The tile is a pure presentational atom: it takes one ``ConversationSummary`` in
and renders the title (a ``Text``, or a per-row ``TextField`` while edit mode is
on and the row is bot-bound), the muted meta line (relative time + message
count) and the active / adopted affordances. It never touches a service: the
tap is a fire-and-forget ``on_tap()`` callback.

``relative_time`` / ``message_count_label`` live here (the conversations screen
that used to own them is retired in this change).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

import flet as ft

from flet_app.core.design_system import DS

if TYPE_CHECKING:
    from services.agent_sessions import ConversationSummary


def relative_time(timestamp_ms: int, *, now_ms: int | None = None) -> str:
    """Format an epoch-ms timestamp as a short Spanish relative label."""
    now = int(time.time() * 1000) if now_ms is None else now_ms
    delta = now - timestamp_ms
    if delta < 60_000:
        return "ahora"

    minutes = delta // 60_000
    if minutes < 60:
        return f"hace {minutes} min"

    hours = minutes // 60
    if hours < 24:
        return f"hace {hours} h"

    days = hours // 24
    if days < 7:
        return f"hace {days} d"

    return time.strftime("%d/%m/%Y", time.localtime(timestamp_ms / 1000))


def message_count_label(count: int) -> str:
    """Human label for the stored message count (includes non-text turns)."""
    return "1 mensaje" if count == 1 else f"{count} mensajes"


def _title_value(session: ConversationSummary) -> str:
    """Display title, falling back to an em dash for an untitled root."""
    return session.title.strip() or "\u2014"


def _adopted_badge(name: str) -> ft.Container:
    """Small pill that marks a bot-adopted (renamable) row."""
    return ft.Container(
        content=ft.Text(
            value=name,
            size=DS.typography.muted_size,
            color=DS.colors.accent_soft,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        ),
        padding=ft.Padding.symmetric(
            horizontal=DS.spacing.sm,
            vertical=DS.spacing.xs,
        ),
        border_radius=DS.radii.pill,
        bgcolor=DS.colors.alpha(DS.colors.accent_soft, 0.14),
    )


def session_tile(
    session: ConversationSummary,
    *,
    active: bool,
    on_tap: Callable[[], None],
    edit_mode: bool = False,
) -> ft.Control:
    """Render one session row: title, muted meta, active/adopted affordances.

    ``edit_mode`` swaps the title for a dense inline ``TextField`` only when the
    row is bot-bound (``adopted_name`` set); orphans stay read-only (EM-02).
    """
    if edit_mode and session.adopted_name is not None:
        title: ft.Control = ft.TextField(
            value=session.title,
            dense=True,
            filled=False,
            border=ft.NoInputBorder(),
            text_size=DS.typography.tile_title_size,
            color=DS.colors.text_primary,
            cursor_color=DS.colors.accent,
            content_padding=ft.Padding.symmetric(vertical=DS.spacing.xxs),
            multiline=False,
        )
    else:
        title = ft.Text(
            value=_title_value(session),
            size=DS.typography.tile_title_size,
            weight=DS.typography.body_weight,
            color=DS.colors.text_primary,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )

    meta = ft.Text(
        value=(
            f"{relative_time(session.updated_at)} · {message_count_label(session.message_count)}"
        ),
        size=DS.typography.meta_size,
        color=DS.colors.text_secondary,
        max_lines=1,
        overflow=ft.TextOverflow.ELLIPSIS,
    )

    trailing: list[ft.Control] = []
    if active:
        trailing.append(
            ft.Container(
                width=DS.spacing.sm,
                height=DS.spacing.sm,
                border_radius=DS.spacing.xs,
                bgcolor=DS.colors.accent,
                tooltip="Conversación activa",
            )
        )
    if session.adopted_name is not None:
        trailing.append(_adopted_badge(session.adopted_name))

    def handle_tap(_: ft.Event[ft.Container]) -> None:
        on_tap()

    row: list[ft.Control] = [
        ft.Column(controls=[title, meta], spacing=DS.spacing.xxs, expand=True),
    ]
    if trailing:
        row.append(
            ft.Row(
                controls=trailing,
                spacing=DS.spacing.sm,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        )

    return ft.Container(
        content=ft.Row(
            controls=row,
            spacing=DS.spacing.md,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(
            horizontal=DS.spacing.md,
            vertical=DS.spacing.sm,
        ),
        bgcolor=DS.colors.surface if active else None,
        border=ft.Border.all(
            1,
            DS.colors.accent if active else DS.colors.outline,
        ),
        border_radius=DS.radii.tile,
        ink=True,
        on_click=handle_tap,
        data=session,
    )
