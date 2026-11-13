"""AppContainer — explicit, typed dependency injection container.

This is the TELEGRAM composition payload: it bundles the shared core services
with the Telegram transport (``MessageSender`` / ``BotPort``). It lives in
``frontends/telegram`` so ``services`` never imports a frontend.

All bot dependencies in one dataclass. Immutable after construction.
Injected via PTB's context.application.bot_data["container"].
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from frontends.telegram.bot_port import BotPort
    from frontends.telegram.message_sender import MessageSender
    from services.ai_provider_factory import AIProviderFactory
    from services.health_service import HealthService
    from services.permission_router import PermissionRouter
    from services.prompt_service import PromptService
    from services.session_service import SessionService
    from services.session_store import SessionStore
    from services.settings_service import SettingsService
    from services.status_service import StatusService
    from services.workdir_service import WorkdirService


@dataclass
class AppContainer:
    """All bot dependencies. Handlers receive this via context."""

    session_store: SessionStore
    message_sender: MessageSender
    prompt_service: PromptService
    provider_factory: AIProviderFactory
    status_service: StatusService
    health_service: HealthService
    session_service: SessionService
    settings_service: SettingsService
    workdir_service: WorkdirService
    bot_port: BotPort
    start_time: float
    allowed_chat_ids: list[int]
    default_model: str
    #: ``None`` with ``OPENCODE_BACKEND=cli`` — the permission callback then
    #: answers "Ya fue resuelto" instead of resolving anything.
    permission_router: PermissionRouter | None = None
