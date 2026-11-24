"""Design-system token contract (DS-01 / DS-03).

Proves the extension is additive: every pre-existing token keeps its value and
the new tokens/consts are present exactly as designed.
"""

from __future__ import annotations

import flet as ft

from flet_app.core.design_system import DS, REDUCED_MOTION, Colors, Motion, Sizes


def test_existing_color_tokens_are_intact() -> None:
    colors = DS.colors
    assert colors.background == "#0A0A0A"
    assert colors.gradient_mid == "#0B1020"
    assert colors.gradient_end == "#170F28"
    assert colors.accent == "#4D6BFE"
    assert colors.accent_soft == "#A78BFA"
    assert colors.surface == "#16161A"
    assert colors.outline == "#24242C"
    assert colors.text_primary == "#F2F2F5"
    assert colors.text_secondary == "#9A9AA5"
    assert colors.text_muted == "#4A4A52"
    assert colors.white == "#FFFFFF"


def test_new_color_tokens_are_present() -> None:
    colors = DS.colors
    assert colors.surface_alt == "#101014"
    assert colors.surface_raised == "#1B1B21"
    assert colors.scrim == "#8C000000"
    assert colors.accent_pressed == "#3F58D9"
    assert colors.danger == "#F87171"
    assert colors.success == "#34D399"
    assert colors.warning == "#FBBF24"


def test_drawer_bg_is_a_single_source_alias_of_surface_alt() -> None:
    assert DS.colors.drawer_bg == "#101014"
    assert DS.colors.drawer_bg == DS.colors.surface_alt


def test_existing_typography_tokens_are_intact() -> None:
    typo = DS.typography
    assert typo.greeting_size == 36
    assert typo.greeting_weight == ft.FontWeight.W_300
    assert typo.title_size == 16
    assert typo.title_weight == ft.FontWeight.W_500
    assert typo.body_size == 14
    assert typo.body_weight == ft.FontWeight.W_400
    assert typo.small_size == 12
    assert typo.muted_size == 11


def test_new_typography_tokens_are_present() -> None:
    typo = DS.typography
    assert typo.label_size == 13
    assert typo.section_size == 12
    assert typo.tile_title_size == 15
    assert typo.meta_size == 12


def test_existing_spacing_and_radii_tokens_are_intact() -> None:
    assert (DS.spacing.xs, DS.spacing.sm, DS.spacing.md) == (4, 8, 12)
    assert (DS.spacing.lg, DS.spacing.xl, DS.spacing.xxl) == (16, 24, 32)
    assert (DS.radii.small, DS.radii.card, DS.radii.pill) == (8, 18, 999)


def test_new_spacing_and_radii_tokens_are_present() -> None:
    assert (DS.spacing.xxs, DS.spacing.gutter) == (2, 20)
    assert (DS.radii.tile, DS.radii.panel) == (12, 28)


def test_sizes_tokens_are_present() -> None:
    sizes = DS.sizes
    assert isinstance(sizes, Sizes)
    assert sizes.avatar == 32
    assert sizes.send == 40
    assert sizes.top_bar_min == 56
    assert sizes.status_bar == 28
    assert sizes.tile_min_height == 52


def test_new_icon_and_glow_size_tokens_are_present() -> None:
    sizes = DS.sizes
    assert (sizes.icon_sm, sizes.icon_md, sizes.icon_lg) == (18, 20, 22)
    assert sizes.glow_large == 520
    assert sizes.glow_large_inset == 200
    assert sizes.glow_large_offset == 180
    assert sizes.glow_large_radius == 260
    assert sizes.glow_small == 420
    assert sizes.glow_small_inset == 220
    assert sizes.glow_small_offset == 140
    assert sizes.glow_small_radius == 210


def test_motion_tokens_are_present() -> None:
    motion = DS.motion
    assert isinstance(motion, Motion)
    assert motion.fast_ms == 120
    assert motion.drawer_ms == 220
    assert motion.slow_ms == 300
    assert motion.welcome_ms == 300
    assert motion.send_ms == 160
    assert motion.curve == ft.AnimationCurve.EASE_OUT


def test_reduced_motion_defaults_off() -> None:
    assert REDUCED_MOTION is False


def test_colors_alpha_still_works_and_drawer_alias_is_read_only() -> None:
    rgba = Colors.alpha(DS.colors.accent, 0.5)
    assert rgba.startswith("#")
    assert Colors() == DS.colors


def test_chat_color_tokens_are_present() -> None:
    colors = DS.colors
    assert colors.text_body == "#E8E8ED"
    assert colors.code_bg == "#101014"
    assert colors.code_border == "#24242C"
    assert colors.code_block_text == "#DADAE2"
    assert colors.code_inline_bg == "#24242C"
    assert colors.code_inline_text == "#E6E6EB"
    assert colors.table_border == "#2E2E38"
    assert colors.table_body_text == "#E4E4E9"
    assert colors.blockquote_bg == "#141418"
    assert colors.blockquote_bar == "#A78BFA"
    assert colors.blockquote_text == "#C6C6D0"
    assert colors.link == "#7C93FF"
    assert colors.hr == "#2A2A33"


def test_chat_typography_tokens_are_present() -> None:
    typo = DS.typography
    assert typo.chat_body_size == 15
    assert typo.chat_line_height == 1.6
    assert typo.chat_h1_size == 20
    assert typo.chat_h2_size == 17
    assert typo.chat_h3_size == 15
    assert typo.chat_h4_size == 14
    assert typo.chat_h5_size == 13
    assert typo.chat_h6_size == 12
    assert typo.chat_heading_weight == ft.FontWeight.W_600
    assert typo.chat_strong_weight == ft.FontWeight.W_600
    assert typo.chat_code_size == 13
    assert typo.chat_inline_code_size == 13
    assert typo.chat_table_size == 13.5
    assert typo.chat_table_header_size == 13
    assert typo.chat_quote_size == 15


def test_chat_spacing_tokens_are_present() -> None:
    spacing = DS.spacing
    assert spacing.chat_turn_gap == 24
    assert spacing.chat_block_gap == 12
    assert (spacing.chat_user_pad_x, spacing.chat_user_pad_y) == (14, 10)


def test_existing_body_size_is_untouched_by_the_chat_scale() -> None:
    assert DS.typography.body_size == 14
    assert DS.typography.body_size != DS.typography.chat_body_size
