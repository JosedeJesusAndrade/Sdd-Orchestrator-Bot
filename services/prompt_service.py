"""PromptService: execute OpenCode prompts, manage output, track state.

Architecture rationale:
  The old _process_prompt() was a 273-line monolithic function inside
  handlers/messages.py. It handled:
    - Session sync (JSON load/save, in-memory dicts)
    - Model resolution (3 fallback sources)
    - Subprocess execution (Popen, timeout, cancel)
    - Response formatting (filter, clean, escape, split)
    - Message delivery (send, edit, fallback)
    - Session ID capture

  This violated SRP (Single Responsibility Principle) and made the code
  untestable — you couldn't test the execution logic without a Telegram bot.

  PromptService solves this by:
    - Delegating session I/O to SessionStore
    - Delegating message delivery to a ChatView (frontend-agnostic)
    - Focusing ONLY on orchestration: build the command, run it,
      route the output to the right delivery method
    - Encapsulating process state (running tasks, cancel flags)

  Why a class?
    - Stateful: tracks running tasks per conversation_id
    - Injectable: SessionStore + AIProviderFactory in constructor;
      a per-conversation ChatView is passed to execute()
    - Testable: mock the backend and verify behavior

  Week 4: AIProviderFactory replaces direct AIBackend. Per-chat settings
  (provider, timeout, workdir) read from SessionStore. Factory resolves
  the correct backend per provider.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from core_config import OPENCODE_TIMEOUT, PROGRESS_UPDATE_INTERVAL
from services.agent_sessions import Message, MessagePart
from services.effective_workdir import env_fallback_workdir
from utils.logging import get_module_logger, log_exception

if TYPE_CHECKING:
    from services.ai_backend import AIBackend
    from services.ai_provider_factory import ProviderFactoryPort
    from services.chat_view import ChatView, ConversationKey, CoreStrings, ProgressHandle
    from services.effective_workdir import EffectiveWorkdir
    from services.opencode_server.client import OpenCodeServerClient
    from services.session_service import SessionService
    from services.session_store import SessionInfo, SessionStorePort

logger = get_module_logger(__name__)

#: Server status types that mean a turn is still in flight.
_BUSY_STATUSES = frozenset({"busy", "retry"})


class PromptAlreadyRunningError(Exception):
    """Raised when a prompt is already running for a chat."""


@dataclass(frozen=True)
class ReconcileResult:
    """Outcome of a server-side state reconciliation.

    ``busy`` is TRUE only when the SERVER reports an active turn. A stale busy
    turn (server busy, no local task) is aborted and reported as not busy, so the
    next prompt is never queued behind an orphan. If that abort FAILS the server
    is still busy, so ``busy`` stays TRUE and the caller must surface the notice
    instead of queueing a send behind the orphan.
    """

    session_id: str | None = None
    server_status: str | None = None
    aborted_stale: bool = False
    busy: bool = False


class PromptService:
    """Orchestrate the lifecycle of an OpenCode prompt execution.

    Responsibilities:
      1. Check if a prompt is already running (no double-execution)
      2. Resolve model + session from SessionStore
      3. Build and execute the opencode CLI command
      4. Capture the session ID on first run
      5. Format and deliver the response via a ChatView
      6. Track prompt counts
      7. Support cancellation

    The frontend is abstracted behind ``ChatView``: this service never
    imports PTB and never touches a ``message_id``.
    """

    def __init__(
        self,
        session_store: SessionStorePort,
        provider_factory: ProviderFactoryPort,
        *,
        effective_workdir: EffectiveWorkdir | None = None,
        session_service: SessionService | None = None,
        server_client: OpenCodeServerClient | None = None,
    ) -> None:
        self._store = session_store
        self._factory = provider_factory
        # Injected by ``build_prompt_stack``. Optional only so unit tests can
        # build a PromptService in isolation; production always passes it.
        self._effective_workdir = effective_workdir
        # SS-01: ensures a bot session exists BEFORE the first prompt so the
        # OpenCode ``real_id`` it returns has somewhere to be persisted.
        self._session_service = session_service
        # Resilience: the SERVER is the source of truth for the busy state. The
        # client is ``None`` with the CLI backend (or when reconciling is not
        # supported), in which case in-memory state is all we have.
        self._server_client = server_client

        # Per-conversation running tasks
        self._running: dict[ConversationKey, asyncio.Task[Any]] = {}
        # Per-conversation cancel flags (set by cancel() before process terminates)
        self._cancel: set[ConversationKey] = set()
        # Last server-reported busy state per conversation (resilience). Kept
        # in sync by ``reconcile`` so ``is_running`` reflects the server even
        # after the local task (and its memory) is gone.
        self._server_busy: dict[ConversationKey, bool] = {}
        # Per-conversation backend instances (F3). One conversation = one
        # backend = one process, so cancel(cid) can only ever touch that
        # conversation's process. Lifetime == the running prompt: created in
        # _execute_prompt, dropped in execute()'s finally. Bounded by the
        # number of *concurrently running* prompts (not by total chats).
        self._backends: dict[ConversationKey, AIBackend] = {}

    # ── Public API ──────────────────────────────────────────────────

    def is_running(self, conversation_id: ConversationKey) -> bool:
        """Check if a prompt is executing for this chat.

        Derived from the SERVER's last known state as well as the local task:
        after a crash/restart the local task is gone, but a stale busy turn on
        the server must still be treated as running until it is aborted.
        """
        return self.is_local_running(conversation_id) or self._server_busy.get(
            conversation_id, False
        )

    def is_local_running(self, conversation_id: ConversationKey) -> bool:
        """True only when THIS process owns a live task for the conversation."""
        return conversation_id in self._running

    async def reconcile(self, conversation_id: ConversationKey) -> ReconcileResult:
        """Reconcile the local state with the SERVER's session status.

        The memory (``_running``) is lost on a crash/restart, so the server is
        asked whether the session is still busy. When it is busy but no local
        task owns it, the turn is an orphan: it is aborted so the next prompt is
        not queued/frozen behind it.
        """
        session = await self._store.get_active_session(conversation_id)
        if session is None or not session.real_id:
            self._server_busy.pop(conversation_id, None)
            return ReconcileResult()

        session_id = session.real_id
        local_running = self.is_local_running(conversation_id)
        if self._server_client is None:
            self._server_busy[conversation_id] = local_running
            return ReconcileResult(session_id=session_id, busy=local_running)

        workdir = await self._resolve_workdir(conversation_id)
        try:
            status = await self._server_client.get_session_status(session_id, directory=workdir)
        except Exception as exc:
            # Never let a status probe break the UI; keep the local truth.
            logger.warning(
                "No se pudo consultar el estado de la sesión; se usa el estado local",
                extra={
                    "event": "opencode_reconcile_status_failed",
                    "session_id": session_id,
                    "error_type": type(exc).__name__,
                },
            )
            self._server_busy[conversation_id] = local_running
            return ReconcileResult(session_id=session_id, busy=local_running)

        busy = status in _BUSY_STATUSES
        aborted = False
        if busy and not local_running:
            # Orphaned turn: the UI is gone (or restarted) but the server keeps
            # the session busy forever. Abort it so it cannot block a new send.
            aborted = await self._abort_session(session_id, workdir)
            # Only trust the abort when it SUCCEEDED. If it failed (HTTP error /
            # 404 unsupported) the server is STILL busy: clearing the flag here
            # would let the next send queue silently behind the orphan — the
            # exact broken state this resilience layer exists to prevent. Keep
            # ``busy`` TRUE so the caller shows the busy notice instead.
            if aborted:
                busy = False

        self._server_busy[conversation_id] = busy
        logger.info(
            "Estado de sesión reconciliado con el servidor",
            extra={
                "event": "opencode_session_reconciled",
                "session_id": session_id,
                "server_status": status,
                "local_running": local_running,
                "aborted_stale": aborted,
                "busy": busy,
            },
        )
        return ReconcileResult(
            session_id=session_id,
            server_status=status,
            aborted_stale=aborted,
            busy=busy,
        )

    async def abort_active(self, conversation_id: ConversationKey) -> bool:
        """Best-effort abort of the conversation's active session on the server.

        Returns True when the abort was requested. Used on disconnect so a
        closing UI does not leave a busy turn behind.
        """
        if self._server_client is None:
            return False
        session = await self._store.get_active_session(conversation_id)
        if session is None or not session.real_id:
            return False
        workdir = await self._resolve_workdir(conversation_id)
        aborted = await self._abort_session(session.real_id, workdir)
        if aborted:
            self._server_busy[conversation_id] = False
        return aborted

    async def abort_on_disconnect(self, conversation_id: ConversationKey) -> bool:
        """Handle the UI going away: stop the local turn AND clear the server.

        A hard disconnect may end the event loop before the backend's scheduled
        abort runs, so the session is aborted directly here as well. Idempotent:
        aborting an already idle session is a harmless no-op.
        """
        if self.is_local_running(conversation_id):
            self.cancel(conversation_id)
        return await self.abort_active(conversation_id)

    async def load_server_history(
        self,
        conversation_id: ConversationKey,
        *,
        limit: int = 50,
    ) -> list[Message] | None:
        """Fetch the REAL conversation history from the server.

        ``None`` means "not available" (no server client, no active session or
        transport failure); the caller then falls back to the DB-backed seam.
        This is what replaces a stale partial snapshot on reconnect.
        """
        if self._server_client is None:
            return None
        session = await self._store.get_active_session(conversation_id)
        if session is None or not session.real_id:
            return None
        workdir = await self._resolve_workdir(conversation_id)
        try:
            raw = await self._server_client.get_messages(
                session.real_id, limit=limit, directory=workdir
            )
        except Exception as exc:
            logger.warning(
                "No se pudo cargar el historial desde el servidor",
                extra={
                    "event": "opencode_history_sync_failed",
                    "session_id": session.real_id,
                    "error_type": type(exc).__name__,
                },
            )
            return None
        return _messages_from_server(raw)

    async def _resolve_workdir(self, conversation_id: ConversationKey) -> str | None:
        if self._effective_workdir is None:
            return None
        try:
            resolved = await self._effective_workdir.resolve(conversation_id)
        except Exception:
            log_exception(
                "resilience_workdir_resolve_failed",
                module=__name__,
                level=logging.DEBUG,
            )
            return None
        return str(resolved.path)

    async def _abort_session(self, session_id: str, workdir: str | None) -> bool:
        if self._server_client is None:
            return False
        try:
            return await self._server_client.abort(session_id, directory=workdir)
        except Exception:
            log_exception(
                "opencode_resilience_abort_failed",
                module=__name__,
                level=logging.WARNING,
                session_id=session_id,
            )
            return False

    def cancel(self, conversation_id: ConversationKey) -> bool:
        """Request cancellation of a running prompt.

        Returns True if there was a prompt to cancel, False otherwise.
        Cancellation is asynchronous — the prompt may take a moment
        to actually stop after this returns.
        """
        if conversation_id not in self._running:
            return False
        self._cancel.add(conversation_id)
        # F3: cancel ONLY this conversation's backend. The old code looped
        # over every cached provider instance and killed whatever shared
        # process the field pointed at — cross-killing other conversations.
        backend = self._backends.get(conversation_id)
        if backend is not None:
            try:
                backend.cancel()
            except Exception as e:
                log_exception(
                    "backend_cancel_failed",
                    module=__name__,
                    level=logging.WARNING,
                    error_type=type(e).__name__,
                )
        return True

    async def execute(
        self,
        conversation_id: ConversationKey,
        prompt_text: str,
        view: ChatView,
    ) -> str:
        """Execute a prompt for the given conversation.

        This is the main entry point. It handles the full lifecycle:
        session sync → execution → delivery → cleanup.

        Phase 2 logging: emits prompt_start, prompt_complete, prompt_error
        events with duration tracking and chat_id_masked.

        Args:
            conversation_id: Conversation key used for session state.
            prompt_text: The user's prompt text.
            view: Frontend surface for progress + delivery (per-conversation).

        Returns:
            The raw stdout output from OpenCode.

        Raises:
            PromptAlreadyRunningError: if a prompt is already running.
        """
        from utils.logging import mask_conversation_key

        if conversation_id in self._running:
            raise PromptAlreadyRunningError(f"Chat {conversation_id} already has a running prompt")

        masked_cid = mask_conversation_key(conversation_id)
        start_time = time.monotonic()
        model = await self._store.get_model(conversation_id)

        logger.info(
            "Prompt started",
            extra={
                "event": "prompt_start",
                "chat_id_masked": masked_cid,
                "model": model,
            },
        )

        try:
            task = asyncio.current_task()
            if task is not None:
                self._running[conversation_id] = task

            # 1. Resolve session (model already resolved above for logging).
            # SS-01: bootstrap the active bot session first so the id returned
            # by the very first prompt can be persisted (``_persist_session_id``).
            session: SessionInfo | None
            if self._session_service is not None:
                session = await self._session_service.ensure_active(conversation_id)
            else:
                session = await self._store.get_active_session(conversation_id)

            self._log_prompt(conversation_id, prompt_text, model)

            # 2. Open the "Processing..." placeholder. The VIEW owns the text
            # (strings.processing); the core passes no copy (item 4).
            handle = await view.start_progress()

            # F3: one backend instance per conversation, created before the
            # ticker decision so a live-progress backend can replace the
            # elapsed-time ticker with real streamed text.
            backend = await self._create_backend(conversation_id)
            live_progress = getattr(backend, "supports_live_progress", False)

            progress_task: asyncio.Task[Any] | None = None
            if not live_progress:
                progress_task = asyncio.create_task(self._update_progress(handle, view.strings))

            # Check if cancelled during session sync (before backend execution)
            if conversation_id in self._cancel:
                # Stop the periodic updater BEFORE painting the terminal state.
                # Otherwise it would keep ticking and overwrite the cancelled
                # text with the elapsed-time progress text.
                if progress_task is not None:
                    await self._stop_progress(progress_task)
                await handle.error(view.strings.cancelled)
                return ""

            # 3. Execute OpenCode via AI backend
            try:
                result = await self._execute_prompt(
                    conversation_id,
                    prompt_text,
                    session,
                    model,
                    view.source_label,
                    backend=backend,
                    on_progress=handle.update if live_progress else None,
                )
            except Exception as exc:
                # A typed backend failure (stream stalled, session.error, ...)
                # must replace the live placeholder instead of leaving it on
                # the processing text forever. The error is re-raised so the
                # caller logs it.
                with contextlib.suppress(Exception):
                    await handle.error(view.strings.error.format(error=exc))
                raise
            finally:
                if progress_task is not None:
                    await self._stop_progress(progress_task)

            # Persist the OpenCode session id BEFORE delivery: a cancelled or
            # failed delivery must never drop it, or the next prompt would start
            # a brand-new OpenCode session. Idempotent with the backend sink.
            await self._persist_session_id(conversation_id, result.get("session_id"))

            # 4. Deliver response
            await self._deliver_response(conversation_id, result, handle, view, session)

            stdout: str = result["stdout"]
            return stdout

        except Exception as exc:
            elapsed = time.monotonic() - start_time
            logger.error(
                "Prompt failed",
                extra={
                    "event": "prompt_error",
                    "chat_id_masked": masked_cid,
                    "model": model,
                    "duration_s": round(elapsed, 3),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            raise
        else:
            elapsed = time.monotonic() - start_time
            logger.info(
                "Prompt complete",
                extra={
                    "event": "prompt_complete",
                    "chat_id_masked": masked_cid,
                    "model": model,
                    "duration_s": round(elapsed, 3),
                },
            )
        finally:
            self._running.pop(conversation_id, None)
            self._cancel.discard(conversation_id)
            # The server reports idle (or will after the backend's abort); drop
            # the cached busy flag so the UI does not stay on the stop button.
            self._server_busy.pop(conversation_id, None)
            # Release this conversation's backend so the instance count stays
            # bounded by live executions (F3). The process is already gone by
            # now; the object holds no state worth keeping.
            self._backends.pop(conversation_id, None)

    # ── Internal: AI backend execution ──────────────────────────────

    async def _create_backend(self, conversation_id: ConversationKey) -> AIBackend:
        """Resolve the per-chat provider and build a fresh backend instance.

        F3: one backend instance per conversation. Stored BEFORE the cancel
        check so a cancel arriving during settings resolution targets *this*
        conversation's backend (and, if the process has not started yet, at
        worst cancels a process-less instance — a harmless no-op).
        """
        provider = await self._store.get_chat_setting(conversation_id, "provider", "opencode")
        backend = self._factory.create(provider)
        self._backends[conversation_id] = backend
        return backend

    async def _execute_prompt(
        self,
        conversation_id: ConversationKey,
        prompt: str,
        session: SessionInfo | None,
        model: str,
        source_label: str = "",
        *,
        backend: AIBackend,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Execute prompt via the resolved AI backend.

        Reads ``timeout``/``agent`` from SessionStore; the workdir comes from
        the single :class:`EffectiveWorkdir` resolver (WD-02).

        Returns a dict with keys: stdout, stderr, returncode, cancelled.
        """
        timeout_val = await self._store.get_chat_setting(
            conversation_id, "timeout", OPENCODE_TIMEOUT
        )
        if self._effective_workdir is not None:
            workdir = str((await self._effective_workdir.resolve(conversation_id)).path)
        else:
            workdir = await self._store.get_chat_setting(
                conversation_id, "workdir", str(env_fallback_workdir())
            )
        agent = await self._store.get_chat_setting(conversation_id, "agent", "sdd-orchestrator")

        # Install the early-persist sink so the id is stored the instant the
        # backend creates the session (a cancelled turn must not lose it).
        self._install_session_id_sink(backend, conversation_id)

        # Check if cancelled during settings resolution (before subprocess)
        if conversation_id in self._cancel:
            return {
                "stdout": "",
                "stderr": "",
                "returncode": 0,
                "cancelled": True,
                "session_id": None,
                "permission_reply_not_accepted": False,
            }

        # ``on_progress`` is only forwarded when the backend streams: the CLI
        # backend (and test doubles) accept it, but there is nothing to push.
        if on_progress is None:
            result = await backend.execute(
                prompt=prompt,
                model=model,
                session_id=session.real_id if session else None,
                agent=agent,
                workdir=str(workdir),
                source_label=source_label,
                timeout=timeout_val,
            )
        else:
            result = await backend.execute(
                prompt=prompt,
                model=model,
                session_id=session.real_id if session else None,
                agent=agent,
                workdir=str(workdir),
                source_label=source_label,
                timeout=timeout_val,
                on_progress=on_progress,
            )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "cancelled": conversation_id in self._cancel or result.cancelled,
            "session_id": result.session_id,
            "permission_reply_not_accepted": result.permission_reply_not_accepted,
        }

    def _install_session_id_sink(
        self, backend: AIBackend, conversation_id: ConversationKey
    ) -> None:
        """Wire the backend's early-persist hook when it supports one.

        Backends that can report the id before the turn ends (the server and
        the CLI) persist it through :meth:`_persist_session_id`. Doubles without
        ``set_session_id_sink`` are left untouched, so their behavior is
        unchanged.
        """
        from services.ai_backend import SessionIdSinkBackend

        if not isinstance(backend, SessionIdSinkBackend):
            return

        async def sink(session_id: str) -> None:
            await self._persist_session_id(conversation_id, session_id)

        backend.set_session_id_sink(sink)

    # ── Internal: response delivery ─────────────────────────────────

    async def _deliver_response(
        self,
        conversation_id: ConversationKey,
        result: dict[str, Any],
        handle: ProgressHandle,
        view: ChatView,
        session: SessionInfo | None,
    ) -> None:
        """Format and send the OpenCode response, or show an error.

        UI copy comes exclusively from ``view.strings``: this method supplies
        only the data (status, result, error) and the substitutions.
        """
        from formatting.markdown import _assemble_response, clean_opencode_output

        strings = view.strings

        # Handle cancellation
        if result["cancelled"]:
            await handle.error(strings.cancelled)
            return

        # Handle hard errors (non-zero exit + empty stdout, or errors via stderr)
        has_error = result["returncode"] != 0 or (result["stderr"] and not result["stdout"].strip())
        if has_error:
            error_text = clean_opencode_output(
                result["stderr"] or result["stdout"] or "Unknown error"
            )
            await handle.error(strings.error.format(error=error_text[:500]))
            return

        # Process and format the response
        cleaned = clean_opencode_output(result["stdout"])
        response = _assemble_response(cleaned, result["stderr"])

        if not response.strip():
            if result["returncode"] != 0:
                response = strings.error_code_no_output.format(code=result["returncode"])
            else:
                response = strings.completed_no_output

        # A permission decision the server never accepted must be VISIBLE: the
        # agent may still be waiting, so a silent success marker would be a lie.
        not_accepted = bool(result.get("permission_reply_not_accepted"))
        if not_accepted:
            response = f"{response}\n\n{strings.permission_not_accepted_notice}"

        # Prepend new-session header for brand-new sessions
        if session is not None and not session.real_id and session.name:
            response = strings.new_session.format(name=session.name) + response

        # Track whether we actually delivered content
        is_success = result["returncode"] == 0

        # Send formatted response via the view. ``response_sent`` is True
        # only when the WHOLE response reached the user (F8): a partially
        # delivered response is a failure for status purposes.
        response_sent = await view.send(response)

        from utils.logging import mask_conversation_key

        logger.info(
            "OpenCode response delivered",
            extra={
                "event": "opencode_response_delivered",
                "chat_id_masked": mask_conversation_key(conversation_id),
                "response_length": len(response),
                "delivered": response_sent,
            },
        )

        # Always reach a terminal state — the placeholder must never be left
        # stuck on the processing text. Before F8, an undelivered response
        # called NEITHER finish NOR error, so the placeholder message lived
        # forever.
        if response_sent and is_success and not not_accepted:
            try:
                await handle.finish(strings.completed)
            except Exception:
                log_exception(
                    "completion_edit_failed",
                    module=__name__,
                    level=logging.DEBUG,
                )
        elif response_sent and not_accepted:
            await handle.error(strings.permission_not_accepted)
        elif not response_sent:
            await handle.error(strings.delivery_failed)
        else:
            # Defensive: a non-successful result that still delivered content.
            # Unreachable today (non-zero exit is handled above) but keeps the
            # placeholder from sticking if that changes.
            await handle.error(strings.error_code.format(code=result["returncode"]))

        # Increment prompt count
        try:
            await self._store.increment_prompt_count(conversation_id)
        except Exception as e:
            logger.warning(
                "Failed to increment prompt count",
                extra={
                    "event": "prompt_count_error",
                    "chat_id": conversation_id,
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )

    async def _persist_session_id(
        self,
        conversation_id: ConversationKey,
        session_id: str | None,
    ) -> None:
        """Persist the real OpenCode session ID so the next prompt continues it.

        Both transports return the id EXPLICITLY (CLI JSON events, or the
        server-created session), so there is no stdout regex anymore: an
        absent ``session_id`` simply means "nothing to persist".
        """
        if not session_id:
            return

        active = await self._store.get_active_session(conversation_id)
        if active is not None and not active.real_id:
            await self._store.update_session_id(conversation_id, session_id)
            from utils.logging import mask_conversation_key

            logger.info(
                "Captured OpenCode session ID",
                extra={
                    "event": "session_id_captured",
                    "chat_id_masked": mask_conversation_key(conversation_id),
                    "session_name": active.name,
                    "session_id": session_id,
                },
            )

    # ── Internal: logging ───────────────────────────────────────────

    def _log_prompt(self, conversation_id: ConversationKey, prompt: str, model: str) -> None:
        """Log the incoming prompt (with credential redaction).

        Phase 2 logging: structured `event=prompt_received` with chat_id_masked,
        model, prompt_length, and (truncated) preview. Credentials in the
        prompt are replaced with `[REDACTED]` before logging.
        """
        from utils.logging import mask_conversation_key

        truncated = prompt[:100] + "..." if len(prompt) > 100 else prompt
        safe_prompt = truncated
        for pattern in ["sk-", "Bearer ", "-----BEGIN", "token=", "secret="]:
            if pattern.lower() in safe_prompt.lower():
                safe_prompt = "[REDACTED - possible credential]"
                break
        logger.info(
            "Prompt received",
            extra={
                "event": "prompt_received",
                "chat_id_masked": mask_conversation_key(conversation_id),
                "model": model,
                "prompt_length": len(prompt),
                "preview": safe_prompt,
            },
        )

    @staticmethod
    async def _stop_progress(progress_task: asyncio.Task[Any]) -> None:
        """Cancel the periodic progress updater and await its termination.

        Called on EVERY path that reaches a terminal state (normal
        completion and the pre-execution cancel early return) so a stray
        ``update()`` can never overwrite the terminal cancelled/completed text.
        """
        progress_task.cancel()
        # Cancelled by us on purpose — collecting the CancelledError is
        # expected, not an error.
        with contextlib.suppress(asyncio.CancelledError):
            await progress_task

    async def _update_progress(self, handle: ProgressHandle, strings: CoreStrings) -> None:
        start = time.monotonic()
        try:
            while True:
                await asyncio.sleep(PROGRESS_UPDATE_INTERVAL)
                elapsed = int(time.monotonic() - start)
                await handle.update(strings.processing_elapsed.format(elapsed=elapsed))
        except asyncio.CancelledError:
            # _update_progress is the periodic "(Ns)" editor; cancel propagation
            # is how the prompt loop stops it — expected, not a failure.
            pass
        except Exception:
            log_exception(
                "progress_update_failed",
                module=__name__,
                level=logging.DEBUG,
            )


def _messages_from_server(raw: list[dict[str, Any]]) -> list[Message]:
    """Map ``GET /session/{id}/message`` payloads to renderable ``Message``s.

    The HTTP shape is ``[{"info": {...}, "parts": [...]}]``; only ``text`` parts
    of a message with a known role are visible. Reasoning, tool and step parts
    are intentionally dropped: they are not conversation turns.
    """
    messages: list[Message] = []
    for item in raw:
        info = item.get("info")
        if not isinstance(info, dict):
            continue
        role = info.get("role")
        if not isinstance(role, str) or not role:
            continue
        texts: list[str] = []
        parts: list[MessagePart] = []
        raw_parts = item.get("parts")
        if isinstance(raw_parts, list):
            for raw_part in raw_parts:
                if not isinstance(raw_part, dict) or raw_part.get("type") != "text":
                    continue
                text = raw_part.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text)
                    parts.append(MessagePart(type="text", text=text))
        joined = "\n\n".join(texts)
        if not joined.strip():
            continue
        message_id = info.get("id")
        created = info.get("time")
        timestamp = 0
        if isinstance(created, dict):
            raw_created = created.get("created")
            if isinstance(raw_created, int):
                timestamp = raw_created
        messages.append(
            Message(
                id=message_id if isinstance(message_id, str) else "",
                role=role,
                text=joined,
                timestamp=timestamp,
                parts=tuple(parts),
            )
        )
    return messages
