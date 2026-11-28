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
from typing import Protocol, cast

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
from flet_app.data.session_switch import SessionSwitcher  # noqa: E402
from flet_app.data.sidebar import SidebarController  # noqa: E402
from flet_app.data.status_strip import StatusStripController  # noqa: E402
from flet_app.edit_mode import EditMode, rename_failure_notice  # noqa: E402
from flet_app.flet_chat_view import FletChatView, render_history  # noqa: E402
from flet_app.flet_permission_presenter import FletPermissionPresenter  # noqa: E402
from flet_app.keys import flet_key  # noqa: E402
from flet_app.resilience import DisconnectGuard, busy_notice  # noqa: E402
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
from services.workdir_service import (  # noqa: E402
    DirectoryEntry,
    DirectoryListing,
    WorkdirFailure,
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


class ExplorerView(Protocol):
    """The pure explorer view the presenter feeds (data in, callbacks out)."""

    @property
    def path(self) -> Path | None: ...

    def render(self, listing: DirectoryListing, recent: tuple[Path, ...] = ()) -> None: ...

    def set_error(self, failure: WorkdirFailure) -> None: ...

    def set_loading(self, loading: bool = True) -> None: ...


class ExplorerPresenter:
    """Drive the workdir controller and feed the pure explorer view (P5).

    Extracted from :func:`main` so the async orchestration (list/select/CRUD plus
    the post-mutation refresh) is unit-testable without a live Flet session.
    """

    def __init__(
        self,
        controller: ExplorerController,
        view: ExplorerView,
        *,
        on_selected: Callable[[], Awaitable[None]],
    ) -> None:
        self._controller = controller
        self._view = view
        self._on_selected = on_selected
        self._last_attempt: Path | None = None

    async def open(self) -> None:
        """List the first root, if any (the initial browse)."""
        roots = self._controller.roots()
        if roots:
            await self.load(roots[0])

    async def load(self, path: Path | str) -> None:
        """Browse ``path`` and render the listing or its typed failure."""
        target = Path(path)
        self._last_attempt = target
        try:
            # Disable the actions *inside* the guard: if the initial flush
            # raises (Flet re-mount patch), the ``finally`` still re-enables.
            self._view.set_loading(True)
            result = await self._controller.list_dir(target)
            if isinstance(result, WorkdirFailure):
                self._view.set_error(result)
                return
            await self._show(result)
        finally:
            # Every terminal path (success, failure and exception) must clear the
            # in-flight state so the CTA/actions never stay stuck disabled.
            self._view.set_loading(False)

    async def select(self, path: Path | str) -> None:
        """Persist ``path`` as the workdir, refresh then return to the chat."""
        try:
            # See ``load``: the disable must not precede the guarded block.
            self._view.set_loading(True)
            result = await self._controller.select(path)
            if isinstance(result, WorkdirFailure):
                self._view.set_error(result)
                return
            await self._show_refreshed()
            await self._on_selected()
        finally:
            self._view.set_loading(False)

    async def create(self, name: str, init_git: bool) -> None:
        """Create a child under the directory the view is browsing."""
        parent = self._view.path
        if parent is None:
            return
        result = await self._controller.create_child(parent, name, init_git=init_git)
        await self._after_mutation(result)

    async def rename(self, path: Path, new_name: str) -> None:
        """Rename the directory at ``path`` then refresh its parent."""
        result = await self._controller.rename(path, new_name)
        await self._after_mutation(result)

    async def delete(self, path: Path) -> None:
        """Delete the directory at ``path`` then refresh its parent."""
        result = await self._controller.delete(path)
        await self._after_mutation(result)

    async def retry(self) -> None:
        """Re-attempt the last browse target (only after a failure)."""
        if self._last_attempt is not None:
            await self.load(self._last_attempt)

    async def _after_mutation(self, result: DirectoryEntry | WorkdirFailure) -> None:
        if isinstance(result, WorkdirFailure):
            self._view.set_error(result)
            return
        await self._show_refreshed()

    async def _show_refreshed(self) -> None:
        listing = self._controller.listing
        if isinstance(listing, DirectoryListing):
            await self._show(listing)

    async def _show(self, listing: DirectoryListing) -> None:
        self._last_attempt = listing.path
        recent = await self._recent_for(listing)
        self._view.render(listing, recent)

    async def _recent_for(self, listing: DirectoryListing) -> tuple[Path, ...]:
        """Recent shortcuts only at the explorer root (no clutter deeper)."""
        roots = self._controller.roots()
        if not roots or listing.path.resolve() != roots[0].resolve():
            return ()
        return await self._controller.recent()


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
    # Switching skips the active session and caches each session's history, so a
    # tap costs zero history reads after the first visit (BUG #2).
    switcher = SessionSwitcher(
        sidebar,
        lambda real_id: core.conversations_service.messages(real_id, limit=50),
    )
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
    edit_mode: EditMode | None = None

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
        """Sync ``expanded``, play/re-arm the shimmer, then reveal/hide."""
        if sidebar.snapshot.expanded != open_:
            sidebar.toggle()
        # D4: exactly one shimmer sweep per open; re-arm on dismiss so the next
        # open sweeps once more. No timer, no background task.
        if open_:
            drawer.begin_shimmer()
        else:
            drawer.end_shimmer()
        page.update()
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
        if edit_mode is None:
            return
        if edit_mode.active:
            page.run_task(edit_mode.save)
        else:
            edit_mode.activate()
            page.update()

    def on_status_refresh() -> None:
        # The strip's refresh icon reloads the sessions AND the status in one
        # batched pass: one tap => one ``refresh_all`` (no polling, no echo).
        page.run_task(refresh_all)

    async def refresh_all() -> None:
        # Show the distinct "loading" strip while the loads are in flight.
        chat.set_status(None, loading=True)
        page.update()
        snapshot = await sidebar.load()
        drawer.render(snapshot)
        if snapshot.model:
            chat.set_model(snapshot.model)
        if snapshot.workdir:
            chat.set_workdir(snapshot.workdir)
        status_snapshot = await status_strip.load()
        chat.set_status(status_snapshot)
        page.update()

    async def refresh_after_select() -> None:
        """Refresh only the strip + captions after a switch (no re-list).

        ``sidebar.select`` already updated the cached snapshot's active row, so a
        full :func:`refresh_all` (which re-queries the agent history) would be
        wasteful; render the cached snapshot and reload the status strip only.
        """
        chat.set_status(None, loading=True)
        page.update()
        snapshot = sidebar.snapshot
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

        # Resilience: derive the busy state from the SERVER before sending. A
        # stale busy turn (no local task) is aborted here, so the new prompt is
        # never silently queued behind an orphan. If the server is genuinely
        # busy, tell the user instead of freezing.
        reconciled = await core.prompt_service.reconcile(CONVERSATION_ID)
        notice = busy_notice(reconciled)
        if notice is not None:
            chat.composer.field.value = text
            chat.composer.set_running(True)
            page.update()
            await view.send(notice)
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
        # The active session just gained turns (and may have a new real id):
        # drop the cached history so a later revisit reloads the fresh state.
        switcher.invalidate()
        await refresh_all()

    async def on_cancel(
        event: ft.Event[ft.TextField] | ft.Event[ft.Container] | None = None,
    ) -> None:
        core.prompt_service.cancel(CONVERSATION_ID)

    async def select_session(real_id: str) -> None:
        # The switcher returns ``None`` only for the session the chat is ALREADY
        # showing: just close the drawer, no reload (the user's "way back"). At
        # startup the newest is active but not loaded, so its tap loads it.
        try:
            result = await switcher.switch(real_id)
        except Exception:
            logger.exception(
                "No se pudo cargar el historial de la sesión",
                extra={"event": "flet_history_failed"},
            )
            render_history(chat.conversation, [])
            chat.reveal_conversation()
            await refresh_after_select()
            on_dismiss()
            return
        if result is not None:
            render_history(chat.conversation, result.messages)
            chat.reveal_conversation()
            await refresh_after_select()
        on_dismiss()

    def on_select(real_id: str) -> None:
        page.run_task(select_session, real_id)

    async def new_chat() -> None:
        await sidebar.new_chat()
        view.reset()
        chat.reset()
        switcher.invalidate()
        switcher.reset_current()
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

    async def resync_history(*, force: bool) -> bool:
        """Replace any stale partial with the REAL conversation history.

        The server is the source of truth (``GET /session/{id}/message``); the
        DB-backed seam is the fallback when the server is unreachable. ``force``
        also wipes a leftover partial when the history turned out to be empty
        (reconnect/restart), instead of leaving a frozen snapshot on screen.
        """
        messages = await core.prompt_service.load_server_history(CONVERSATION_ID)
        if not messages:
            active = await core.session_store.get_active_session(CONVERSATION_ID)
            if active is not None and active.real_id:
                try:
                    messages = await core.conversations_service.messages(active.real_id, limit=50)
                except Exception:
                    logger.exception(
                        "No se pudo cargar el historial de respaldo",
                        extra={"event": "flet_history_fallback_failed"},
                    )
                    messages = None
        if messages:
            render_history(chat.conversation, messages)
            chat.reveal_conversation()
            switcher.invalidate()
        elif force:
            chat.reset()
        page.update()
        return bool(messages)

    async def reconcile_and_resync(*, force: bool, reset_composer: bool = False) -> None:
        """Abort a stale busy turn, then render the server's real history.

        Called on startup and on (re)connect. If THIS process still owns a live
        turn, it is left untouched (nothing to reconcile, no history to reload).
        """
        if core.prompt_service.is_local_running(CONVERSATION_ID):
            return
        result = await core.prompt_service.reconcile(CONVERSATION_ID)
        await resync_history(force=force)
        if reset_composer:
            chat.composer.set_busy(result.busy)
            chat.composer.set_running(result.busy)
            page.update()

    async def reconnect() -> None:
        """A page refresh/reconnect: reconcile with the server and reload."""
        await reconcile_and_resync(force=True, reset_composer=True)
        await refresh_all()

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

    async def after_explorer_select() -> None:
        """Refresh the chrome for the new workdir, then reveal the chat.

        The topbar/composer caption only reads the freshly persisted workdir
        after :func:`refresh_all`, so navigating back is the visual confirmation
        of the change (no manual back tap needed).
        """
        await refresh_all()
        await host.show(chat.control)

    presenter: ExplorerPresenter

    def on_enter(path: Path) -> None:
        page.run_task(presenter.load, path)

    def on_select_path(path: Path) -> None:
        page.run_task(presenter.select, path)

    def on_create(name: str, init_git: bool) -> None:
        page.run_task(presenter.create, name, init_git)

    def on_rename(path: Path, new_name: str) -> None:
        page.run_task(presenter.rename, path, new_name)

    def on_delete(path: Path) -> None:
        page.run_task(presenter.delete, path)

    def on_retry() -> None:
        page.run_task(presenter.retry)

    explorer_screen = build_explorer_screen(
        page,
        on_back=on_back,
        on_select=on_select_path,
        on_enter=on_enter,
        on_create=on_create,
        on_rename=on_rename,
        on_delete=on_delete,
        on_retry=on_retry,
    )
    presenter = ExplorerPresenter(
        explorer,
        explorer_screen,
        on_selected=after_explorer_select,
    )

    async def open_explorer() -> None:
        on_dismiss()
        await presenter.open()
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
        on_search=on_search,
    )

    # P6: the pencil drives this state machine; the save path goes through
    # ``sidebar.rename`` (the P2 data path) and a failure keeps edit mode on.
    edit_mode = EditMode(
        drawer=drawer,
        rename=sidebar.rename,
        set_pencil=chat.set_edit_mode,
        refresh=refresh_all,
        show_failure=lambda: rename_failure_notice(page),
    )

    chat.composer.bind(on_send, on_cancel=on_cancel)
    page.add(host.control)
    # A4: ``page.drawer`` MUST be assigned before the first ``show_drawer``;
    # both ``show_drawer``/``close_drawer`` are async in flet 1.0.3.
    page.drawer = drawer.control

    def on_resize(event: ft.PageResizeEvent | None = None) -> None:
        """Keep the 100%-wide drawer and its pinned footer in step (SH-01/A3).

        One resize => set the width + height and a single ``page.update()``.
        """
        width = event.width if event is not None else page.width
        height = event.height if event is not None else page.height
        if width is not None:
            drawer.set_width(float(width))
        if height is not None:
            drawer.set_height(float(height))
        page.update()

    page.on_resize = on_resize
    page.update()

    await refresh_all()

    # F5: a dropped connection is often only a transient network blip, so it
    # must NOT abort a legitimately running turn. ``DisconnectGuard`` arms a
    # grace timer on disconnect (cancelled by a quick reconnect) and aborts only
    # if the grace elapses or the app is actually closed.
    disconnect_guard = DisconnectGuard(
        lambda: core.prompt_service.abort_on_disconnect(CONVERSATION_ID)
    )

    def on_page_disconnect(event: object = None) -> None:
        # Transient blip (e.g. the phone loses Wi-Fi for a couple seconds):
        # arm the grace timer instead of aborting immediately. A fast reconnect
        # disarms it; only a real disconnect falls through to the abort.
        page.run_task(disconnect_guard.on_disconnect)

    def on_page_close(event: object = None) -> None:
        # D5: the server lives always. The bot may be sharing this process, so
        # closing the GUI never calls ``manager.stop()``; it only logs.
        log_server_left_running(core.server_manager)
        # Resilience: never leave the abandoned turn busy on the server. Closing
        # the app is a real end, so abort now (the local cancel stops the stream;
        # the direct abort is the guarantee even if the event loop ends before
        # the backend's scheduled abort runs).
        page.run_task(disconnect_guard.on_close)

    def on_page_connect(event: object = None) -> None:
        # Fires on refresh / reconnect (NOT on first open). Cancel any pending
        # disconnect abort (the blip recovered), then re-derive the state from
        # the server and replace any stale partial with the real history. If the
        # local turn survived, ``reconcile_and_resync`` leaves it running.
        disconnect_guard.disarm()
        page.run_task(reconnect)

    page.on_disconnect = on_page_disconnect
    page.on_close = on_page_close
    page.on_connect = on_page_connect

    # Eager warm-up AFTER the UI is mounted: adopt/spawn the shared server now
    # instead of on the first prompt, and surface a clear notice on failure.
    await warm_up_gui(view.send, core.server_manager, directory=workdir)

    # Startup reconciliation: abort any turn orphaned by a previous crash and
    # render the real history instead of an empty/partial screen.
    await reconcile_and_resync(force=False, reset_composer=True)


if __name__ == "__main__":
    ft.run(main)
