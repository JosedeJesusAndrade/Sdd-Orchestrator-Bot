"""Tests for the chat markdown/typography redesign (message bubbles).

The chat render is presentation-only: these pin the design-system values on
the ``ft.Markdown`` style sheets (body scale, headings, inline/fenced code,
tables, blockquotes, links, rules) plus the bubble spacing. No client needed.
"""

from __future__ import annotations

import flet as ft

from flet_app.components.message_bubble import (
    _normalize_markdown,
    assistant_message,
    build_markdown,
    user_message,
)
from flet_app.core.design_system import DS


def _sheets() -> tuple[ft.MarkdownStyleSheet, ft.MarkdownStyleSheet]:
    md = build_markdown("hola")
    assert md.md_style_sheet is not None
    assert md.code_style_sheet is not None
    return md.md_style_sheet, md.code_style_sheet


def _as_padding(value: object) -> ft.Padding:
    assert isinstance(value, ft.Padding)
    return value


def test_build_markdown_returns_markdown_with_both_style_sheets() -> None:
    md = build_markdown("hola")

    assert isinstance(md, ft.Markdown)
    assert md.md_style_sheet is not None
    assert md.code_style_sheet is not None


def test_build_markdown_uses_github_web_atom_one_dark_and_is_selectable() -> None:
    md = build_markdown("hola")

    assert md.extension_set == ft.MarkdownExtensionSet.GITHUB_WEB
    assert md.code_theme == ft.MarkdownCodeTheme.ATOM_ONE_DARK
    assert md.selectable is True


def test_paragraph_uses_the_body_tokens() -> None:
    md, _ = _sheets()
    style = md.p_text_style
    assert style is not None
    assert style.size == DS.typography.chat_body_size == 15
    assert style.weight == ft.FontWeight.W_400
    assert style.height == DS.typography.chat_line_height == 1.6
    assert style.color == DS.colors.text_body == "#E8E8ED"


def test_heading_scale_and_weight() -> None:
    md, _ = _sheets()
    styles = (
        (md.h1_text_style, DS.typography.chat_h1_size, 20),
        (md.h2_text_style, DS.typography.chat_h2_size, 17),
        (md.h3_text_style, DS.typography.chat_h3_size, 15),
        (md.h4_text_style, DS.typography.chat_h4_size, 14),
        (md.h5_text_style, DS.typography.chat_h5_size, 13),
        (md.h6_text_style, DS.typography.chat_h6_size, 12),
    )
    for style, token, literal in styles:
        assert style is not None
        assert style.size == token == literal
        assert style.weight == DS.typography.chat_heading_weight == ft.FontWeight.W_600


def test_heading_padding_is_top_only() -> None:
    md, _ = _sheets()
    for padding, top in (
        (md.h1_padding, 12),
        (md.h2_padding, 12),
        (md.h3_padding, 10),
        (md.h4_padding, 8),
        (md.h5_padding, 8),
        (md.h6_padding, 8),
    ):
        assert padding is not None
        resolved = _as_padding(padding)
        assert resolved.top == top
        assert resolved.bottom == 0


def test_strong_is_six_hundred_and_em_is_italic() -> None:
    md, _ = _sheets()
    strong = md.strong_text_style
    assert strong is not None
    assert strong.weight == DS.typography.chat_strong_weight == ft.FontWeight.W_600
    assert strong.color == DS.colors.text_primary
    em = md.em_text_style
    assert em is not None
    assert em.italic is True


def test_inline_code_uses_the_inline_tokens() -> None:
    md, _ = _sheets()
    style = md.code_text_style
    assert style is not None
    assert style.size == DS.typography.chat_inline_code_size == 13
    assert style.bgcolor == DS.colors.code_inline_bg == "#24242C"
    assert style.color == DS.colors.code_inline_text == "#E6E6EB"
    assert style.font_family == "monospace"


def test_code_block_decoration_text_and_padding() -> None:
    _, code = _sheets()
    text = code.code_text_style
    assert text is not None
    assert text.size == DS.typography.chat_code_size == 13
    assert text.color == DS.colors.code_block_text == "#DADAE2"

    decoration = code.codeblock_decoration
    assert decoration is not None
    assert decoration.bgcolor == DS.colors.code_bg == "#101014"
    assert decoration.border_radius == 10
    border = decoration.border
    assert border is not None
    assert border.top.width == 1
    assert border.top.color == DS.colors.code_border

    padding = code.codeblock_padding
    assert padding is not None
    resolved = _as_padding(padding)
    assert resolved.top == 12
    assert resolved.left == 12


def test_table_head_and_body_styles() -> None:
    md, _ = _sheets()
    head = md.table_head_text_style
    assert head is not None
    assert head.size == DS.typography.chat_table_header_size == 13
    assert head.weight == ft.FontWeight.W_600
    assert head.color == DS.colors.text_primary
    assert md.table_head_text_align == ft.TextAlign.LEFT

    body = md.table_body_text_style
    assert body is not None
    assert body.size == DS.typography.chat_table_size == 13.5
    assert body.height == 1.45
    assert body.color == DS.colors.table_body_text == "#E4E4E9"


def test_table_cells_border_and_padding() -> None:
    md, _ = _sheets()
    padding = _as_padding(md.table_cells_padding)
    assert padding.top == 8
    assert padding.left == 12

    decoration = md.table_cells_decoration
    assert decoration is not None
    border = decoration.border
    assert border is not None
    assert border.right.width == 0.5
    assert border.right.color == DS.colors.table_border == "#2E2E38"
    assert border.bottom.color == DS.colors.table_border


def test_blockquote_decoration_text_and_padding() -> None:
    md, _ = _sheets()
    text = md.blockquote_text_style
    assert text is not None
    assert text.size == DS.typography.chat_quote_size == 15
    assert text.color == DS.colors.blockquote_text == "#C6C6D0"

    decoration = md.blockquote_decoration
    assert decoration is not None
    assert decoration.bgcolor == DS.colors.blockquote_bg == "#141418"
    border = decoration.border
    assert border is not None
    assert border.left.width == 3
    assert border.left.color == DS.colors.blockquote_bar == "#A78BFA"

    padding = _as_padding(md.blockquote_padding)
    assert padding.left == 16
    assert padding.top == 8


def test_link_rule_list_and_block_spacing() -> None:
    md, _ = _sheets()
    link = md.a_text_style
    assert link is not None
    assert link.color == DS.colors.link == "#7C93FF"
    assert link.decoration == ft.TextDecoration.UNDERLINE
    assert link.decoration_color == DS.colors.link

    rule = md.horizontal_rule_decoration
    assert rule is not None
    assert rule.bgcolor == DS.colors.hr == "#2A2A33"

    assert md.list_indent == 20
    bullet = md.list_bullet_text_style
    assert bullet is not None
    assert bullet.color == DS.colors.text_secondary == "#9A9AA5"

    assert md.block_spacing == DS.spacing.chat_block_gap == 12


def test_normalize_collapses_blank_lines_and_preserves_emojis() -> None:
    assert _normalize_markdown("a\n\n\n\nb") == "a\n\nb"
    assert _normalize_markdown("  \n\n hola \N{ROCKET} \n\n\n") == "hola \N{ROCKET}"


def test_build_markdown_normalizes_the_value() -> None:
    md = build_markdown("hola\n\n\n\nmundo")

    assert md.value == "hola\n\nmundo"


def test_user_message_bubble_padding_and_radius() -> None:
    row = user_message(ft.Text("hola"))
    assert isinstance(row, ft.Row)
    bubble = row.controls[0]
    assert isinstance(bubble, ft.Container)
    assert bubble.border_radius == DS.radii.card == 18
    padding = _as_padding(bubble.padding)
    assert padding.left == DS.spacing.chat_user_pad_x == 14
    assert padding.top == DS.spacing.chat_user_pad_y == 10


def test_assistant_message_has_no_vertical_padding() -> None:
    container = assistant_message(ft.Text("hola"))
    assert isinstance(container, ft.Container)
    padding = _as_padding(container.padding)
    assert padding.top == 0
    assert padding.bottom == 0
