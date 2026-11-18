"""SettingsService: per-chat configuration, frontend-agnostic (Fase B).

Moves the ``/config`` business logic out of ``handlers/commands.py`` (B8):
defaults, key validation, ``timeout`` integer validation and model-alias
resolution. The handler keeps only argument parsing and message formatting.

``flet-data-layer`` P3 (SET-01) adds boundary validation:
  - ``workdir`` delegates to ``WorkdirService.select`` (single-writer rule);
  - ``provider`` is validated against the model catalog's provider ids, and is
    accepted when the catalog is empty/degraded.

Orthogonalization F3 (the intended change):
  - the ``WorkdirService`` is REQUIRED for the workdir write — there is no
    direct-persistence fallback left, so the single writer cannot be bypassed;
  - failures keep their type: ``InvalidWorkdirError`` carries the
    ``WorkdirFailureKind`` and ``InvalidProviderError`` the rejected provider,
    so the view can render specific copy instead of the timeout message.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core_config import DEFAULT_MODEL, OPENCODE_TIMEOUT, resolve_model
from services.effective_workdir import env_fallback_workdir
from services.session_store import SessionStore
from services.workdir_failures import WorkdirFailure, WorkdirFailureKind

if TYPE_CHECKING:
    from services.conversation import ConversationKey
    from services.model_catalog import OpenCodeModelCatalog
    from services.workdir_service import WorkdirService

SETTINGS_KEYS: tuple[str, ...] = ("model", "timeout", "workdir", "provider")

# 24 hours. ``asyncio.wait_for`` treats a timeout <= 0 as already expired
# (every prompt fails instantly), so the lower bound must be strict. The upper
# bound is a sanity cap: an interactive chat prompt should never legitimately
# need to block for more than a day, and it rejects typos like ``999999``.
_MAX_TIMEOUT_SECONDS = 86400

_DEFAULTS: dict[str, Any] = {
    "model": DEFAULT_MODEL,
    "timeout": OPENCODE_TIMEOUT,
    "provider": "opencode",
}


def _default_value(key: str) -> Any:
    """Resolve a setting default; ``workdir`` stays lazy via the facade."""
    if key == "workdir":
        return str(env_fallback_workdir())
    return _DEFAULTS[key]


class UnknownSettingError(KeyError):
    """Raised when a config key is not part of the supported set."""


class InvalidSettingValueError(ValueError):
    """Raised when a setting value fails validation (e.g. non-numeric timeout)."""


class InvalidWorkdirError(InvalidSettingValueError):
    """Raised when a ``workdir`` value is rejected (F3).

    ``kind`` is the typed failure returned by ``WorkdirService.select``; it is
    ``None`` only when no ``WorkdirService`` was injected (a wiring error). The
    view maps it to the specific copy, never to the timeout message.
    """

    def __init__(
        self,
        kind: WorkdirFailureKind | None,
        path: Path | None = None,
        detail: str | None = None,
    ) -> None:
        self.kind = kind
        self.path = path
        self.detail = detail
        if detail is not None:
            message = detail
        elif kind is not None:
            message = kind.value
        else:
            message = "WorkdirService is required to set the workdir"
        super().__init__(message)


class InvalidProviderError(InvalidSettingValueError):
    """Raised when a ``provider`` value is not offered by the catalog (F3)."""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        super().__init__(provider)


@dataclass(frozen=True)
class ChatSettings:
    """Fully resolved per-chat configuration (defaults applied)."""

    model: str
    timeout: int | str
    workdir: str
    provider: str


class SettingsService:
    """Read, validate and persist per-chat settings."""

    def __init__(
        self,
        session_store: SessionStore,
        *,
        workdir_service: WorkdirService | None = None,
        model_catalog: OpenCodeModelCatalog | None = None,
    ) -> None:
        self._store = session_store
        self._workdir_service = workdir_service
        self._model_catalog = model_catalog

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
            workdir=raw.get("workdir", str(env_fallback_workdir())),
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
        return await self._store.get_chat_setting(conversation_id, key, _default_value(key))

    async def set(self, conversation_id: ConversationKey, key: str, raw_value: str) -> str | int:
        """Validate and persist one setting, returning the stored value.

        ``model`` values go through alias resolution; ``timeout`` must be an
        integer number of seconds in ``(0, 86400]``; ``workdir`` is delegated to
        ``WorkdirService.select``; ``provider`` is validated against the model
        catalog (tolerated when degraded).

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

        if key == "workdir":
            return await self._set_workdir(conversation_id, raw_value)

        if key == "provider":
            return await self._set_provider(conversation_id, raw_value)

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

        await self._store.set_chat_setting(conversation_id, key, value)
        return value

    async def _set_workdir(self, conversation_id: ConversationKey, raw_value: str) -> str:
        """Persist via ``WorkdirService.select`` — the ONLY writer (F3, WD-02).

        The service is required for this write: the settings layer never persists
        ``workdir`` directly. When it is missing the call fails with a typed
        error instead of silently bypassing the single writer.
        """
        if self._workdir_service is None:
            raise InvalidWorkdirError(None)

        result = await self._workdir_service.select(conversation_id, Path(raw_value))
        if isinstance(result, WorkdirFailure):
            raise InvalidWorkdirError(result.kind, result.path, result.detail)
        return str(result.path)

    async def _set_provider(self, conversation_id: ConversationKey, raw_value: str) -> str:
        """Validate against the catalog's provider ids; tolerate degradation."""
        if self._model_catalog is not None:
            snapshot = await self._model_catalog.list(conversation_id)
            providers = {option.id.partition("/")[0] for option in snapshot.options}
            if not snapshot.degraded and providers and raw_value not in providers:
                raise InvalidProviderError(raw_value)

        await self._store.set_chat_setting(conversation_id, "provider", raw_value)
        return raw_value

    @staticmethod
    def _ensure_known(key: str) -> None:
        if key not in SETTINGS_KEYS:
            raise UnknownSettingError(key)
