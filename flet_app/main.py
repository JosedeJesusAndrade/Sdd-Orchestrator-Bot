"""Flet GUI entry point — shell with a page-level drawer and the chat screen.

Run (desktop):  .venv\\Scripts\\flet.exe run flet_app/main.py
Android (QR):   .venv\\Scripts\\flet.exe run --android flet_app/main.py

Chat is the only main screen; a 100%-wide ``NavigationDrawer`` holds the
sessions. The prompt travels through the SAME ``build_prompt_stack()``
composition root and ``PromptService.execute`` the Telegram bot uses; only the
``ChatView`` implementation differs. Everything is async on Flet's single event
loop — never ``page.run_thread`` (the core is loop-bound and contextvar-aware).
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

# ``flet run`` may execute this file with flet_app/ as sys.path[0]; make the
# repository root importable so ``services.*`` resolves.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import flet as ft  # noqa: E402

from core_config import DEFAULT_MODEL, setup_logger  # noqa: E402

# Entry point: attach console + file handlers before anything logs. Importing
# config/library modules has no logging side effects by design.
setup_logger()

from flet_app.components.drawer import build_drawer  # noqa: E402
from flet_app.components.model_picker import model_picker  # noqa: E402
from flet_app.core.design_system import DS  # noqa: E402
from flet_app.core.navigation import ScreenHost  # noqa: E402
from flet_app.data.explorer import ExplorerController  # noqa: E402
from flet_app.data.model_selector import ModelSelectorController  # noqa: E402
from flet_app.data.sidebar import SidebarController  # noqa: E402
from flet_app.data.status_strip import StatusStripController  # noqa: E402
from flet_app.flet_chat_view import FletChatView, render_history  # noqa: E402
from flet_app.flet_permission_presenter import FletPermissionPresenter  # noqa: E402
from flet_app.keys import flet_key  # noqa: E402
from flet_app.screens.chat_screen import build_chat_screen  # noqa: E402
from flet_app.screens.explorer_screen import build_explorer_screen  # noqa: E402
from flet_app.ui_strings import TOAST_COMING_SOON  # noqa: E402
from services.composition import build_prompt_stack  # noqa: E402
from services.frontend_gate import (  # noqa: E402
    AllowAllGate,
    authorize_request,
    finish_request,
)
from services.prompt_service import PromptAlreadyRunningError  # noqa: E402
from services.server_lifecycle import (  # noqa: E402
    WarmUpLifecycle,
    log_server_left_running,
    warm_up_server,
)

CONVERSATION_ID = flet_key()

WARM_UP_FAILED_MESSAGE = (
    "⚠️ No se pudo preparar el servidor OpenCode. Revisa la configuración del "
    "servidor y el log; el próximo mensaje volverá a intentarlo."
)

logger = logging.getLogger(__name__)


async def warm_up_gui(
    send_notice: Callable[[str], Awaitable[bool]],
    manager: WarmUpLifecycle | None,
    *,
    directory: str | Path | None,
) -> bool:
    """Warm the shared server up and notify the user when it fails.

    Extracted from :func:`main` so the startup behavior is unit-testable
    without a live Flet session or a real server.
    """
    ready = await warm_up_server(manager, directory=directory)
    if not ready:
        await send_notice(WARM_UP_FAILED_MESSAGE)
    return ready


async def main(page: ft.Page) -> None:
    page.title = "SDD Orchestrator"
    page.padding = 0
    page.bgcolor = DS.colors.background
    page.theme_mode = ft.ThemeMode.DARK

    core = build_prompt_stack()
    gate = AllowAllGate()

    # FD-09/FD-R1: the app NEVER writes ``settings["workdir"]`` at startup;
    # ``WorkdirService.select`` stays the only writer. The caption and the
    # server warm-up share ONE resolved path instead.
    sidebar = SidebarController(
        core.conversations_service,
        core.session_service,
        core.effective_workdir,
        core.session_store,
        CONVERSATION_ID,
    )
    status_strip = StatusStripController(core.status_service, CONVERSATION_ID)
    model_selector = ModelSelectorController(
        core.model_catalog,
        core.session_store,
        CONVERSATION_ID,
    )
    explorer = ExplorerController(core.workdir_service, CONVERSATION_ID)
    data_controllers = (sidebar, status_strip, model_selector, explorer)
    logger.debug(
        "Flet data layer lista",
        extra={
            "event": "flet_data_layer_ready",
            "controllers": [type(controller).__name__ for controller in data_controllers],
        },
    )

    model = DEFAULT_MODEL
    try:
        settings = await core.settings_service.get(CONVERSATION_ID)
        model = settings.model
    except Exception:
        logger.exception("No se pudieron leer los settings; usando defaults")

    try:
        resolved = await core.effective_workdir.resolve(CONVERSATION_ID)
        workdir = str(resolved.path)
        logger.info(
            "Flet workdir resuelto",
            extra={
                "event": "flet_workdir_resolved",
                "workdir": workdir,
                "source": str(resolved.source),
            },
        )
    except Exception:
        logger.exception("No se pudo resolver el workdir; usando el fallback")
        workdir = str(core.effective_workdir.fallback)

    initial_snapshot = await sidebar.load()
    editing = False

    def show_snackbar(message: str) -> None:
        page.show_dialog(
            ft.SnackBar(
                content=ft.Text(value=message),
                duration=3000,
                behavior=ft.SnackBarBehavior.FLOATING,
                bgcolor=DS.colors.surface_raised,
            )
        )

    async def open_drawer() -> None:
        if page.drawer is None:
            return
        try:
            show = cast(Callable[[], Awaitable[None]], page.show_drawer)
            await show()
        except ValueError:
            logger.debug("Drawer no disponible", extra={"event": "flet_drawer_open_failed"})

    async def close_drawer() -> None:
        if page.drawer is None:
            return
        try:
            hide = cast(Callable[[], Awaitable[None]], page.close_drawer)
            await hide()
        except Exception:
            logger.debug("Drawer no disponible", extra={"event": "flet_drawer_close_failed"})

    def set_drawer_open(open_: bool) -> None:
        """Sync ``expanded`` then reveal/hide the drawer (one sync toggle)."""
        if sidebar.snapshot.expanded != open_:
            sidebar.toggle()
        page.run_task(open_drawer if open_ else close_drawer)

    def on_open() -> None:
        set_drawer_open(True)

    def on_dismiss() -> None:
        set_drawer_open(False)

    def on_search() -> None:
        show_snackbar(TOAST_COMING_SOON)

    def on_open_settings() -> None:
        show_snackbar(TOAST_COMING_SOON)

    def on_edit() -> None:
        nonlocal editing
        editing = not editing
        chat.set_edit_mode(editing)
        drawer.set_edit_mode(editing)
        page.update()

    async def refresh_status() -> None:
        snapshot = await status_strip.load()
        chat.set_status(snapshot)
        page.update()

    def on_status_refresh() -> None:
        page.run_task(refresh_status)

    async def refresh_all() -> None:
        snapshot = await sidebar.load()
        drawer.render(snapshot)
        if snapshot.model:
            chat.set_model(snapshot.model)
        if snapshot.workdir:
            chat.set_workdir(snapshot.workdir)
        status_snapshot = await status_strip.load()
        chat.set_status(status_snapshot)
        page.update()

    async def on_send(
        event: ft.Event[ft.TextField] | ft.Event[ft.Container] | None = None,
    ) -> None:
        text = (chat.composer.field.value or "").strip()
        if not text:
            return

        # Same shared helper as ``@authorized``: request id + entry log +
        # gate policy (the Flet gate is AllowAllGate: physical access).
        if not authorize_request(gate, CONVERSATION_ID, "flet_on_send"):
            await view.send("⛔ No autorizado.")
            return

        started = time.monotonic()
        try:
            chat.composer.field.value = ""
            chat.composer.set_busy(True)
            chat.composer.set_running(True)
            page.update()

            await view.send(text, role="user")

            try:
                await core.prompt_service.execute(
                    conversation_id=CONVERSATION_ID,
                    prompt_text=text,
                    view=view,
                )
            except PromptAlreadyRunningError:
                await view.send("⏳ Ya hay un prompt en ejecución.")
            except Exception as exc:
                await view.send(f"❌ Error inesperado: `{exc}`")
        finally:
            chat.composer.set_busy(False)
            chat.composer.set_running(False)
            page.update()
            finish_request("flet_on_send", CONVERSATION_ID, started)
        await refresh_all()

    async def on_cancel(
        event: ft.Event[ft.TextField] | ft.Event[ft.Container] | None = None,
    ) -> None:
        core.prompt_service.cancel(CONVERSATION_ID)

    async def select_session(real_id: str) -> None:
        await sidebar.select(real_id)
        try:
            messages = await core.conversations_service.messages(real_id, limit=50)
        except Exception:
            logger.exception(
                "No se pudo cargar el historial de la sesión",
                extra={"event": "flet_history_failed"},
            )
            messages = []
        render_history(chat.conversation, messages)
        chat.reveal_conversation()
        await refresh_all()
        on_dismiss()

    def on_select(real_id: str) -> None:
        page.run_task(select_session, real_id)

    async def new_chat() -> None:
        await sidebar.new_chat()
        view.reset()
        chat.reset()
        await refresh_all()
        on_dismiss()

    def on_new_chat() -> None:
        page.run_task(new_chat)

    async def select_model(model_id: str) -> None:
        await model_selector.select(model_id)
        page.pop_dialog()
        await refresh_all()

    def on_model_select(model_id: str) -> None:
        page.run_task(select_model, model_id)

    def on_model_retry() -> None:
        page.pop_dialog()
        page.run_task(open_model_picker)

    async def open_model_picker() -> None:
        snapshot = await model_selector.load()
        if snapshot is None:
            return
        page.show_dialog(model_picker(snapshot, on_select=on_model_select, on_retry=on_model_retry))

    def on_pick_model() -> None:
        page.run_task(open_model_picker)

    chat = build_chat_screen(
        model=model,
        workdir=workdir,
        profile_name=initial_snapshot.profile_name,
        on_toggle=on_open,
        on_open_model=on_pick_model,
        on_new_chat=on_new_chat,
        on_edit=on_edit,
        on_refresh_status=on_status_refresh,
    )
    view = FletChatView(
        page,
        chat.conversation,
        on_first_message=chat.reveal_conversation,
    )

    # Permission prompts share the router with Telegram. The Flet presenter
    # renders them as queued modal dialogs and shows the outcome as a floating
    # SnackBar. With ``OPENCODE_BACKEND=cli`` there is no router, so nothing is
    # registered and the UI is unchanged.
    if core.permission_router is not None:
        core.permission_router.add_presenter(
            FletPermissionPresenter(
                page,
                resolve=core.permission_router.resolve_from_ui,
            )
        )
        logger.info(
            "Flet permission presenter registered",
            extra={"event": "permission_presenter_registered", "frontend": "flet"},
        )
    else:
        logger.info(
            "Flet permission presenter skipped",
            extra={"event": "permission_presenter_skipped", "frontend": "flet"},
        )

    host = ScreenHost(page, chat.control)

    async def show_chat() -> None:
        await host.show(chat.control)

    def on_back() -> None:
        page.run_task(show_chat)

    def request_refresh() -> None:
        page.run_task(refresh_all)

    explorer_screen = build_explorer_screen(
        page,
        explorer,
        on_back=on_back,
        on_select=request_refresh,
    )

    async def open_explorer() -> None:
        on_dismiss()
        roots = explorer.roots()
        if roots:
            await explorer_screen.load(roots[0])
        await host.show(explorer_screen.control)

    def on_open_explorer() -> None:
        page.run_task(open_explorer)

    drawer = build_drawer(
        page,
        initial_snapshot,
        on_toggle=on_dismiss,
        on_select=on_select,
        on_new_chat=on_new_chat,
        on_open_explorer=on_open_explorer,
        on_open_settings=on_open_settings,
        on_edit=on_edit,
        on_search=on_search,
    )

    chat.composer.bind(on_send, on_cancel=on_cancel)
    page.add(host.control)
    # A4: ``page.drawer`` MUST be assigned before the first ``show_drawer``;
    # both ``show_drawer``/``close_drawer`` are async in flet 1.0.3.
    page.drawer = drawer.control
    page.update()

    await refresh_all()

    def on_page_close() -> None:
        # D5: the server lives always. The bot may be sharing this process, so
        # closing the GUI never calls ``manager.stop()``; it only logs.
        log_server_left_running(core.server_manager)

    page.on_disconnect = on_page_close
    page.on_close = on_page_close

    # Eager warm-up AFTER the UI is mounted: adopt/spawn the shared server now
    # instead of on the first prompt, and surface a clear notice on failure.
    await warm_up_gui(view.send, core.server_manager, directory=workdir)


if __name__ == "__main__":
    ft.run(main)
