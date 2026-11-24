"""Tests for the chat shell: top bar wiring, history rendering and reset.

The conversations screen was retired in ``flet-ui`` P4; the surviving helpers
moved to ``sidebar_tile`` (already tested there). This file keeps the render
history checks and the shell behavior (top bar callbacks, welcome gradient,
``reset``).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import cast

import flet as ft

from flet_app.components.top_bar import build_top_bar
from flet_app.core.design_system import DS
from flet_app.flet_chat_view import render_history
from flet_app.screens.chat_screen import build_chat_screen
from services.agent_sessions import Message


def _message(role: str, text: str) -> Message:
    return Message(
        id=f"msg_{role}_{abs(hash(text))}",
        role=role,
        text=text,
        timestamp=int(time.time() * 1000),
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


def _icon_button(root: object, icon: ft.IconData) -> ft.IconButton:
    return next(c for c in _walk(root) if isinstance(c, ft.IconButton) and c.icon == icon)


def test_render_history_clears_and_fills_the_chat_column() -> None:
    chat = build_chat_screen(model="deepseek/deepseek-flash", workdir=".")
    chat.conversation.controls.append(ft.Text("turno viejo"))

    rendered = render_history(
        chat.conversation,
        [_message("user", "hola"), _message("assistant", "**¿qué tal?**")],
    )

    assert rendered == 2
    assert len(chat.conversation.controls) == 2
    assert isinstance(chat.conversation.controls[0], ft.Row)
    assert isinstance(chat.conversation.controls[1], ft.Container)
    assert "turno viejo" not in _texts(chat.conversation)


def test_render_history_skips_blank_text_and_adds_a_placeholder() -> None:
    chat = build_chat_screen(model="deepseek/deepseek-flash", workdir=".")
    chat.conversation.controls.append(ft.Text("turno viejo"))

    rendered = render_history(
        chat.conversation,
        [_message("user", "   "), _message("assistant", "")],
    )

    assert rendered == 0
    assert len(chat.conversation.controls) == 1
    assert "Esta conversación no tiene mensajes de texto para mostrar." in _texts(chat.conversation)


def test_top_bar_toggle_invokes_the_callback() -> None:
    calls: list[str] = []
    top_bar = build_top_bar("deepseek/deepseek-flash", on_toggle=lambda: calls.append("toggle"))

    menu = _icon_button(top_bar.control, ft.Icons.MENU)
    assert menu.tooltip == "Sesiones"
    cast(Callable[[object], None], menu.on_click)(None)

    assert calls == ["toggle"]


def test_top_bar_wires_new_chat_and_edit_without_an_avatar() -> None:
    calls: list[str] = []
    top_bar = build_top_bar(
        "deepseek/deepseek-flash",
        on_new_chat=lambda: calls.append("new"),
        on_edit=lambda: calls.append("edit"),
    )

    new_chat = _icon_button(top_bar.control, ft.Icons.ADD_COMMENT)
    edit = _icon_button(top_bar.control, ft.Icons.EDIT)
    cast(Callable[[object], None], new_chat.on_click)(None)
    cast(Callable[[object], None], edit.on_click)(None)

    assert calls == ["new", "edit"]
    # BUG #3: the avatar lives ONLY in the drawer footer, never in the header.
    assert not any(isinstance(c, ft.CircleAvatar) for c in _walk(top_bar.control))


def test_top_bar_edit_mode_swaps_the_pencil_icon() -> None:
    top_bar = build_top_bar("deepseek/deepseek-flash", on_edit=lambda: None)

    top_bar.set_edit_mode(True)
    assert top_bar.edit_button.icon == ft.Icons.CHECK

    top_bar.set_edit_mode(False)
    assert top_bar.edit_button.icon == ft.Icons.EDIT


def test_chat_screen_reset_clears_turns_and_restores_the_welcome() -> None:
    chat = build_chat_screen(model="deepseek/deepseek-flash", workdir=".")
    chat.conversation.controls.append(ft.Text("turno viejo"))
    chat.reveal_conversation()

    assert chat.greeting.opacity == 0.0
    assert chat.background.gradient is None

    chat.reset()

    assert chat.conversation.controls == []
    assert chat.greeting.opacity == 1.0
    assert chat.background.gradient is not None
    assert all(glow.visible for glow in chat.glows)


def test_chat_screen_welcome_fade_uses_the_design_token() -> None:
    """CH-01/AC#8: the welcome fades over ``welcome_ms`` (not instantly)."""
    chat = build_chat_screen(model="deepseek/deepseek-flash", workdir=".")

    animation = cast(ft.Animation, chat.greeting.animate_opacity)
    assert animation is not None
    assert animation.duration == DS.motion.welcome_ms == 300
    assert animation.curve == DS.motion.curve

    chat.reveal_conversation()
    assert chat.greeting.opacity == 0.0
    chat.reset()
    assert chat.greeting.opacity == 1.0


def test_chat_screen_status_degrades_to_dashes_without_a_snapshot() -> None:
    chat = build_chat_screen(model="deepseek/deepseek-flash", workdir=".")

    chat.set_status(None)

    assert chat.status_holder.content is not None
    texts = _texts(chat.status_holder.content)
    assert any("\u2014" in text for text in texts)


def test_chat_screen_status_loading_is_distinct_from_degraded() -> None:
    chat = build_chat_screen(model="deepseek/deepseek-flash", workdir=".")

    chat.set_status(None, loading=True)

    assert chat.status_holder.content is not None
    line = next(text for text in _texts(chat.status_holder.content) if "·" in text)
    assert "\u2026" in line
    assert "\u2014" not in line


def test_chat_screen_set_workdir_updates_the_caption() -> None:
    chat = build_chat_screen(model="deepseek/deepseek-flash", workdir=".")
    chat.set_workdir("C:/Users/marie/Desktop/mono/python/Sdd-Orchestrator-Telegram-Bot")

    assert chat.composer.workdir_text.value == "…\\python\\Sdd-Orchestrator-Telegram-Bot"
    assert chat.composer.workdir_caption.tooltip is not None


def test_status_strip_uses_design_system_height() -> None:
    chat = build_chat_screen(model="deepseek/deepseek-flash", workdir=".")
    strip = cast(ft.Container, chat.status_holder.content)

    assert strip.height == DS.sizes.status_bar
