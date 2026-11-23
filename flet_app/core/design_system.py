"""Single source of truth for the Flet GUI design tokens (Slice 1).

Concept: *Gemini in form, DeepSeek in soul*. A near-black canvas slides into
deep blue and a restrained purple, with electric blue as the primary action
and lavender as the complement. All UI code imports :data:`DS` and never
hardcodes a color, size, spacing, radius or gradient.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import flet as ft


@dataclass(frozen=True, slots=True)
class Colors:
    """DeepSeek-inspired palette with a subtle lavender complement."""

    background: str = "#0A0A0A"
    # Mid/end stops sit ~20% closer to ``background`` than the Slice 1 values
    # (#0B1226 / #1A1030): the gradient stays perceptible but quieter.
    gradient_mid: str = "#0B1020"
    gradient_end: str = "#170F28"
    accent: str = "#4D6BFE"
    accent_soft: str = "#A78BFA"
    surface: str = "#16161A"
    surface_alt: str = "#101014"
    surface_raised: str = "#1B1B21"
    outline: str = "#24242C"
    text_primary: str = "#F2F2F5"
    text_secondary: str = "#9A9AA5"
    text_muted: str = "#4A4A52"
    white: str = "#FFFFFF"
    # Translucent black used behind modal layers (``#AARRGGBB``).
    scrim: str = "#8C000000"
    accent_pressed: str = "#3F58D9"
    danger: str = "#F87171"
    success: str = "#34D399"
    warning: str = "#FBBF24"

    @property
    def drawer_bg(self) -> str:
        """Drawer surface alias (single source of truth for ``#101014``)."""
        return self.surface_alt

    @staticmethod
    def alpha(color: str, opacity: float) -> str:
        """Return ``color`` with the given opacity (0.0 - 1.0)."""
        return ft.Colors.with_opacity(opacity, color)


@dataclass(frozen=True, slots=True)
class Typography:
    """Type scale: a large light greeting, body copy and muted captions."""

    greeting_size: int = 36
    greeting_weight: ft.FontWeight = ft.FontWeight.W_300
    title_size: int = 16
    title_weight: ft.FontWeight = ft.FontWeight.W_500
    body_size: int = 14
    body_weight: ft.FontWeight = ft.FontWeight.W_400
    small_size: int = 12
    muted_size: int = 11
    label_size: int = 13
    section_size: int = 12
    tile_title_size: int = 15
    meta_size: int = 12


@dataclass(frozen=True, slots=True)
class Spacing:
    """4/8/12/16/24/32 spacing scale."""

    xxs: int = 2
    xs: int = 4
    sm: int = 8
    md: int = 12
    lg: int = 16
    gutter: int = 20
    xl: int = 24
    xxl: int = 32


@dataclass(frozen=True, slots=True)
class Radii:
    """Corner radii: pill composer, cards and small chips."""

    small: int = 8
    tile: int = 12
    card: int = 18
    panel: int = 28
    pill: int = 999


@dataclass(frozen=True, slots=True)
class Sizes:
    """Fixed component dimensions (avatar, send button, chrome rows)."""

    avatar: int = 32
    send: int = 40
    top_bar_min: int = 56
    status_bar: int = 28
    tile_min_height: int = 52


@dataclass(frozen=True, slots=True)
class Motion:
    """Animation durations (ms) and the shared easing curve."""

    fast_ms: int = 120
    drawer_ms: int = 220
    slow_ms: int = 300
    welcome_ms: int = 300
    send_ms: int = 160
    curve: ft.AnimationCurve = ft.AnimationCurve.EASE_OUT


#: Flet 1.0.3 exposes no OS reduced-motion API; a single constant gates the
#: optional animations so a future platform check only has to flip this flag.
REDUCED_MOTION: bool = False


@dataclass(frozen=True, slots=True)
class Gradients:
    """Background gradient and the restrained lavender/blue glows."""

    @staticmethod
    def background() -> ft.LinearGradient:
        """Near-black at the top -> deep blue -> purple-blue at the bottom."""
        return ft.LinearGradient(
            begin=ft.Alignment.TOP_CENTER,
            end=ft.Alignment.BOTTOM_CENTER,
            colors=[
                _PALETTE.background,
                _PALETTE.gradient_mid,
                _PALETTE.gradient_end,
            ],
            stops=[0.0, 0.55, 1.0],
        )

    @staticmethod
    def lavender_glow(opacity: float = 0.22) -> ft.RadialGradient:
        """Soft light-purple halo that complements (never fights) the blue."""
        return ft.RadialGradient(
            center=ft.Alignment.CENTER,
            radius=1.0,
            colors=[
                Colors.alpha(_PALETTE.accent_soft, opacity),
                Colors.alpha(_PALETTE.accent_soft, 0.0),
            ],
        )

    @staticmethod
    def blue_glow(opacity: float = 0.16) -> ft.RadialGradient:
        """Faint electric-blue glow used near the composer."""
        return ft.RadialGradient(
            center=ft.Alignment.CENTER,
            radius=1.0,
            colors=[
                Colors.alpha(_PALETTE.accent, opacity),
                Colors.alpha(_PALETTE.accent, 0.0),
            ],
        )


_PALETTE = Colors()


@dataclass(frozen=True, slots=True)
class DesignSystem:
    """The complete token set exposed as ``DS``."""

    colors: Colors = field(default_factory=Colors)
    typography: Typography = field(default_factory=Typography)
    spacing: Spacing = field(default_factory=Spacing)
    radii: Radii = field(default_factory=Radii)
    sizes: Sizes = field(default_factory=Sizes)
    motion: Motion = field(default_factory=Motion)
    gradients: Gradients = field(default_factory=Gradients)


DS = DesignSystem()
