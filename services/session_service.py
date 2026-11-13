"""SessionService: session merge/discover/adopt/delete, frontend-agnostic.

Moves the session business logic out of ``handlers/sessions.py`` (B9-B14) so a
second frontend can reuse it. This module is PTB-free.

Design decisions (design ``sdd/flet-preparation/design`` #426):
  - ``SessionStore`` stays the single source of truth for bot sessions; only
    ``fetch_opencode_sessions`` is used for discovery (no third reader).
  - The ``invalidate_opencode_sessions_cache()`` call lives HERE, once per
    mutating operation (create/switch/adopt/delete), not in the handlers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from core_config import DEFAULT_SESSION_NAME, INTERNAL_SUBPROCESS_TIMEOUT, OPENCODE_CMD
from opencode.client import query_opencode_db
from persistence.sessions import (
    fetch_opencode_sessions,
    invalidate_opencode_sessions_cache,
)
from services.agent_sessions import SessionCatalog, is_valid_real_id
from services.session_commands import (
    SessionAdopted,
    SessionCommand,
    SessionCommandKind,
    SessionCreated,
    SessionDeleted,
    SessionDiscovered,
    SessionInfoResult,
    SessionListed,
    SessionOutcome,
    SessionSwitched,
    SessionUsage,
)
from services.session_store import (
    SessionExistsError,
    SessionInfo,
    SessionNotFoundError,
    SessionStore,
)
from utils.logging import get_module_logger, log_exception, mask_conversation_key

if TYPE_CHECKING:
    from collections.abc import Mapping

    from services.conversation import ConversationKey

logger = get_module_logger(__name__)

_SESSION_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,30}$")


def _ms_to_iso(value: object) -> str | None:
    """Convert an epoch-millisecond value to ISO 8601 UTC, best-effort."""
    if value is None or isinstance(value, bool):
        return None
    try:
        millis = int(str(value))
    except (TypeError, ValueError):
        return None
    try:
        return datetime.fromtimestamp(millis / 1000, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _normalize_model(value: object) -> str | None:
    """Render the stored JSON model text as ``providerID/id``."""
    if value is None:
        return None

    if isinstance(value, dict):
        parsed: object = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text

    if isinstance(parsed, dict):
        provider = parsed.get("providerID")
        model_id = parsed.get("id")
        if isinstance(provider, str) and isinstance(model_id, str) and provider and model_id:
            return f"{provider}/{model_id}"
        return None

    return str(parsed)


@dataclass(frozen=True)
class DiscoveredSession:
    """An OpenCode session found through the CLI, with adoption metadata."""

    id: str
    title: str
    updated: str = ""
    adopted_name: str | None = None
    prompt_count: int = 0


@dataclass(frozen=True)
class SessionListSnapshot:
    """Bot-managed sessions plus OpenCode sessions not yet adopted."""

    sessions: list[SessionInfo]
    unadopted: list[DiscoveredSession]


@dataclass(frozen=True)
class SessionDetails:
    """The RAW data behind ``/session info``, formatted by the view.

    The core never decides display strings: ``real_id`` truncation, the
    ``(sin ID aún)`` placeholder, the ``nunca`` fallback and the relative time
    all live in the frontend. ``enrichment`` is the raw OpenCode DB map
    (``message_count`` / ``created_at`` / ``model`` when present).
    """

    name: str
    real_id: str | None
    title: str
    created: str
    last_used: str | None
    prompt_count: int
    enrichment: Mapping[str, object]


class SessionNotDiscoveredError(Exception):
    """Raised when adopting a real ID that OpenCode does not know about."""

    def __init__(self, real_id: str) -> None:
        self.real_id = real_id
        super().__init__(f"OpenCode session '{real_id}' not found")


class InvalidSessionNameError(ValueError):
    """Raised when an adoption target name fails the naming rules."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Invalid session name: '{name}'")


class SessionService:
    """Session operations shared by every frontend."""

    def __init__(
        self,
        session_store: SessionStore,
        *,
        catalog: SessionCatalog | None = None,
    ) -> None:
        self._store = session_store
        self._catalog = catalog

    async def get_info(
        self, conversation_id: ConversationKey, name: str | None = None
    ) -> SessionDetails:
        """Resolve a session (explicit / active / default) and return its RAW data.

        Raises:
            SessionNotFoundError: when the resolved target does not exist. The
                exception message carries the resolved name so the view can
                render the not-found copy.
        """
        sessions = await self._store.list_sessions(conversation_id)
        active_session = await self._store.get_active_session(conversation_id)
        target_name = (
            name if name else (active_session.name if active_session else DEFAULT_SESSION_NAME)
        )

        target = next((s for s in sessions if s.name == target_name), None)
        if target is None:
            raise SessionNotFoundError(target_name)

        enrichment = await self.enrich(target.real_id)
        return SessionDetails(
            name=target.name,
            real_id=target.real_id,
            title=target.title,
            created=target.created,
            last_used=target.last_used,
            prompt_count=target.prompt_count,
            enrichment=enrichment,
        )

    async def dispatch(
        self, conversation_id: ConversationKey, command: SessionCommand
    ) -> SessionOutcome:
        """Execute a parsed ``/session`` command and return a typed outcome.

        Missing required arguments yield :class:`SessionUsage` without touching
        the store. Domain errors (``SessionExistsError``,
        ``SessionNotFoundError``, ``InvalidSessionNameError``,
        ``SessionNotDiscoveredError``) propagate so the view maps them to copy.
        """
        kind = command.kind

        if kind is SessionCommandKind.NEW:
            if not command.name:
                return SessionUsage(kind=kind)
            return SessionCreated(await self.create(conversation_id, command.name))

        if kind is SessionCommandKind.LIST:
            return SessionListed(await self.list_with_discovered(conversation_id))

        if kind is SessionCommandKind.SWITCH:
            if not command.name:
                return SessionUsage(kind=kind)
            return SessionSwitched(await self.switch(conversation_id, command.name))

        if kind is SessionCommandKind.DELETE:
            if not command.name:
                return SessionUsage(kind=kind)
            real_id = await self.delete(conversation_id, command.name)
            return SessionDeleted(name=command.name, real_id=real_id)

        if kind is SessionCommandKind.INFO:
            return SessionInfoResult(await self.get_info(conversation_id, command.name))

        if kind is SessionCommandKind.DISCOVER:
            return SessionDiscovered(await self.discover(conversation_id))

        if kind is SessionCommandKind.ADOPT:
            if not command.real_id or not command.name:
                return SessionUsage(kind=kind)
            await self.adopt(conversation_id, command.real_id, command.name)
            return SessionAdopted(name=command.name, real_id=command.real_id)

        return SessionUsage(kind=kind, raw=command.raw)

    async def create(self, conversation_id: ConversationKey, name: str) -> SessionInfo:
        """Create a named session and invalidate the discovery cache."""
        session = await self._store.create_session(conversation_id, name)
        invalidate_opencode_sessions_cache()
        return session

    async def switch(self, conversation_id: ConversationKey, name: str) -> SessionInfo:
        """Switch the active session and invalidate the discovery cache."""
        session = await self._store.switch_session(conversation_id, name)
        invalidate_opencode_sessions_cache()
        return session

    async def list_with_discovered(self, conversation_id: ConversationKey) -> SessionListSnapshot:
        """Merge bot sessions with OpenCode-discovered, not-yet-adopted ones."""
        bot_sessions = await self._store.list_sessions(conversation_id)
        bot_ids = {s.real_id for s in bot_sessions if s.real_id}

        try:
            oc_sessions = await fetch_opencode_sessions()
        except Exception:
            log_exception(
                "oc_sessions_fetch_failed",
                module=__name__,
                level=logging.DEBUG,
            )
            oc_sessions = []

        unadopted = [
            DiscoveredSession(
                id=oc["id"],
                title=oc.get("title", oc["id"][:16]),
                updated=oc.get("updated", ""),
            )
            for oc in oc_sessions
            if oc["id"] not in bot_ids
        ]
        return SessionListSnapshot(sessions=bot_sessions, unadopted=unadopted)

    async def discover(self, conversation_id: ConversationKey) -> list[DiscoveredSession]:
        """Return every OpenCode session annotated with its adoption status."""
        oc_sessions = await fetch_opencode_sessions()
        sessions = await self._store.list_sessions(conversation_id)
        adopted_map = {s.real_id: s.name for s in sessions if s.real_id}

        discovered: list[DiscoveredSession] = []
        for oc in oc_sessions:
            sid = oc["id"]
            friendly_name = adopted_map.get(sid)
            prompt_count = 0
            if friendly_name:
                matching = next((s for s in sessions if s.name == friendly_name), None)
                prompt_count = matching.prompt_count if matching else 0
            discovered.append(
                DiscoveredSession(
                    id=sid,
                    title=oc.get("title", sid[:16]),
                    updated=oc.get("updated", ""),
                    adopted_name=friendly_name,
                    prompt_count=prompt_count,
                )
            )
        return discovered

    async def delete(self, conversation_id: ConversationKey, name: str) -> str | None:
        """Delete a stored session and clean it up in OpenCode if it had an ID."""
        real_id = await self._store.delete_session(conversation_id, name)
        invalidate_opencode_sessions_cache()

        if real_id:
            try:
                proc = await asyncio.create_subprocess_exec(
                    OPENCODE_CMD,
                    "session",
                    "delete",
                    real_id,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.wait(), timeout=INTERNAL_SUBPROCESS_TIMEOUT)
                logger.info(
                    "Deleted OpenCode session %s for chat %s",
                    real_id,
                    mask_conversation_key(conversation_id),
                )
            except Exception as e:
                logger.warning(
                    "OpenCode session delete failed",
                    extra={
                        "event": "oc_session_delete_failed",
                        "real_id": real_id,
                        "chat_id_masked": mask_conversation_key(conversation_id),
                        "error_type": type(e).__name__,
                    },
                    exc_info=True,
                )
        return real_id

    async def enrich(self, real_id: str | None) -> dict[str, Any]:
        """Query OpenCode's DB for message count / created_at / model."""
        data: dict[str, Any] = {}
        if not real_id or not is_valid_real_id(real_id):
            return data

        try:
            msg_rows = await query_opencode_db(
                f"SELECT COUNT(*) as count FROM message WHERE session_id = '{real_id}'",
                raise_on_error=True,
            )
            if msg_rows:
                data["message_count"] = msg_rows[0].get("count", "?")

            session_rows = await query_opencode_db(
                f"SELECT time_created, model FROM session WHERE id = '{real_id}'",
                raise_on_error=True,
            )
            if session_rows:
                row = session_rows[0]
                created_at = _ms_to_iso(row.get("time_created"))
                if created_at:
                    data["created_at"] = created_at
                model = _normalize_model(row.get("model"))
                if model:
                    data["model"] = model
        except Exception:
            log_exception(
                "oc_db_enrich_failed",
                module=__name__,
                level=logging.WARNING,
                real_id=real_id,
            )
        return data

    async def adopt(self, conversation_id: ConversationKey, real_id: str, name: str) -> None:
        """Adopt an existing OpenCode session under a bot-managed name.

        Raises:
            InvalidSessionNameError: if ``name`` fails the naming rules.
            SessionNotDiscoveredError: if ``real_id`` is unknown to OpenCode.
            SessionExistsError: if ``name`` is already used for this chat.
        """
        if not _SESSION_NAME_PATTERN.match(name):
            raise InvalidSessionNameError(name)

        if self._catalog is not None:
            summary = await self._catalog.get_session(real_id)
            if summary is None:
                raise SessionNotDiscoveredError(real_id)
            title = summary.title
        else:
            oc_sessions = await fetch_opencode_sessions()
            oc_by_id = {s["id"]: s for s in oc_sessions}

            if real_id not in oc_by_id:
                raise SessionNotDiscoveredError(real_id)
            title = oc_by_id[real_id].get("title", name)

        existing = await self._store.list_sessions(conversation_id)
        if any(s.name == name for s in existing):
            raise SessionExistsError(f"Session '{name}' already exists")

        await self._store.create_session(conversation_id, name)
        await self._store.update_session_id(conversation_id, real_id)
        await self._store.update_session_title(conversation_id, name, title)
        invalidate_opencode_sessions_cache()

        logger.info(
            "Session '%s' adopted (%s) for %s",
            name,
            real_id,
            mask_conversation_key(conversation_id),
        )
