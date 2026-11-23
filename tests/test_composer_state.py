"""Tests for the composer state machine (CP-02/CP-03/CP-04).

The unit tests validate the configuration and the send↔cancel morph. The real
Enter-newline and autogrow behavior depends on the on-device keyboard, so the
manual APK check is deferred (A7).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

import flet as ft

from flet_app.components.composer import build_composer
from flet_app.core.design_system import DS


def test_field_is_multiline_one_to_five_lines_and_never_submits() -> None:
    composer = build_composer(workdir=".")

    assert composer.field.multiline is True
    assert composer.field.min_lines == 1
    assert composer.field.max_lines == 5
    assert composer.field.on_submit is None
    assert composer.field.keyboard_type == ft.KeyboardType.MULTILINE


def test_button_starts_as_send() -> None:
    composer = build_composer(workdir=".")

    assert composer.running is False
    icon = cast(ft.Icon, composer.icon_switcher.content)
    assert icon.icon == ft.Icons.ARROW_UPWARD
    assert composer.send_button.tooltip == "Enviar"
    assert composer.icon_switcher.duration == DS.motion.send_ms


def test_set_running_morphs_to_cancel_and_back() -> None:
    composer = build_composer(workdir=".")

    composer.set_running(True)

    assert composer.running is True
    icon = cast(ft.Icon, composer.icon_switcher.content)
    assert icon.icon == ft.Icons.STOP_CIRCLE
    assert composer.send_button.tooltip == "Cancelar"
    assert composer.send_button.bgcolor == DS.colors.danger

    composer.set_running(False)

    assert composer.running is False
    assert cast(ft.Icon, composer.icon_switcher.content).icon == ft.Icons.ARROW_UPWARD
    assert composer.send_button.bgcolor == DS.colors.accent


def test_set_busy_disables_the_field() -> None:
    composer = build_composer(workdir=".")

    composer.set_busy(True)
    assert composer.field.disabled is True

    composer.set_busy(False)
    assert composer.field.disabled is False


async def test_dispatch_sends_when_idle() -> None:
    composer = build_composer(workdir=".")
    sent: list[str] = []
    cancelled: list[str] = []

    async def on_send(event: object = None) -> None:
        sent.append("send")

    async def on_cancel(event: object = None) -> None:
        cancelled.append("cancel")

    composer.bind(on_send, on_cancel=on_cancel)
    click = cast(Callable[[object], Awaitable[None]], composer.send_button.on_click)
    await click(None)

    assert sent == ["send"]
    assert cancelled == []


async def test_dispatch_cancels_while_running() -> None:
    composer = build_composer(workdir=".")
    sent: list[str] = []
    cancelled: list[str] = []

    async def on_send(event: object = None) -> None:
        sent.append("send")

    async def on_cancel(event: object = None) -> None:
        cancelled.append("cancel")

    composer.bind(on_send, on_cancel=on_cancel)
    composer.set_running(True)
    click = cast(Callable[[object], Awaitable[None]], composer.send_button.on_click)
    await click(None)

    assert cancelled == ["cancel"]
    assert sent == []


def test_set_workdir_updates_the_caption() -> None:
    composer = build_composer(workdir=".")
    path = "C:/Users/marie/Desktop/mono/python/Sdd-Orchestrator-Telegram-Bot"

    composer.set_workdir(path)

    assert composer.workdir_text.value == "…\\python\\Sdd-Orchestrator-Telegram-Bot"
    assert composer.workdir_caption.tooltip == path
