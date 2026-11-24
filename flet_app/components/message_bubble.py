"""Message presentation atoms: markdown rendering and role styling.

Slice 1 keeps the distinction subtle: the user gets a right-aligned charcoal
bubble, the assistant gets clean full-width markdown, Gemini-style.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import flet as ft

from flet_app.core.design_system import DS

_MONO_FONT = "monospace"
_HEADING_TOP = {1: 12, 2: 12, 3: 10, 4: 8, 5: 8, 6: 8}
_HEADING_HEIGHT = {1: 1.3, 2: 1.35, 3: 1.4, 4: 1.45, 5: 1.5, 6: 1.5}
_BLANK_LINE_RUN = re.compile(r"\n{3,}")


def _heading_style(size: int, level: int) -> tuple[ft.TextStyle, ft.Padding]:
    """Text style and top/bottom padding for one heading level."""
    color = DS.colors.text_secondary if level == 6 else DS.colors.text_primary
    style = ft.TextStyle(
        size=size,
        weight=DS.typography.chat_heading_weight,
        height=_HEADING_HEIGHT[level],
        color=color,
    )
    return style, ft.Padding.only(top=_HEADING_TOP[level], bottom=0)


def _chat_markdown_style() -> tuple[ft.MarkdownStyleSheet, ft.MarkdownStyleSheet]:
    """Return the (general, code-block) markdown style sheets from the tokens."""
    colors = DS.colors
    typo = DS.typography

    h1_style, h1_pad = _heading_style(typo.chat_h1_size, 1)
    h2_style, h2_pad = _heading_style(typo.chat_h2_size, 2)
    h3_style, h3_pad = _heading_style(typo.chat_h3_size, 3)
    h4_style, h4_pad = _heading_style(typo.chat_h4_size, 4)
    h5_style, h5_pad = _heading_style(typo.chat_h5_size, 5)
    h6_style, h6_pad = _heading_style(typo.chat_h6_size, 6)

    general = ft.MarkdownStyleSheet(
        p_text_style=ft.TextStyle(
            size=typo.chat_body_size,
            weight=ft.FontWeight.W_400,
            height=typo.chat_line_height,
            color=colors.text_body,
        ),
        h1_text_style=h1_style,
        h1_padding=h1_pad,
        h2_text_style=h2_style,
        h2_padding=h2_pad,
        h3_text_style=h3_style,
        h3_padding=h3_pad,
        h4_text_style=h4_style,
        h4_padding=h4_pad,
        h5_text_style=h5_style,
        h5_padding=h5_pad,
        h6_text_style=h6_style,
        h6_padding=h6_pad,
        strong_text_style=ft.TextStyle(
            weight=typo.chat_strong_weight,
            color=colors.text_primary,
        ),
        em_text_style=ft.TextStyle(italic=True),
        code_text_style=ft.TextStyle(
            size=typo.chat_inline_code_size,
            weight=ft.FontWeight.W_400,
            height=1.5,
            color=colors.code_inline_text,
            bgcolor=colors.code_inline_bg,
            font_family=_MONO_FONT,
        ),
        a_text_style=ft.TextStyle(
            color=colors.link,
            decoration=ft.TextDecoration.UNDERLINE,
            decoration_color=colors.link,
        ),
        blockquote_text_style=ft.TextStyle(
            size=typo.chat_quote_size,
            color=colors.blockquote_text,
        ),
        blockquote_decoration=ft.BoxDecoration(
            bgcolor=colors.blockquote_bg,
            border=ft.Border.only(left=ft.BorderSide(3, colors.blockquote_bar)),
        ),
        blockquote_padding=ft.Padding.only(left=16, top=8, right=16, bottom=8),
        blockquote_alignment=ft.MainAxisAlignment.START,
        block_spacing=DS.spacing.chat_block_gap,
        list_indent=20,
        list_bullet_text_style=ft.TextStyle(
            color=colors.text_secondary,
            height=typo.chat_line_height,
        ),
        table_head_text_style=ft.TextStyle(
            size=typo.chat_table_header_size,
            weight=typo.chat_heading_weight,
            color=colors.text_primary,
        ),
        table_body_text_style=ft.TextStyle(
            size=typo.chat_table_size,
            height=1.45,
            color=colors.table_body_text,
        ),
        table_head_text_align=ft.TextAlign.LEFT,
        table_cells_padding=ft.Padding.symmetric(vertical=8, horizontal=12),
        table_cells_decoration=ft.BoxDecoration(
            border=ft.Border.only(
                right=ft.BorderSide(0.5, colors.table_border),
                bottom=ft.BorderSide(0.5, colors.table_border),
            ),
        ),
        horizontal_rule_decoration=ft.BoxDecoration(bgcolor=colors.hr),
    )

    code = ft.MarkdownStyleSheet(
        code_text_style=ft.TextStyle(
            size=typo.chat_code_size,
            height=1.5,
            color=colors.code_block_text,
            font_family=_MONO_FONT,
        ),
        codeblock_decoration=ft.BoxDecoration(
            bgcolor=colors.code_bg,
            border=ft.Border.all(1, colors.code_border),
            border_radius=10,
        ),
        codeblock_padding=ft.Padding.symmetric(vertical=12, horizontal=12),
        codeblock_alignment=ft.MainAxisAlignment.START,
    )

    return general, code


def _normalize_markdown(value: str) -> str:
    """Trim the edges and collapse runs of blank lines to a single gap."""
    return _BLANK_LINE_RUN.sub("\n\n", value.strip())


def build_markdown(
    value: str,
    *,
    on_tap_link: Callable[..., Any] | None = None,
) -> ft.Markdown:
    """Render GITHUB_WEB markdown with the chat typography and code theme."""
    md_style_sheet, code_style_sheet = _chat_markdown_style()
    return ft.Markdown(
        value=_normalize_markdown(value),
        selectable=True,
        extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
        code_theme=ft.MarkdownCodeTheme.ATOM_ONE_DARK,
        md_style_sheet=md_style_sheet,
        code_style_sheet=code_style_sheet,
        on_tap_link=on_tap_link,
    )


def user_message(content: ft.Control) -> ft.Control:
    """Right-aligned surface bubble for the user's turn.

    ``wrap=True`` bounds the bubble to the conversation width, so long
    Markdown wraps instead of overflowing off-screen while short turns keep
    their intrinsic width.
    """
    bubble = ft.Container(
        content=content,
        bgcolor=DS.colors.surface,
        border=ft.Border.all(1, DS.colors.outline),
        border_radius=DS.radii.card,
        padding=ft.Padding.symmetric(
            horizontal=DS.spacing.chat_user_pad_x,
            vertical=DS.spacing.chat_user_pad_y,
        ),
    )
    return ft.Row(
        controls=[bubble],
        wrap=True,
        alignment=ft.MainAxisAlignment.END,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )


def assistant_message(content: ft.Control) -> ft.Control:
    """Plain, full-width assistant turn (no bubble, no chrome)."""
    return ft.Container(
        content=content,
        padding=ft.Padding.symmetric(vertical=0),
    )
