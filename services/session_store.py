"""SessionStore: clean, typed API wrapping persistence/sessions.py.

This is the SINGLE SOURCE OF TRUTH for session state in the bot.
It replaces:
  - 5 global dicts in handlers/__init__.py (active_sessions, current_model, ...)
  - Direct JSON read/write in 3+ handler modules
  - Ad-hoc sync logic scattered across the codebase

Architecture rationale:
  The persistence/sessions.py module is a low-level data access layer
  (load/save JSON, TTL cache, atomic writes). SessionStore is the
  SERVICE layer — it adds domain logic (validation, error types, typed
  return values via SessionInfo) while leaning on the DAL for storage.

  Why a class instead of more module-level functions?
    - Stateful: TTL cache + asyncio.Lock need an owning context
    - Testable: can be instantiated with a temp file for tests
    - Injectable: bot.py creates one instance, handlers use it
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from core_config import DEFAULT_MODEL, DEFAULT_SESSION_NAME
from services.conversation import ConversationKey
from utils.logging import get_module_logger, mask_conversation_key

logger = get_module_logger(__name__)

# Legacy bare keys migrated on load: numeric Telegram chat ids and the old
# ``"flet"`` id. Negative ids are valid Telegram group/channel ids.
_LEGACY_NUMERIC_KEY = re.compile(r"^-?\d+$")

_TELEGRAM_PREFIX = "telegram:"
_FLET_PREFIX = "flet:"


def _migrate_key(key: str) -> str:
    """Return the namespaced form of a (possibly legacy) conversation key.

    Idempotent: already-prefixed keys are returned unchanged.
    """
    if key.startswith(_TELEGRAM_PREFIX) or key.startswith(_FLET_PREFIX):
        return key
    if key == "flet":
        return "flet:local"
    if _LEGACY_NUMERIC_KEY.match(key):
        return f"telegram:{key}"
    return key


def migrate_legacy_keys(data: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Rewrite legacy conversation keys in a sessions.json payload.

    Returns the migrated mapping and the number of keys that changed. When a
    legacy key and its canonical prefixed twin both exist, the canonical entry
    wins (it already represents the new state) so the migration never
    overwrites real data.
    """
    migrated: dict[str, Any] = {}
    changed = 0
    for raw_key, value in data.items():
        key = str(raw_key)
        new_key = _migrate_key(key)
        if new_key != key:
            changed += 1
            if new_key in migrated:
                # Canonical key already seen (or collision): keep it.
                continue
        migrated[new_key] = value
    return migrated, changed


# ── Domain objects ─────────────────────────────────────────────────────


@dataclass
class SessionInfo:
    """Immutable snapshot of a named session's state.

    Returned by all read operations — callers never touch raw dicts.
    """

    name: str
    real_id: str | None
    title: str
    created: str  # ISO 8601
    last_used: str | None  # ISO 8601 or None
    prompt_count: int
    is_active: bool = False
    #: Effective workdir the session ran in. ``None`` for legacy records; no
    #: migration is performed, so an old ``sessions.json`` simply reads ``None``.
    workdir: str | None = None


# ── Domain errors ──────────────────────────────────────────────────────


class SessionExistsError(Exception):
    """Raised when trying to create a session that already exists."""


class SessionNotFoundError(Exception):
    """Raised when referencing a session that does not exist."""


# ── Structural port ────────────────────────────────────────────────────


class SessionStorePort(Protocol):
    """Minimal structural contract ``PromptService`` needs from session state.

    ``SessionStore`` satisfies this; tests can supply a small in-memory
    double without inheriting from the concrete production class.
    """

    async def get_model(self, conversation_id: ConversationKey) -> str: ...

    async def get_active_session(self, conversation_id: ConversationKey) -> SessionInfo | None: ...

    async def get_chat_setting(
        self, conversation_id: ConversationKey, key: str, default: Any = None
    ) -> Any: ...

    async def increment_prompt_count(self, conversation_id: ConversationKey) -> int: ...

    async def update_session_id(self, conversation_id: ConversationKey, real_id: str) -> None: ...


# ── Service ────────────────────────────────────────────────────────────


class SessionStore:
    """Typed, async-safe session state management.

    Wraps a JSON file on disk with:
      - asyncio.Lock for concurrent access safety
      - TTL cache (2 s) to avoid re-reading the file on every handler call
      - Atomic writes (write to .tmp → replace) for crash safety
    """

    def __init__(self, persistence_path: Path) -> None:
        """Initialize with path to the sessions JSON file.

        Args:
            persistence_path: Path to sessions.json on disk.
        """
        self._path = persistence_path
        self._lock = asyncio.Lock()
        self._cache: dict[str, Any] | None = None
        self._cache_time: float = 0.0
        self._cache_ttl: float = 2.0

    # ── Internal: file I/O ─────────────────────────────────────────

    @property
    def _backup_path(self) -> Path:
        """Path of the pre-migration backup (``sessions.json.bak``)."""
        return self._path.with_name(f"{self._path.name}.bak")

    def _write_payload(self, data: dict[str, Any]) -> None:
        """Write JSON atomically (caller must hold the lock)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_name(f"{self._path.name}.tmp")
        tmp_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp_path.replace(self._path)  # atomic on Windows & Unix

    def _write_backup(self, content: str) -> None:
        """Preserve the original payload before the first rewrite.

        The backup is written ONCE: if it already exists it is left untouched so
        an earlier copy of the pre-migration data is never clobbered.
        """
        if not self._backup_path.exists():
            self._backup_path.write_text(content, encoding="utf-8")

    def _load_sync(self) -> dict[str, Any]:
        """Read + migrate sessions.json. Runs while holding ``_lock``."""
        try:
            content = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.debug("SessionStore: no sessions.json at %s, starting fresh", self._path)
            return {}

        if not content.strip():
            return {}

        try:
            raw: object = json.loads(content)
        except json.JSONDecodeError:
            # Fail-safe: keep the unreadable payload recoverable and start
            # fresh instead of silently discarding data.
            self._write_backup(content)
            logger.error(
                "SessionStore: invalid JSON at %s, starting fresh (backup preserved)",
                self._path,
            )
            return {}

        if not isinstance(raw, dict):
            self._write_backup(content)
            logger.error(
                "SessionStore: sessions.json is not an object at %s, starting fresh",
                self._path,
            )
            return {}

        data: dict[str, Any] = raw
        migrated, changed = migrate_legacy_keys(data)
        if changed:
            # Backup BEFORE the rewrite. The atomic replace below either
            # succeeds fully or leaves the original file intact.
            self._write_backup(content)
            self._write_payload(migrated)
            logger.info(
                "Conversation keys migrated",
                extra={
                    "event": "conversation_key_migrated",
                    "count": changed,
                    "path": str(self._path),
                },
            )
        return migrated

    async def _load(self) -> dict[str, Any]:
        """Load sessions.json with TTL cache, lock protection and migration.

        Returns a FRESH COPY — callers can mutate safely.
        """
        now = asyncio.get_event_loop().time()
        if self._cache is not None and (now - self._cache_time) < self._cache_ttl:
            return self._cache

        async with self._lock:
            # Double-check: another task may have refreshed while waiting
            now = asyncio.get_event_loop().time()
            if self._cache is not None and (now - self._cache_time) < self._cache_ttl:
                return self._cache

            self._cache = self._load_sync()
            self._cache_time = asyncio.get_event_loop().time()
            return self._cache

    async def _save(self, data: dict[str, Any]) -> None:
        """Atomically persist session data to disk.

        Writes to a .tmp file first, then replaces the original.
        This prevents corruption on partial writes (crash-safe).
        """
        async with self._lock:
            self._write_payload(data)
            self._cache = data
            self._cache_time = asyncio.get_event_loop().time()

    # ── Public API: session CRUD ───────────────────────────────────

    async def get_active_session(self, conversation_id: ConversationKey) -> SessionInfo | None:
        """Return the currently active session for a chat, or None."""
        data = await self._load()
        chat_data = data.get(str(conversation_id), {})
        active_name = chat_data.get("active")
        if not active_name:
            return None
        sessions = chat_data.get("sessions", {})
        session = sessions.get(active_name)
        if not session:
            return None
        return SessionInfo(
            name=active_name,
            real_id=session.get("id"),
            title=session.get("title", active_name),
            created=session.get("created", ""),
            last_used=session.get("last_used"),
            prompt_count=session.get("prompt_count", 0),
            is_active=True,
            workdir=session.get("workdir"),
        )

    async def ensure_active(self, conversation_id: ConversationKey) -> SessionInfo:
        """Return the active session, activating ``default`` or creating it.

        This is the store-level half of SS-01: on a fresh key the first prompt
        must have a bot session to persist the OpenCode ``real_id`` into. The
        operation is idempotent — an already-active session is returned without
        writing anything.
        """
        active = await self.get_active_session(conversation_id)
        if active is not None:
            return active

        data = await self._load()
        cid = str(conversation_id)
        chat_data = data.setdefault(cid, {})
        sessions = chat_data.setdefault("sessions", {})

        if DEFAULT_SESSION_NAME in sessions:
            chat_data["active"] = DEFAULT_SESSION_NAME
            await self._save(data)
            s = sessions[DEFAULT_SESSION_NAME]
            return SessionInfo(
                name=DEFAULT_SESSION_NAME,
                real_id=s.get("id"),
                title=s.get("title", DEFAULT_SESSION_NAME),
                created=s.get("created", ""),
                last_used=s.get("last_used"),
                prompt_count=s.get("prompt_count", 0),
                is_active=True,
                workdir=s.get("workdir"),
            )

        return await self.create_session(conversation_id, DEFAULT_SESSION_NAME)

    async def create_session(
        self,
        conversation_id: ConversationKey,
        name: str,
        *,
        workdir: str | None = None,
        last_used: str | None = None,
    ) -> SessionInfo:
        """Create a new named session and set it as active.

        Args:
            workdir: Optional effective workdir stamped on the record (SS-05).
                Legacy records never get migrated; they simply read ``None``.
            last_used: Optional initial ``last_used``. ``new_chat`` passes
                ``now``; the explicit ``/session new`` path keeps ``None``.

        Raises:
            SessionExistsError: if a session with this name already exists.
        """
        data = await self._load()
        cid = str(conversation_id)
        chat_data = data.setdefault(cid, {})
        sessions = chat_data.setdefault("sessions", {})

        if name in sessions:
            raise SessionExistsError(f"Session '{name}' already exists")

        now = datetime.now(UTC).isoformat()
        record: dict[str, Any] = {
            "id": None,
            "title": name,
            "created": now,
            "last_used": last_used,
            "prompt_count": 0,
        }
        if workdir is not None:
            record["workdir"] = workdir
        sessions[name] = record
        chat_data["active"] = name
        await self._save(data)

        logger.info(
            "Session created",
            extra={
                "event": "session_created",
                "session_name": name,
                "chat_id_masked": mask_conversation_key(conversation_id),
            },
        )
        return SessionInfo(
            name=name,
            real_id=None,
            title=name,
            created=now,
            last_used=last_used,
            prompt_count=0,
            is_active=True,
            workdir=workdir,
        )

    async def switch_session(self, conversation_id: ConversationKey, name: str) -> SessionInfo:
        """Switch the active session to another existing one.

        Raises:
            SessionNotFoundError: if the named session does not exist.
        """
        data = await self._load()
        cid = str(conversation_id)
        sessions = data.get(cid, {}).get("sessions", {})

        if name not in sessions:
            raise SessionNotFoundError(f"Session '{name}' not found")

        data.setdefault(cid, {})["active"] = name
        await self._save(data)

        s = sessions[name]
        logger.info(
            "Session switched",
            extra={
                "event": "session_switched",
                "session_name": name,
                "chat_id_masked": mask_conversation_key(conversation_id),
            },
        )
        return SessionInfo(
            name=name,
            real_id=s.get("id"),
            title=s.get("title", name),
            created=s.get("created", ""),
            last_used=s.get("last_used"),
            prompt_count=s.get("prompt_count", 0),
            is_active=True,
            workdir=s.get("workdir"),
        )

    async def delete_session(self, conversation_id: ConversationKey, name: str) -> str | None:
        """Delete a session. Returns the real OpenCode session ID if it had one.

        If the deleted session was active, auto-switches to the next available.
        If no sessions remain, creates a fresh 'default'.

        Raises:
            SessionNotFoundError: if the session does not exist.
        """
        data = await self._load()
        cid = str(conversation_id)
        sessions = data.get(cid, {}).get("sessions", {})

        if name not in sessions:
            raise SessionNotFoundError(f"Session '{name}' not found")

        real_id: str | None = sessions[name].get("id")
        del sessions[name]

        # Auto-switch if the deleted session was active
        if data.get(cid, {}).get("active") == name:
            remaining = list(sessions.keys())
            if remaining:
                data[cid]["active"] = remaining[0]
            else:
                data[cid]["active"] = DEFAULT_SESSION_NAME
                now = datetime.now(UTC).isoformat()
                data.setdefault(cid, {}).setdefault("sessions", {})[DEFAULT_SESSION_NAME] = {
                    "id": None,
                    "title": DEFAULT_SESSION_NAME,
                    "created": now,
                    "last_used": None,
                    "prompt_count": 0,
                }

        await self._save(data)
        logger.info(
            "Session deleted",
            extra={
                "event": "session_deleted",
                "session_name": name,
                "chat_id_masked": mask_conversation_key(conversation_id),
            },
        )
        return real_id

    async def list_sessions(self, conversation_id: ConversationKey) -> list[SessionInfo]:
        """Return all sessions for a chat, with the active one flagged."""
        data = await self._load()
        cid = str(conversation_id)
        chat_data = data.get(cid, {})
        active_name = chat_data.get("active")
        sessions = chat_data.get("sessions", {})

        result: list[SessionInfo] = []
        for name, s in sessions.items():
            result.append(
                SessionInfo(
                    name=name,
                    real_id=s.get("id"),
                    title=s.get("title", name),
                    created=s.get("created", ""),
                    last_used=s.get("last_used"),
                    prompt_count=s.get("prompt_count", 0),
                    is_active=(name == active_name),
                    workdir=s.get("workdir"),
                )
            )
        return result

    # ── Public API: model preference ───────────────────────────────

    async def get_model(self, conversation_id: ConversationKey) -> str:
        """Return the model preference for a chat, falling back to DEFAULT_MODEL."""
        data = await self._load()
        model: str = data.get(str(conversation_id), {}).get("model", DEFAULT_MODEL)
        return model

    async def set_model(self, conversation_id: ConversationKey, model: str) -> None:
        """Persist a model preference for a chat."""
        data = await self._load()
        data.setdefault(str(conversation_id), {})["model"] = model
        await self._save(data)
        logger.info(
            "Model changed",
            extra={
                "event": "model_changed",
                "model": model,
                "chat_id_masked": mask_conversation_key(conversation_id),
            },
        )

    # ── Public API: prompt tracking ────────────────────────────────

    async def increment_prompt_count(self, conversation_id: ConversationKey) -> int:
        """Increment prompt count for the active session. Returns new count."""
        data = await self._load()
        cid = str(conversation_id)
        active_name = data.get(cid, {}).get("active", DEFAULT_SESSION_NAME)
        session = data.setdefault(cid, {}).setdefault("sessions", {}).setdefault(active_name, {})
        session["prompt_count"] = session.get("prompt_count", 0) + 1
        session["last_used"] = datetime.now(UTC).isoformat()
        await self._save(data)
        count: int = session["prompt_count"]
        return count

    async def update_session_id(self, conversation_id: ConversationKey, real_id: str) -> None:
        """Update the real OpenCode session ID for the active session."""
        data = await self._load()
        cid = str(conversation_id)
        active_name = data.get(cid, {}).get("active", DEFAULT_SESSION_NAME)
        sessions = data.setdefault(cid, {}).setdefault("sessions", {})
        if active_name in sessions:
            sessions[active_name]["id"] = real_id
        await self._save(data)

    async def update_session_title(
        self, conversation_id: ConversationKey, name: str, title: str
    ) -> None:
        """Update the title of a named session."""
        data = await self._load()
        cid = str(conversation_id)
        sessions = data.get(cid, {}).get("sessions", {})
        if name in sessions:
            sessions[name]["title"] = title
        await self._save(data)

    async def get_chat_setting(
        self, conversation_id: ConversationKey, key: str, default: Any = None
    ) -> Any:
        data = await self._load()
        chat_data = data.get(str(conversation_id), {})
        settings = chat_data.get("settings", {})
        return settings.get(key, default)

    async def set_chat_setting(
        self, conversation_id: ConversationKey, key: str, value: object
    ) -> None:
        data = await self._load()
        cid = str(conversation_id)
        chat_data = data.setdefault(cid, {})
        settings = chat_data.setdefault("settings", {})
        settings[key] = value
        await self._save(data)

    async def get_all_chat_settings(self, conversation_id: ConversationKey) -> dict[str, Any]:
        data = await self._load()
        settings: dict[str, Any] = data.get(str(conversation_id), {}).get("settings", {})
        return settings

    async def reset_session(self, conversation_id: ConversationKey) -> None:
        """Reset the active session's OpenCode ID and prompt count.

        Used by /new — clears the real session ID so the next prompt
        starts fresh, but preserves the session name and model.
        """
        data = await self._load()
        cid = str(conversation_id)
        chat_data = data.get(cid, {})
        active_name = chat_data.get("active", DEFAULT_SESSION_NAME)
        sessions = chat_data.get("sessions", {})
        if active_name in sessions:
            sessions[active_name]["id"] = None
            sessions[active_name]["prompt_count"] = 0
            await self._save(data)
            logger.info(
                "Session reset",
                extra={
                    "event": "session_reset",
                    "session_name": active_name,
                    "chat_id_masked": mask_conversation_key(conversation_id),
                },
            )
