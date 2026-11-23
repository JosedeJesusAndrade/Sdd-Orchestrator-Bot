"""Tests for the thin status strip and the pure ``format_uptime`` helper (P3)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import cast

import flet as ft

from flet_app.components.status_strip import format_uptime, status_strip
from flet_app.core.design_system import DS
from services.status_service import StatusSnapshot


def _walk(control: object) -> Iterator[object]:
    yield control
    content = getattr(control, "content", None)
    if content is not None and not isinstance(content, str):
        yield from _walk(content)
    children = getattr(control, "controls", None)
    if children:
        for child in children:
            yield from _walk(child)


def _snapshot(
    *,
    workdir: str = "C:/repo/proj",
    branch: str = "main",
    model: str = "deepseek/flash",
    uptime_seconds: int | None = 3660,
) -> StatusSnapshot:
    return StatusSnapshot(
        session_name="work",
        session_real_id="ses_1",
        model=model,
        branch=branch,
        workdir=workdir,
        provider="opencode",
        session_count=3,
        created=None,
        last_used=None,
        prompt_count=0,
        uptime_seconds=uptime_seconds,
    )


def test_format_uptime() -> None:
    assert format_uptime(None) == "\u2014"
    assert format_uptime(0) == "0m"
    assert format_uptime(90) == "1m"
    assert format_uptime(3660) == "1h 1m"
    assert format_uptime(90_000) == "1d 1h"


def test_status_strip_renders_fields_and_refresh() -> None:
    calls: list[str] = []
    control = cast(ft.Container, status_strip(_snapshot(), on_refresh=lambda: calls.append("r")))

    assert control.height == DS.sizes.status_bar
    texts = [c.value for c in _walk(control) if isinstance(c, ft.Text)]
    line = next(text for text in texts if text and "·" in text)
    assert "proj" in line
    assert "main" in line
    assert "deepseek/flash" in line
    assert "1h 1m" in line

    refresh = next(c for c in _walk(control) if isinstance(c, ft.IconButton))
    cast(Callable[[object], None], refresh.on_click)(None)
    assert calls == ["r"]


def test_status_strip_degrades_to_dashes_on_none() -> None:
    control = cast(ft.Container, status_strip(None, on_refresh=lambda: None))

    texts = [c.value for c in _walk(control) if isinstance(c, ft.Text)]
    line = next(text for text in texts if text and "·" in text)
    assert line == "\u2014 · \u2014 · \u2014 · \u2014"


def test_status_strip_empty_fields_become_dashes() -> None:
    control = cast(
        ft.Container,
        status_strip(
            _snapshot(branch="  ", model="", uptime_seconds=None), on_refresh=lambda: None
        ),
    )

    texts = [c.value for c in _walk(control) if isinstance(c, ft.Text)]
    line = next(text for text in texts if text and "·" in text)
    assert "\u2014 · \u2014 · \u2014" in line
