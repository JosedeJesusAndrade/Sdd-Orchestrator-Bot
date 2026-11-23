"""Tests for the muted uppercase section header (P3)."""

from __future__ import annotations

from typing import cast

import flet as ft

from flet_app.components.section_header import section_header
from flet_app.core.design_system import DS


def test_section_header_is_uppercase_muted_and_token_sized() -> None:
    control = cast(ft.Container, section_header("Últimos 7 días"))
    text = cast(ft.Text, control.content)

    assert text.value == "ÚLTIMOS 7 DÍAS"
    assert text.size == DS.typography.section_size
    assert text.color == DS.colors.text_muted
