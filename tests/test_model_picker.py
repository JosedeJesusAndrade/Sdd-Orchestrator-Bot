"""Tests for the grouped model picker dialog (P3)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import cast

import flet as ft

from flet_app.components.model_picker import model_picker
from flet_app.ui_strings import MODELS_EMPTY
from services.model_catalog import ModelCatalogSnapshot, ModelGroup, ModelOption


def _walk(control: object) -> Iterator[object]:
    yield control
    content = getattr(control, "content", None)
    if content is not None and not isinstance(content, str):
        yield from _walk(content)
    children = getattr(control, "controls", None)
    if children:
        for child in children:
            yield from _walk(child)
    for slot in ("title", "subtitle", "leading", "trailing"):
        child = getattr(control, slot, None)
        if isinstance(child, ft.Control):
            yield from _walk(child)


def _snapshot(*options: ModelOption) -> ModelCatalogSnapshot:
    groups: list[ModelGroup] = []
    for option in options:
        existing = next((g for g in groups if g.provider == option.provider), None)
        if existing is None:
            groups.append(
                ModelGroup(
                    provider=option.provider,
                    label=option.provider.title(),
                    options=(option,),
                )
            )
        else:
            groups[groups.index(existing)] = ModelGroup(
                provider=existing.provider,
                label=existing.label,
                options=(*existing.options, option),
            )
    return ModelCatalogSnapshot(
        options=tuple(options),
        groups=tuple(groups),
        source="live",
        degraded=False,
        fetched_at=datetime.now(UTC),
    )


def test_model_picker_groups_and_marks_default() -> None:
    chosen: list[str] = []
    flash = ModelOption(id="deepseek/flash", provider="deepseek", label="Flash", is_default=True)
    pro = ModelOption(id="deepseek/pro", provider="deepseek", label="Pro", is_default=False)
    dialog = model_picker(_snapshot(flash, pro), on_select=chosen.append)

    texts = [c.value for c in _walk(dialog) if isinstance(c, ft.Text)]
    assert "DEEPSEEK" in texts
    assert "Flash" in texts and "Pro" in texts

    tiles = [c for c in _walk(dialog) if isinstance(c, ft.ListTile)]
    assert len(tiles) == 2
    default_tile = next(t for t in tiles if t.trailing is not None)
    assert isinstance(default_tile.trailing, ft.Icon)
    assert default_tile.trailing.icon == ft.Icons.CHECK

    cast(Callable[[object], None], default_tile.on_click)(None)
    assert chosen == ["deepseek/flash"]


def test_model_picker_empty_copy() -> None:
    dialog = model_picker(_snapshot(), on_select=lambda _: None)

    texts = [c.value for c in _walk(dialog) if isinstance(c, ft.Text)]
    assert MODELS_EMPTY in texts
    assert not any(isinstance(c, ft.ListTile) for c in _walk(dialog))


def test_model_picker_empty_offers_retry_when_wired() -> None:
    calls: list[str] = []
    dialog = model_picker(_snapshot(), on_select=lambda _: None, on_retry=lambda: calls.append("r"))

    button = next(c for c in _walk(dialog) if isinstance(c, ft.FilledButton))
    cast(Callable[[object], None], button.on_click)(None)
    assert calls == ["r"]
