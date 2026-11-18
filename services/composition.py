"""Composition root — the frontend-agnostic core object graph.

Both entry points build the SAME core by calling :func:`build_prompt_stack`:
  - the Telegram entry point (``bot.py::run_bot``), and
  - the future Flet entry point.

This module is PTB-free on purpose: it must never import ``telegram`` (nor
``flet``). The Telegram adapters (``TelegramAdapter``, ``MessageSender``,
``AppContainer``) stay in ``bot.py``; the Flet adapters will live in its own
package. See design `sdd/flet-preparation/design` (#426) for the boundary.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from core_config import (
    OPENAI_API_KEY,
    OPENCODE_BACKEND,
    OPENCODE_CMD,
    OPENCODE_PERMISSION_TIMEOUT,
    OPENCODE_SERVER_ALLOW_REMOTE,
    OPENCODE_SERVER_BINARY,
    OPENCODE_SERVER_HOST,
    OPENCODE_SERVER_PASSWORD,
    OPENCODE_SERVER_PORT,
    OPENCODE_TIMEOUT,
    SESSIONS_PATH,
)
from opencode.sessions import OpenCodeSessions
from services.agent_sessions import AgentSessions
from services.ai_provider_factory import AIProviderFactory
from services.conversations_service import ConversationsService
from services.effective_workdir import (
    ENV_FALLBACK_WORKDIR,
    PROJECT_ROOT,
    EffectiveWorkdir,
)
from services.health_service import HealthService
from services.model_catalog import (
    AliasModelsSource,
    CliModelsSource,
    HttpProvidersSource,
    ModelSource,
    OpenCodeModelCatalog,
    SkipSource,
)
from services.opencode_cli_backend import OpenCodeCLIBackend
from services.opencode_server import (
    OpenCodeServerClient,
    OpenCodeServerManager,
    ServerConfig,
)
from services.opencode_server_backend import OpenCodeServerBackend
from services.permission_router import PermissionRouter
from services.permissions import PermissionPort
from services.prompt_service import PromptService
from services.pull_request_service import PullRequestService, default_runners
from services.session_service import SessionService
from services.session_store import SessionStore
from services.settings_service import SettingsService
from services.status_service import StatusService
from services.transcription_service import TranscriptionService, WhisperTranscriber
from services.workdir_service import WorkdirService


@dataclass(frozen=True)
class CoreStack:
    """The shared core graph both frontends depend on.

    The Telegram-only payload (``MessageSender``/``AppContainer``/``BotPort``)
    never enters here; the four ``*_service`` fields are PTB-free.
    """

    session_store: SessionStore
    effective_workdir: EffectiveWorkdir
    provider_factory: AIProviderFactory
    prompt_service: PromptService
    status_service: StatusService
    health_service: HealthService
    session_service: SessionService
    settings_service: SettingsService
    workdir_service: WorkdirService
    pull_request_service: PullRequestService
    transcription_service: TranscriptionService
    agent_sessions: AgentSessions
    conversations_service: ConversationsService
    model_catalog: OpenCodeModelCatalog
    start_time: float
    backend: str = OPENCODE_BACKEND
    permission_timeout: int = OPENCODE_PERMISSION_TIMEOUT
    # Server-mode only; all stay ``None`` with ``OPENCODE_BACKEND=cli``.
    server_manager: OpenCodeServerManager | None = None
    server_client: OpenCodeServerClient | None = None
    permission_port: PermissionPort | None = None
    #: The concrete router when the active port is a ``PermissionRouter``
    #: (Phase 4/5 register their presenters on it).
    permission_router: PermissionRouter | None = None


def build_prompt_stack(
    *,
    sessions_path: str | Path = SESSIONS_PATH,
    default_provider: str = "opencode",
    opencode_cmd: str = OPENCODE_CMD,
    workdir: str = str(ENV_FALLBACK_WORKDIR),
    timeout: int = OPENCODE_TIMEOUT,
    sessions_directory: str | Path = PROJECT_ROOT,
    start_time: float | None = None,
    backend: str = OPENCODE_BACKEND,
    server_host: str = OPENCODE_SERVER_HOST,
    server_port: int = OPENCODE_SERVER_PORT,
    server_password: str = OPENCODE_SERVER_PASSWORD,
    server_binary: str = OPENCODE_SERVER_BINARY,
    server_allow_remote: bool = OPENCODE_SERVER_ALLOW_REMOTE,
    permission_timeout: int = OPENCODE_PERMISSION_TIMEOUT,
    permission_port: PermissionPort | None = None,
    server_client: OpenCodeServerClient | None = None,
    server_manager: OpenCodeServerManager | None = None,
) -> CoreStack:
    """Build the frontend-agnostic core graph.

    Keyword-only, with defaults taken from ``core_config`` so it is callable as
    a bare ``build_prompt_stack()`` yet unit-testable by injecting fakes.

    Registers the ``"opencode"`` provider (moved out of ``bot.py``) and owns
    ``start_time`` — both frontends therefore share uptime semantics.

    ``backend`` selects the transport:
      - ``"server"`` (default): ``opencode serve`` over HTTP with SSE.
      - ``"cli"``: the one-shot ``opencode run`` subprocess (rollback path).

    Server construction is LAZY: creating the manager/client opens no socket
    and spawns no process. ``OpenCodeServerBackend`` calls
    ``manager.ensure_running()`` on its first ``execute``.

    ``permission_port`` defaults to a ``PermissionRouter`` with no presenters
    (Phase 3: fail closed). Phase 4/5 register their UI adapters on
    ``CoreStack.permission_router``.
    """
    selected_backend = backend.strip().lower()
    if selected_backend not in {"server", "cli"}:
        raise ValueError(
            f"OPENCODE_BACKEND inválido: {backend!r}. Valores permitidos: 'server', 'cli'."
        )

    session_store = SessionStore(Path(sessions_path))
    resolved_start_time = time.time() if start_time is None else start_time

    # Dependency order: store -> agent_sessions (the history/catalog seam) ->
    # effective_workdir -> session_service -> prompt_service. Every consumer of
    # the effective workdir shares THIS resolver so prompts, history, status and
    # session stamps can never disagree.
    agent_sessions = OpenCodeSessions(sessions_directory)
    effective_workdir = EffectiveWorkdir(
        session_store,
        agent_sessions,
        fallback=Path(workdir),
    )

    provider_factory = AIProviderFactory(default_provider=default_provider)
    manager: OpenCodeServerManager | None = None
    client: OpenCodeServerClient | None = None
    resolved_port: PermissionPort | None = None
    router: PermissionRouter | None = None

    if selected_backend == "server":
        manager = server_manager or OpenCodeServerManager(
            ServerConfig(
                host=server_host,
                port=server_port,
                password=server_password,
                binary=server_binary,
                directory=workdir,
                allow_remote=server_allow_remote,
            )
        )
        client = server_client or OpenCodeServerClient(
            manager.base_url,
            server_password,
            workdir,
        )
        resolved_port = (
            permission_port
            if permission_port is not None
            else PermissionRouter(presenters=[], timeout=permission_timeout)
        )
        if isinstance(resolved_port, PermissionRouter):
            router = resolved_port
        provider_factory.register(
            "opencode",
            OpenCodeServerBackend,
            client=client,
            # The backend only needs the callable; pass the port's bound
            # method so any PermissionPort implementation can be injected.
            permission_handler=resolved_port.request_permission,
            timeout=timeout,
            permission_timeout=permission_timeout,
            manager=manager,
        )
    else:
        provider_factory.register(
            "opencode",
            OpenCodeCLIBackend,
            opencode_cmd=opencode_cmd,
            workdir=workdir,
            timeout=timeout,
        )

    session_service = SessionService(
        session_store,
        catalog=agent_sessions,
        effective_workdir=effective_workdir,
    )
    prompt_service = PromptService(
        session_store=session_store,
        provider_factory=provider_factory,
        effective_workdir=effective_workdir,
        session_service=session_service,
    )
    conversations_service = ConversationsService(
        agent_sessions,
        session_service,
        session_store,
        effective_workdir=effective_workdir,
    )

    git_runner, gh_runner = default_runners()

    # Model catalog fallback chain: HTTP (complete) -> CLI (slow) -> aliases
    # (static) -> empty. ``SkipSource`` keeps the HTTP slot when there is no
    # server client (``OPENCODE_BACKEND=cli``).
    model_sources: list[ModelSource] = [
        HttpProvidersSource(client) if client is not None else SkipSource(),
        CliModelsSource(opencode_cmd),
        AliasModelsSource(),
    ]
    model_catalog = OpenCodeModelCatalog(model_sources, session_store)
    workdir_service = WorkdirService(session_store, root=Path(workdir))

    return CoreStack(
        session_store=session_store,
        effective_workdir=effective_workdir,
        provider_factory=provider_factory,
        prompt_service=prompt_service,
        status_service=StatusService(
            session_store,
            resolved_start_time,
            effective_workdir=effective_workdir,
            agent_sessions=agent_sessions,
        ),
        health_service=HealthService(session_store, resolved_start_time),
        session_service=session_service,
        settings_service=SettingsService(
            session_store,
            workdir_service=workdir_service,
            model_catalog=model_catalog,
        ),
        workdir_service=workdir_service,
        pull_request_service=PullRequestService(session_store, git=git_runner, gh=gh_runner),
        transcription_service=TranscriptionService(
            WhisperTranscriber(OPENAI_API_KEY), enabled=bool(OPENAI_API_KEY)
        ),
        agent_sessions=agent_sessions,
        conversations_service=conversations_service,
        model_catalog=model_catalog,
        start_time=resolved_start_time,
        backend=selected_backend,
        permission_timeout=permission_timeout,
        server_manager=manager,
        server_client=client,
        permission_port=resolved_port,
        permission_router=router,
    )
