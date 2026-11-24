"""Chat screen: assembles the header, status strip, conversation and composer.

Composition only: the header, the thin status row, the welcome overlay and the
floating composer are dropped into the shared ``app_background`` shell. The data
comes from ``main.py`` through ``set_model``/``set_workdir``/``set_status``; the
gradient lives ONLY on the welcome state and disappears after the first message.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import flet as ft

from flet_app.components.app_background import app_background
from flet_app.components.composer import Composer, build_composer
from flet_app.components.greeting import build_greeting
from flet_app.components.status_strip import status_strip
from flet_app.components.top_bar import TopBar, build_top_bar
from flet_app.core.design_system import DS, REDUCED_MOTION

if TYPE_CHECKING:
    from services.status_service import StatusSnapshot


def _noop() -> None:
    """Inert refresh callback until ``main`` wires the status controller."""


@dataclass
class ChatScreen:
    """The assembled screen plus handles the entry point needs to drive it."""

    control: ft.Container
    top_bar: TopBar
    composer: Composer
    conversation: ft.Column
    greeting: ft.Container
    conversation_holder: ft.Container
    status_holder: ft.Container
    background: ft.Container
    glows: list[ft.Control]
    status_refresh: Callable[[], None]

    def set_model(self, model: str) -> None:
        self.top_bar.set_model(model)

    def set_workdir(self, workdir: str) -> None:
        self.composer.set_workdir(workdir)

    def set_status(self, snapshot: StatusSnapshot | None, *, loading: bool = False) -> None:
        """Re-render the thin status row for ``snapshot``.

        ``loading`` shows the ``…`` skeleton; without it a ``None`` snapshot
        degrades to the all-dash row (SS-02).
        """
        self.status_holder.content = status_strip(
            snapshot,
            on_refresh=self.status_refresh,
            loading=loading,
        )

    def set_edit_mode(self, editing: bool) -> None:
        self.top_bar.set_edit_mode(editing)

    def reveal_conversation(self) -> None:
        """Fade the greeting out and drop the gradient on the first message.

        The conversation column is mounted VISIBLE from the very first frame
        (see ``build_chat_screen``); only the greeting overlay's opacity and the
        welcome gradient are toggled here. The greeting stays mounted at
        ``opacity=0`` so the 300ms fade can actually play (CH-01/AC#8). Because
        Flutter's ``Opacity`` keeps hit-testing at 0, the greeting also sets
        ``ignore_interactions`` so its invisible text box stops swallowing the
        conversation's scroll/tap gestures (W2).
        """
        self._apply_welcome(False)

    def reset(self) -> None:
        """Clear the view and fade the welcome back in (CH-03)."""
        self.conversation.controls.clear()
        self._apply_welcome(True)

    def _apply_welcome(self, welcome: bool) -> None:
        """Toggle the welcome overlay; opacity carries the 300ms fade.

        ``ignore_interactions`` is flipped in lock-step with the opacity: the
        greeting is the top child of the conversation ``Stack``, so while it is
        hidden it must stop hit-testing or it swallows the chat's scroll/tap
        gestures. It only affects pointer events, never the fade animation.
        """
        self.greeting.opacity = 1.0 if welcome else 0.0
        self.greeting.ignore_interactions = not welcome
        self.background.gradient = DS.gradients.background() if welcome else None
        for glow in self.glows:
            glow.visible = welcome


def build_chat_screen(
    *,
    model: str,
    workdir: str,
    on_toggle: Callable[[], None] | None = None,
    on_open_model: Callable[[], None] | None = None,
    on_new_chat: Callable[[], None] | None = None,
    on_edit: Callable[[], None] | None = None,
    on_refresh_status: Callable[[], None] | None = None,
) -> ChatScreen:
    """Build the chat screen (A8: optional callbacks, keyword-only)."""
    top_bar = build_top_bar(
        model,
        on_toggle=on_toggle,
        on_open_model=on_open_model,
        on_new_chat=on_new_chat,
        on_edit=on_edit,
    )
    greeting = build_greeting()
    greeting.animate_opacity = (
        None if REDUCED_MOTION else ft.Animation(DS.motion.welcome_ms, DS.motion.curve)
    )
    composer = build_composer(workdir=workdir)
    status_refresh = on_refresh_status if on_refresh_status is not None else _noop
    status_holder = ft.Container(
        content=status_strip(None, on_refresh=status_refresh),
    )

    conversation = ft.Column(
        controls=[],
        spacing=DS.spacing.md,
        scroll=ft.ScrollMode.AUTO,
        auto_scroll=True,
        expand=True,
    )
    conversation_holder = ft.Container(
        content=conversation,
        expand=True,
        padding=ft.Padding.only(
            left=DS.spacing.xl,
            right=DS.spacing.xl,
            top=DS.spacing.sm,
        ),
    )

    # The conversation is NOT hidden at mount: a control tree that is created
    # with ``visible=False`` and revealed later (in the same patch that adds
    # its first child) can fail to build that child on the client. Mount the
    # scrollable column visible from frame one and let the greeting sit on top
    # of it; the first message only hides the greeting.
    conversation_area = ft.Stack(
        expand=True,
        fit=ft.StackFit.EXPAND,
        controls=[conversation_holder, greeting],
    )

    composer_holder = ft.Container(
        content=ft.ResponsiveRow(
            controls=[
                ft.Container(
                    content=composer.control,
                    col={"sm": 12, "md": 10, "lg": 8},
                )
            ],
            alignment=ft.MainAxisAlignment.CENTER,
        ),
        padding=ft.Padding.only(
            left=DS.spacing.xl,
            right=DS.spacing.xl,
            top=DS.spacing.sm,
            bottom=DS.spacing.md,
        ),
    )

    body = ft.Column(
        controls=[top_bar.control, status_holder, conversation_area, composer_holder],
        expand=True,
        spacing=0,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )

    # Welcome-only gradient: ``app_background`` builds the shell once; the
    # gradient and its two glows are toggled off when the first turn arrives.
    shell = app_background(body, gradient=True)
    stack = shell.content
    glows = list(stack.controls[:-1]) if isinstance(stack, ft.Stack) else []

    return ChatScreen(
        control=shell,
        top_bar=top_bar,
        composer=composer,
        conversation=conversation,
        greeting=greeting,
        conversation_holder=conversation_holder,
        status_holder=status_holder,
        background=shell,
        glows=glows,
        status_refresh=status_refresh,
    )
