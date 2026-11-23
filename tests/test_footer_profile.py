"""Tests for the fixed drawer footer profile strip (P3)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import cast

import flet as ft

from flet_app.components.footer_profile import footer_profile
from flet_app.core.design_system import DS


def _walk(control: object) -> Iterator[object]:
    yield control
    content = getattr(control, "content", None)
    if content is not None and not isinstance(content, str):
        yield from _walk(content)
    children = getattr(control, "controls", None)
    if children:
        for child in children:
            yield from _walk(child)


def test_footer_profile_renders_initial_name_and_dots() -> None:
    control = cast(ft.Container, footer_profile("marie", on_tap=lambda: None))

    avatars = [c for c in _walk(control) if isinstance(c, ft.CircleAvatar)]
    assert avatars and avatars[0].radius == DS.sizes.avatar // 2
    assert cast(ft.Text, avatars[0].content).value == "M"

    texts = [c.value for c in _walk(control) if isinstance(c, ft.Text)]
    assert "marie" in texts

    icons = [c for c in _walk(control) if isinstance(c, ft.Icon)]
    assert any(icon.icon == ft.Icons.MORE_HORIZ for icon in icons)


def test_footer_profile_tap_and_blank_initial() -> None:
    calls: list[str] = []
    control = cast(ft.Container, footer_profile("   ", on_tap=lambda: calls.append("tap")))

    avatars = [c for c in _walk(control) if isinstance(c, ft.CircleAvatar)]
    assert cast(ft.Text, avatars[0].content).value == "?"

    cast(Callable[[object], None], control.on_click)(None)
    assert calls == ["tap"]


def test_footer_profile_without_tap_is_inert() -> None:
    control = cast(ft.Container, footer_profile("marie"))

    assert control.on_click is None
    assert control.ink is False
