"""SettingsService: per-chat configuration, frontend-agnostic (Fase B).

Moves the ``/config`` business logic out of ``handlers/commands.py`` (B8):
defaults, key validation, ``timeout`` integer validation and model-alias
resolution. The handler keeps only argument parsing and message formatting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from core_config import DEFAULT_MODEL, OPENCODE_TIMEOUT, resolve_model
from services.effective_workdir import ENV_FALLBACK_WORKDIR
from services.session_store import SessionStore

if TYPE_CHECKING:
    from services.conversation import ConversationKey

SETTINGS_KEYS: tuple[str, ...] = ("model", "timeout", "workdir", "provider")

# 24 hours. ``asyncio.wait_for`` treats a timeout <= 0 as already expired
# (every prompt fails instantly), so the lower bound must be strict. The upper
# bound is a sanity cap: an interactive chat prompt should never legitimately
# need to block for more than a day, and it rejects typos like ``999999``.
_MAX_TIMEOUT_SECONDS = 86400

_DEFAULTS: dict[str, Any] = {
    "model": DEFAULT_MODEL,
    "timeout": OPENCODE_TIMEOUT,
    "workdir": str(ENV_FALLBACK_WORKDIR),
    "provider": "opencode",
}


class UnknownSettingError(KeyError):
    """Raised when a config key is not part of the supported set."""


class InvalidSettingValueError(ValueError):
    """Raised when a setting value fails validation (e.g. non-numeric timeout)."""


@dataclass(frozen=True)
class ChatSettings:
    """Fully resolved per-chat configuration (defaults applied)."""

    model: str
    timeout: int | str
    workdir: str
    provider: str


class SettingsService:
    """Read, validate and persist per-chat settings."""

    def __init__(self, session_store: SessionStore) -> None:
        self._store = session_store

    async def get(self, conversation_id: ConversationKey) -> ChatSettings:
        """Return the effective settings with defaults applied.

        ``model`` is read from the SAME source ``PromptService`` uses
        (``SessionStore.get_model``), so ``/config model`` and ``/model``
        agree on a single effective value.
        """
        raw = await self._store.get_all_chat_settings(conversation_id)
        return ChatSettings(
            model=await self._store.get_model(conversation_id),
            timeout=raw.get("timeout", OPENCODE_TIMEOUT),
            workdir=raw.get("workdir", str(ENV_FALLBACK_WORKDIR)),
            provider=raw.get("provider", "opencode"),
        )

    async def get_value(self, conversation_id: ConversationKey, key: str) -> Any:
        """Return one setting with its default applied.

        Raises:
            UnknownSettingError: if ``key`` is not supported.
        """
        self._ensure_known(key)
        if key == "model":
            return await self._store.get_model(conversation_id)
        return await self._store.get_chat_setting(conversation_id, key, _DEFAULTS[key])

    async def set(self, conversation_id: ConversationKey, key: str, raw_value: str) -> str | int:
        """Validate and persist one setting, returning the stored value.

        ``model`` values go through alias resolution; ``timeout`` must be an
        integer number of seconds in ``(0, 86400]``.

        Raises:
            UnknownSettingError: if ``key`` is not supported.
            InvalidSettingValueError: if the value is not valid for ``key``.
        """
        self._ensure_known(key)

        # ``model`` has a dedicated accessor shared with ``/model`` and
        # ``PromptService``. Writing it through ``set_chat_setting`` would
        # update the inert ``settings.model`` mirror while prompts keep
        # reading the top-level key, so ``/config model`` had no effect.
        if key == "model":
            model = resolve_model(raw_value)
            await self._store.set_model(conversation_id, model)
            return model

        value: str | int
        if key == "timeout":
            try:
                value = int(raw_value)
            except ValueError as e:
                raise InvalidSettingValueError(raw_value) from e
            if value <= 0:
                raise InvalidSettingValueError("El timeout debe ser mayor que 0 segundos.")
            if value > _MAX_TIMEOUT_SECONDS:
                raise InvalidSettingValueError(
                    f"El timeout no puede superar los {_MAX_TIMEOUT_SECONDS} segundos (24 horas)."
                )
        else:
            value = raw_value

        await self._store.set_chat_setting(conversation_id, key, value)
        return value

    @staticmethod
    def _ensure_known(key: str) -> None:
        if key not in SETTINGS_KEYS:
            raise UnknownSettingError(key)
