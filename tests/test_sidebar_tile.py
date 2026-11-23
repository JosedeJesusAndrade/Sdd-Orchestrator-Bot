"""Tests for the session tile atom and its pure relative-time helpers (P3)."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import cast

import flet as ft

from flet_app.components.sidebar_tile import (
    message_count_label,
    relative_time,
    session_tile,
)
from flet_app.core.design_system import DS
from services.agent_sessions import ConversationSummary


def _summary(
    *,
    real_id: str = "ses_abc123",
    title: str = "Saludo desde la app Flet",
    message_count: int = 3,
    adopted_name: str | None = "saludo-desde-la-app-flet",
) -> ConversationSummary:
    now_ms = int(time.time() * 1000)
    return ConversationSummary(
        real_id=real_id,
        title=title,
        created_at=now_ms - 60_000,
        updated_at=now_ms - 120_000,
        message_count=message_count,
        directory="C:/repo",
        agent="opencode",
        is_active=False,
        adopted_name=adopted_name,
    )


def _walk(control: object) -> Iterator[object]:
    yield control
    content = getattr(control, "content", None)
    if content is not None and not isinstance(content, str):
        yield from _walk(content)
    children = getattr(control, "controls", None)
    if children:
        for child in children:
            yield from _walk(child)


def _texts(control: object) -> list[str]:
    return [c.value for c in _walk(control) if isinstance(c, ft.Text) and c.value is not None]


def test_relative_time_labels() -> None:
    now = 1_800_000_000_000

    assert relative_time(now - 30_000, now_ms=now) == "ahora"
    assert relative_time(now - 5 * 60_000, now_ms=now) == "hace 5 min"
    assert relative_time(now - 3 * 3_600_000, now_ms=now) == "hace 3 h"
    assert relative_time(now - 2 * 86_400_000, now_ms=now) == "hace 2 d"
    assert relative_time(now - 30 * 86_400_000, now_ms=now).count("/") == 2


def test_message_count_label() -> None:
    assert message_count_label(1) == "1 mensaje"
    assert message_count_label(3) == "3 mensajes"


def test_session_tile_renders_title_meta_and_adopted_badge() -> None:
    calls: list[str] = []
    tile = cast(
        ft.Container,
        session_tile(_summary(), active=False, on_tap=lambda: calls.append("tap")),
    )

    texts = _texts(tile)
    assert "Saludo desde la app Flet" in texts
    assert any("3 mensajes" in text for text in texts)
    assert "saludo-desde-la-app-flet" in texts
    assert tile.data is not None
    assert tile.border_radius == DS.radii.tile

    cast(Callable[[object], None], tile.on_click)(None)
    assert calls == ["tap"]


def test_session_tile_active_applies_surface_highlight() -> None:
    active = cast(ft.Container, session_tile(_summary(), active=True, on_tap=lambda: None))
    inactive = cast(ft.Container, session_tile(_summary(), active=False, on_tap=lambda: None))

    assert active.bgcolor == DS.colors.surface
    assert inactive.bgcolor is None


def test_session_tile_edit_mode_edits_bot_bound_rows() -> None:
    tile = session_tile(_summary(), active=False, on_tap=lambda: None, edit_mode=True)

    assert any(isinstance(c, ft.TextField) for c in _walk(tile))


def test_session_tile_edit_mode_keeps_orphans_read_only() -> None:
    orphan = _summary(adopted_name=None)
    tile = session_tile(orphan, active=False, on_tap=lambda: None, edit_mode=True)

    assert not any(isinstance(c, ft.TextField) for c in _walk(tile))
    assert "Saludo desde la app Flet" in _texts(tile)
