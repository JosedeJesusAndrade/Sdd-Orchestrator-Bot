"""Muted, uppercase, letter-spaced section header for the drawer (SH-03)."""

from __future__ import annotations

import flet as ft

from flet_app.core.design_system import DS


def section_header(label: str) -> ft.Control:
    """Render ``label`` as a small muted caption above a session bucket."""
    return ft.Container(
        padding=ft.Padding.only(
            left=DS.spacing.md,
            right=DS.spacing.md,
            top=DS.spacing.md,
            bottom=DS.spacing.xs,
        ),
        content=ft.Text(
            value=label.upper(),
            size=DS.typography.section_size,
            weight=DS.typography.title_weight,
            color=DS.colors.text_muted,
            style=ft.TextStyle(letter_spacing=DS.spacing.xxs),
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        ),
    )
