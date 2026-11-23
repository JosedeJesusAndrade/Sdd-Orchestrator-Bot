"""Tests for the search pill and its single-pass shimmer (P3)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import cast

import flet as ft

from flet_app.components import search_placeholder as module
from flet_app.components.search_placeholder import (
    SEARCH_HINT,
    SearchPlaceholder,
    search_placeholder,
)
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


def test_search_placeholder_builds_icon_hint_and_tap() -> None:
    calls: list[str] = []
    placeholder = search_placeholder(lambda: calls.append("tap"))
    assert isinstance(placeholder, SearchPlaceholder)

    icons = [c for c in _walk(placeholder.control) if isinstance(c, ft.Icon)]
    assert any(icon.icon == ft.Icons.SEARCH for icon in icons)

    texts = [c.value for c in _walk(placeholder.control) if isinstance(c, ft.Text)]
    assert SEARCH_HINT in texts

    pill = cast(ft.Container, placeholder.control.controls[0])
    assert pill.border_radius == DS.radii.pill
    assert pill.bgcolor == DS.colors.surface
    cast(Callable[[object], None], pill.on_click)(None)
    assert calls == ["tap"]


def test_search_shimmer_sweeps_once_and_resets() -> None:
    placeholder = search_placeholder(lambda: None)

    assert placeholder.played is False
    placeholder.sweep()
    assert placeholder.played is True
    assert placeholder.shimmer.offset == module._SWEEP_OFFSET

    first_offset = placeholder.shimmer.offset
    placeholder.sweep()
    assert placeholder.shimmer.offset == first_offset

    placeholder.reset()
    assert placeholder.played is False
    assert placeholder.shimmer.offset == module._REST_OFFSET
