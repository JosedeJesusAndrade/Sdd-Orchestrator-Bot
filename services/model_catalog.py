"""Model catalog for the selector: HTTP -> CLI -> aliases fallback.

The catalog answers one question for a conversation: *which models can the
agent run, grouped by provider, and which one is active?* It never raises and
never merges sources: the first source that yields a non-empty list wins.

Sources, in order (MD-02):
  1. :class:`HttpProvidersSource` — ``GET /config/providers`` (fast, complete,
     provider- and model-filtered). Only providers the agent can actually
     execute are kept (env-derived API-key entries and credential-less ghosts
     are dropped); within them, models need ``status == "active"`` AND
     ``capabilities.output.text``.
  2. :class:`CliModelsSource` — ``opencode models --verbose`` off the event
     loop via ``asyncio.to_thread``. The strict filter is best-effort here: it
     is applied only when the metadata parses (Flag #7 — the CLI has no
     per-provider ``status`` guarantee).
  3. :class:`AliasModelsSource` — the canonical ids of ``MODEL_ALIASES``. Alias
     keys are never exposed as options (MD-03).
  4. Empty snapshot.

Fetched options are cached for :data:`DEFAULT_TTL_SECONDS` (300 s) and
concurrent misses share ONE fetch through an ``asyncio.Lock`` (MD-04). The
``is_default`` flag is recomputed on every ``list()`` from the conversation's
effective model — it is per-key, not per-fetch.

This module is core: it is frontend-free and imports nothing from
``telegram``/``flet``/``frontends``.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, cast

from core_config import DEFAULT_MODEL, MODEL_ALIASES, OPENCODE_CMD, resolve_model
from utils.logging import get_module_logger, log_exception

if TYPE_CHECKING:
    from services.conversation import ConversationKey

logger = get_module_logger(__name__)

#: How long a fetched option set stays fresh, in seconds.
DEFAULT_TTL_SECONDS = 300.0

#: Source labels echoed in :attr:`ModelCatalogSnapshot.source`.
SOURCE_HTTP = "http"
SOURCE_CLI = "cli"
SOURCE_ALIASES = "aliases"
SOURCE_EMPTY = "empty"


# ── DTOs ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ModelOption:
    """One selectable model.

    ``id`` is always the prompt wire format ``providerID/modelID``; ``provider``
    is the provider's display name (fallback: its id) and ``label`` the model's
    display name (fallback: its id).
    """

    id: str
    provider: str
    label: str
    is_default: bool = False


@dataclass(frozen=True)
class ModelGroup:
    """Models of one provider, in provider order."""

    provider: str
    label: str
    options: tuple[ModelOption, ...]


@dataclass(frozen=True)
class ModelCatalogSnapshot:
    """An immutable view of the catalog for one conversation."""

    options: tuple[ModelOption, ...]
    groups: tuple[ModelGroup, ...]
    source: str
    degraded: bool
    fetched_at: datetime


class ProvidersClient(Protocol):
    """The transport the HTTP source needs: the raw provider catalog payload.

    ``OpenCodeServerClient`` conforms structurally, so :class:`HttpProvidersSource`
    never couples to the concrete HTTP client. The selector's public contract
    lives in ``flet_app/data/model_selector`` (``list``), not here.
    """

    async def list_providers(self) -> dict[str, Any]: ...


class ModelSource(Protocol):
    """A fallback source in the chain; ``name`` is its snapshot label."""

    name: str

    async def fetch(self) -> list[ModelOption]: ...


class ModelPreferenceStore(Protocol):
    """The only state the catalog reads (never writes)."""

    async def get_model(self, conversation_id: ConversationKey) -> str: ...


# ── Sources ────────────────────────────────────────────────────────────


class HttpProvidersSource:
    """Primary source: ``GET /config/providers`` (strict filter)."""

    name = SOURCE_HTTP

    def __init__(self, client: ProvidersClient) -> None:
        self._client = client

    async def fetch(self) -> list[ModelOption]:
        payload = await self._client.list_providers()
        return _options_from_providers(payload)


class CliModelsSource:
    """Fallback: ``opencode models --verbose`` run off the event loop."""

    name = SOURCE_CLI

    def __init__(self, command: str = OPENCODE_CMD, *, timeout: float = 30.0) -> None:
        self._command = command
        self._timeout = timeout

    async def fetch(self) -> list[ModelOption]:
        stdout = await asyncio.to_thread(self._run)
        return _options_from_cli(stdout)

    def _run(self) -> str:
        result = subprocess.run(
            [self._command, "models", "--verbose"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self._timeout,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"opencode models exited with {result.returncode}")
        return result.stdout


class AliasModelsSource:
    """Last-resort static source: the canonical ids of ``MODEL_ALIASES``.

    Alias *keys* (``pro``, ``flash``, ...) are never options; only the
    ``provider/model`` values are exposed (MD-03). The mapping is read, never
    mutated.
    """

    name = SOURCE_ALIASES

    def __init__(self, aliases: Mapping[str, Mapping[str, str]] | None = None) -> None:
        self._aliases = MODEL_ALIASES if aliases is None else aliases

    async def fetch(self) -> list[ModelOption]:
        options: list[ModelOption] = []
        seen: set[str] = set()
        for aliases in self._aliases.values():
            for canonical in aliases.values():
                if canonical in seen:
                    continue
                provider_id, separator, model_id = canonical.partition("/")
                if not separator or not provider_id or not model_id:
                    continue
                seen.add(canonical)
                options.append(ModelOption(id=canonical, provider=provider_id, label=model_id))
        return options


class SkipSource:
    """A no-op placeholder that preserves the source order (e.g. no client)."""

    name = "skip"

    async def fetch(self) -> list[ModelOption]:
        return []


# ── Catalog ────────────────────────────────────────────────────────────


class OpenCodeModelCatalog:
    """Fallback chain + TTL cache + single-flight for the model selector."""

    def __init__(
        self,
        sources: Sequence[ModelSource],
        store: ModelPreferenceStore,
        *,
        ttl: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sources = tuple(sources)
        self._store = store
        self._ttl = ttl
        self._clock = clock
        self._lock = asyncio.Lock()
        self._options: tuple[ModelOption, ...] | None = None
        self._source = SOURCE_EMPTY
        self._cached_at = 0.0
        self._fetched_at = datetime.now(UTC)

    async def list(self, key: ConversationKey) -> ModelCatalogSnapshot:
        """Return the catalog for ``key`` (never raises)."""
        options, source, fetched_at = await self._resolve_options()
        return await self._snapshot(key, options, source, fetched_at)

    async def _resolve_options(self) -> tuple[tuple[ModelOption, ...], str, datetime]:
        cached = self._fresh_options()
        if cached is not None:
            return cached

        # Single-flight: everyone that missed the cache waits here, and only
        # the first one fetches; the rest re-check and reuse the fresh value.
        async with self._lock:
            cached = self._fresh_options()
            if cached is not None:
                return cached

            options, source = await self._fetch_chain()
            self._options = options
            self._source = source
            self._cached_at = self._clock()
            self._fetched_at = datetime.now(UTC)
            return options, source, self._fetched_at

    def _fresh_options(self) -> tuple[tuple[ModelOption, ...], str, datetime] | None:
        if self._options is None:
            return None
        if self._clock() - self._cached_at >= self._ttl:
            return None
        return self._options, self._source, self._fetched_at

    async def _fetch_chain(self) -> tuple[tuple[ModelOption, ...], str]:
        """First non-empty source wins; every failure falls through."""
        for source in self._sources:
            try:
                fetched = await source.fetch()
            except Exception:
                log_exception(
                    "model_catalog_source_failed",
                    module=__name__,
                    source=source.name,
                )
                continue
            options = _dedupe(fetched)
            if options:
                return options, source.name
        return (), SOURCE_EMPTY

    async def _snapshot(
        self,
        key: ConversationKey,
        options: tuple[ModelOption, ...],
        source: str,
        fetched_at: datetime,
    ) -> ModelCatalogSnapshot:
        if not options:
            return ModelCatalogSnapshot(
                options=(),
                groups=(),
                source=SOURCE_EMPTY,
                degraded=True,
                fetched_at=fetched_at,
            )

        effective = await self._effective_model(key)
        known = {option.id for option in options}
        if effective in known:
            default_id = effective
        else:
            default_id = options[0].id
            # Exactly one log per snapshot; the stored model is NOT changed.
            logger.warning(
                "Effective model is absent from the catalog; using the first option",
                extra={
                    "event": "model_default_missing",
                    "effective_model": effective,
                    "fallback_model": default_id,
                },
            )

        marked = tuple(replace(option, is_default=option.id == default_id) for option in options)
        return ModelCatalogSnapshot(
            options=marked,
            groups=_group(marked),
            source=source,
            degraded=source != SOURCE_HTTP,
            fetched_at=fetched_at,
        )

    async def _effective_model(self, key: ConversationKey) -> str:
        try:
            stored = await self._store.get_model(key)
        except Exception:
            log_exception("model_catalog_get_model_failed", module=__name__)
            return DEFAULT_MODEL
        return resolve_model(stored or DEFAULT_MODEL)


# ── Parsing / helpers ──────────────────────────────────────────────────


def _options_from_providers(payload: Mapping[str, Any]) -> list[ModelOption]:
    providers = payload.get("providers")
    if not isinstance(providers, list):
        return []

    options: list[ModelOption] = []
    seen: set[str] = set()
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        provider_data = cast(dict[str, Any], provider)
        if not _provider_agent_executable(provider_data):
            continue
        options.extend(_options_from_provider(provider_data, seen))
    return options


def _provider_agent_executable(provider: Mapping[str, Any]) -> bool:
    """Whether the agent can actually route prompts to this provider.

    ``/config/providers`` lists more than what the agent can execute: it adds
    env-derived providers (``source == "env"``) merely because an API-key env
    var exists — often a placeholder such as ``your_openai_key_here`` — and it
    keeps configured providers with no usable credential. Showing those models
    is a lie the selector must not tell.

    A provider is excluded when it is env-derived, or when it declares an
    explicitly empty ``options.apiKey`` and carries no ``key``. Absent
    credential metadata is treated as executable (fail-open) so a minimal
    catalog payload is never over-filtered.
    """
    if provider.get("source") == "env":
        return False
    key = provider.get("key")
    has_key = isinstance(key, str) and bool(key)
    options = provider.get("options")
    if isinstance(options, dict):
        api_key = options.get("apiKey")
        if isinstance(api_key, str) and not api_key and not has_key:
            return False
    return True


def _options_from_provider(provider: Mapping[str, Any], seen: set[str]) -> list[ModelOption]:
    provider_id = _non_empty_str(provider.get("id"))
    models = provider.get("models")
    if provider_id is None or not isinstance(models, dict):
        return []

    display = _non_empty_str(provider.get("name")) or provider_id
    options: list[ModelOption] = []
    for model_key, model in models.items():
        if not isinstance(model_key, str) or not model_key or not isinstance(model, dict):
            continue
        model_data = cast(dict[str, Any], model)
        if not _http_model_selectable(model_data):
            continue
        model_id = _non_empty_str(model_data.get("id")) or model_key
        model_provider = _non_empty_str(model_data.get("providerID")) or provider_id
        full_id = f"{model_provider}/{model_id}"
        if full_id in seen:
            continue
        seen.add(full_id)
        label = _non_empty_str(model_data.get("name")) or model_id
        options.append(ModelOption(id=full_id, provider=display, label=label))
    return options


def _http_model_selectable(model: Mapping[str, Any]) -> bool:
    """Strict HTTP filter: active AND text output (MD-03)."""
    if model.get("status") != "active":
        return False
    capabilities = model.get("capabilities")
    if not isinstance(capabilities, dict):
        return False
    output = capabilities.get("output")
    if not isinstance(output, dict):
        return False
    return output.get("text") is True


def _options_from_cli(stdout: str) -> list[ModelOption]:
    records = _decode_json_records(stdout)
    if records:
        return _options_from_cli_records(records)
    return _options_from_cli_lines(stdout)


def _decode_json_records(stdout: str) -> list[dict[str, Any]]:
    """Pull every top-level JSON object out of ``--verbose`` output."""
    decoder = json.JSONDecoder()
    records: list[dict[str, Any]] = []
    index = 0
    length = len(stdout)
    while index < length:
        if stdout[index] != "{":
            index += 1
            continue
        try:
            raw, end = decoder.raw_decode(stdout, index)
        except json.JSONDecodeError:
            index += 1
            continue
        if isinstance(raw, dict):
            records.append(cast(dict[str, Any], raw))
        index = end
    return records


def _options_from_cli_records(records: Sequence[Mapping[str, Any]]) -> list[ModelOption]:
    options: list[ModelOption] = []
    seen: set[str] = set()
    for record in records:
        model_id = _non_empty_str(record.get("id"))
        provider_id = _non_empty_str(record.get("providerID")) or _non_empty_str(
            record.get("providerId")
        )
        if model_id is None or provider_id is None or not _cli_model_selectable(record):
            continue
        full_id = f"{provider_id}/{model_id}"
        if full_id in seen:
            continue
        seen.add(full_id)
        label = _non_empty_str(record.get("name")) or model_id
        options.append(ModelOption(id=full_id, provider=provider_id, label=label))
    return options


def _cli_model_selectable(record: Mapping[str, Any]) -> bool:
    """Best-effort filter: only exclude when metadata explicitly says no.

    The CLI does not guarantee ``status``; when it is absent the model stays
    selectable. ``capabilities.output.text is False`` is a definitive "no".
    """
    status = record.get("status")
    if isinstance(status, str) and status != "active":
        return False
    capabilities = record.get("capabilities")
    if isinstance(capabilities, dict):
        output = capabilities.get("output")
        if isinstance(output, dict) and output.get("text") is False:
            return False
    return True


def _options_from_cli_lines(stdout: str) -> list[ModelOption]:
    """Plain ``provider/id`` lines (non-verbose output)."""
    options: list[ModelOption] = []
    seen: set[str] = set()
    for line in stdout.splitlines():
        candidate = line.strip()
        if not candidate or " " in candidate or "\t" in candidate:
            continue
        provider_id, separator, model_id = candidate.partition("/")
        if not separator or not provider_id or not model_id or candidate in seen:
            continue
        seen.add(candidate)
        options.append(ModelOption(id=candidate, provider=provider_id, label=model_id))
    return options


def _dedupe(options: Sequence[ModelOption]) -> tuple[ModelOption, ...]:
    """De-duplicate by id, first wins (MD-03)."""
    seen: set[str] = set()
    result: list[ModelOption] = []
    for option in options:
        if option.id in seen:
            continue
        seen.add(option.id)
        result.append(option)
    return tuple(result)


def _group(options: Sequence[ModelOption]) -> tuple[ModelGroup, ...]:
    """Group by provider display name, preserving first-seen order."""
    order: list[str] = []
    buckets: dict[str, list[ModelOption]] = {}
    for option in options:
        if option.provider not in buckets:
            buckets[option.provider] = []
            order.append(option.provider)
        buckets[option.provider].append(option)
    return tuple(
        ModelGroup(provider=name, label=name, options=tuple(buckets[name])) for name in order
    )


def _non_empty_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
