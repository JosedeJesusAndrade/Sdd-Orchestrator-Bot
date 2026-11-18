"""Tests for the model catalog (MD-01..MD-05).

Covers the fallback chain (HTTP -> CLI -> aliases -> empty), the strict HTTP
filter, provider grouping, the per-key ``is_default``, the 300 s TTL, the
single-flight lock and the "never raises" contract. No live server or process:
``httpx.MockTransport`` for HTTP and a monkeypatched ``subprocess.run`` for the
CLI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from typing import Any

import httpx
import pytest

from core_config import DEFAULT_MODEL, MODEL_ALIASES
from services.conversation import ConversationKey
from services.model_catalog import (
    AliasModelsSource,
    CliModelsSource,
    HttpProvidersSource,
    ModelCatalogSnapshot,
    ModelOption,
    OpenCodeModelCatalog,
    ProvidersClient,
)
from services.opencode_server.client import OpenCodeServerClient
from tests.conversation_keys import conv

# ── Fakes ──────────────────────────────────────────────────────────────


class FakeStore:
    """Model-preference store double; optionally raises on read."""

    def __init__(self, *, model: str = DEFAULT_MODEL, error: Exception | None = None) -> None:
        self.model = model
        self.error = error
        self.calls: list[ConversationKey] = []
        self.set_calls: list[tuple[ConversationKey, str]] = []

    async def get_model(self, conversation_id: ConversationKey) -> str:
        self.calls.append(conversation_id)
        if self.error is not None:
            raise self.error
        return self.model

    async def set_model(self, conversation_id: ConversationKey, model: str) -> None:
        self.set_calls.append((conversation_id, model))


class KeyedStore:
    """Per-key model store (proves ``is_default`` is not cached)."""

    def __init__(self, models: dict[str, str]) -> None:
        self._models = models
        self.calls = 0

    async def get_model(self, conversation_id: ConversationKey) -> str:
        self.calls += 1
        return self._models[str(conversation_id)]


class FakeSource:
    """Source double: counts calls, can delay/raise."""

    def __init__(
        self,
        name: str,
        options: list[ModelOption] | None = None,
        *,
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.name = name
        self._options = options or []
        self._error = error
        self._delay = delay
        self.calls = 0

    async def fetch(self) -> list[ModelOption]:
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return list(self._options)


class FakeProvidersClient:
    """Providers transport double: implements the ``ProvidersClient`` port."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.calls = 0

    async def list_providers(self) -> dict[str, Any]:
        self.calls += 1
        return self._payload


class FakeClock:
    """Monotonic clock double for the TTL test."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


# ── Helpers ────────────────────────────────────────────────────────────


def _opt(full_id: str, *, provider: str | None = None, label: str | None = None) -> ModelOption:
    provider_id, _, model_id = full_id.partition("/")
    return ModelOption(id=full_id, provider=provider or provider_id, label=label or model_id)


def _model(
    model_id: str,
    provider_id: str,
    *,
    name: str | None = None,
    status: str = "active",
    text: bool = True,
) -> dict[str, Any]:
    return {
        "id": model_id,
        "providerID": provider_id,
        "name": name or model_id,
        "status": status,
        "capabilities": {"output": {"text": text}},
    }


def _client(handler: Any) -> OpenCodeServerClient:
    return OpenCodeServerClient(
        "http://127.0.0.1:4096",
        "secret",
        r"C:\project\demo",
        transport=httpx.MockTransport(handler),
    )


def _default_ids(snapshot: ModelCatalogSnapshot) -> set[str]:
    return {option.id for option in snapshot.options if option.is_default}


def _completed(
    *, returncode: int, stdout: str, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["opencode", "models", "--verbose"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


# ── HTTP source / happy path ───────────────────────────────────────────


async def test_http_happy_path_filters_groups_and_marks_default() -> None:
    payload = {
        "providers": [
            {
                "id": "deepseek",
                "name": "DeepSeek",
                "models": {
                    "deepseek-flash": _model("deepseek-flash", "deepseek", name="DeepSeek Flash"),
                    "legacy": _model("legacy", "deepseek", status="deprecated"),
                    "vision": _model("vision", "deepseek", text=False),
                },
            },
            {
                "id": "minimax",
                "name": "MiniMax",
                "models": {"MiniMax-M3": _model("MiniMax-M3", "minimax")},
            },
            {
                "id": "nameless",
                "models": {"model-a": _model("model-a", "nameless")},
            },
            {
                "id": "all-dropped",
                "name": "Dropped",
                "models": {"gone": _model("gone", "all-dropped", status="disabled")},
            },
        ],
        "default": {"deepseek": "deepseek-flash"},
    }
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json=payload)

    client = _client(handler)
    try:
        catalog = OpenCodeModelCatalog(
            [HttpProvidersSource(client)],
            FakeStore(model="deepseek/deepseek-flash"),
        )
        snapshot = await catalog.list(conv(1))
    finally:
        await client.close()

    assert paths == ["/config/providers"]
    assert snapshot.source == "http"
    assert snapshot.degraded is False
    assert [option.id for option in snapshot.options] == [
        "deepseek/deepseek-flash",
        "minimax/MiniMax-M3",
        "nameless/model-a",
    ]
    # Provider display name, with fallback to the id for "nameless".
    assert [group.provider for group in snapshot.groups] == ["DeepSeek", "MiniMax", "nameless"]
    assert [option.label for option in snapshot.options] == [
        "DeepSeek Flash",
        "MiniMax-M3",
        "model-a",
    ]
    # Union of groups equals options; every option appears exactly once.
    grouped = tuple(option for group in snapshot.groups for option in group.options)
    assert grouped == snapshot.options
    # Default comes from the conversation's model, not the API's per-provider map.
    assert _default_ids(snapshot) == {"deepseek/deepseek-flash"}
    # fetched_at is tz-aware UTC.
    assert snapshot.fetched_at.tzinfo is not None
    offset = snapshot.fetched_at.utcoffset()
    assert offset is not None
    assert offset.total_seconds() == 0


async def test_http_source_depends_only_on_the_providers_client_port() -> None:
    payload = {
        "providers": [
            {
                "id": "deepseek",
                "name": "DeepSeek",
                "models": {"deepseek-flash": _model("deepseek-flash", "deepseek")},
            }
        ]
    }
    fake = FakeProvidersClient(payload)

    options = await HttpProvidersSource(fake).fetch()

    assert fake.calls == 1
    assert [option.id for option in options] == ["deepseek/deepseek-flash"]


async def test_real_client_conforms_to_the_providers_client_port() -> None:
    client = _client(lambda _request: httpx.Response(200, json={"providers": []}))
    try:
        port: ProvidersClient = client
        assert callable(port.list_providers)
    finally:
        await client.close()


# ── Fallback chain ─────────────────────────────────────────────────────


async def test_http_malformed_json_falls_through_to_cli() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json at all")

    client = _client(handler)
    cli = FakeSource("cli", [_opt("deepseek/deepseek-flash")])
    try:
        catalog = OpenCodeModelCatalog(
            [HttpProvidersSource(client), cli],
            FakeStore(model="deepseek/deepseek-flash"),
        )
        snapshot = await catalog.list(conv(1))
    finally:
        await client.close()

    assert snapshot.source == "cli"
    assert snapshot.degraded is True
    assert [option.id for option in snapshot.options] == ["deepseek/deepseek-flash"]
    assert cli.calls == 1


async def test_http_error_status_falls_through_to_cli() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request")

    client = _client(handler)
    cli = FakeSource("cli", [_opt("minimax/MiniMax-M3")])
    try:
        catalog = OpenCodeModelCatalog([HttpProvidersSource(client), cli], FakeStore())
        snapshot = await catalog.list(conv(1))
    finally:
        await client.close()

    assert snapshot.source == "cli"
    assert snapshot.degraded is True


async def test_cli_failure_falls_through_to_aliases() -> None:
    http = FakeSource("http", error=RuntimeError("http down"))
    cli = FakeSource("cli", error=RuntimeError("cli down"))
    catalog = OpenCodeModelCatalog(
        [http, cli, AliasModelsSource()],
        FakeStore(model=DEFAULT_MODEL),
    )

    snapshot = await catalog.list(conv(1))

    assert snapshot.source == "aliases"
    assert snapshot.degraded is True
    canonical = {value for aliases in MODEL_ALIASES.values() for value in aliases.values()}
    # Canonical ids only, de-duplicated (pro and deepseek-v4-pro collapse).
    assert {option.id for option in snapshot.options} == canonical
    assert len(snapshot.options) == len(canonical)
    assert all(option.id.count("/") == 1 for option in snapshot.options)
    assert _default_ids(snapshot) == {DEFAULT_MODEL}


async def test_all_sources_failing_returns_empty_snapshot_without_raising() -> None:
    catalog = OpenCodeModelCatalog(
        [
            FakeSource("http", error=RuntimeError("http down")),
            FakeSource("cli", error=RuntimeError("cli down")),
            FakeSource("aliases", error=RuntimeError("aliases down")),
        ],
        FakeStore(),
    )

    snapshot = await catalog.list(conv(1))

    assert snapshot.options == ()
    assert snapshot.groups == ()
    assert snapshot.source == "empty"
    assert snapshot.degraded is True
    assert snapshot.fetched_at.tzinfo is not None


# ── is_default / dangling model ────────────────────────────────────────


async def test_missing_active_model_defaults_to_first_without_persisting(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FakeStore(model="ghost/does-not-exist")
    source = FakeSource(
        "http",
        [_opt("deepseek/deepseek-flash"), _opt("deepseek/deepseek-v4-pro")],
    )
    catalog = OpenCodeModelCatalog([source], store)

    with caplog.at_level(logging.WARNING, logger="opencode_bot.services.model_catalog"):
        snapshot = await catalog.list(conv(1))

    assert snapshot.options[0].is_default is True
    assert snapshot.options[1].is_default is False
    events = [getattr(record, "event", "") for record in caplog.records]
    assert events.count("model_default_missing") == 1
    # Nothing was persisted: the store has no write path called.
    assert store.set_calls == []


async def test_store_read_failure_does_not_raise() -> None:
    source = FakeSource("http", [_opt(DEFAULT_MODEL)])
    catalog = OpenCodeModelCatalog([source], FakeStore(error=RuntimeError("boom")))

    snapshot = await catalog.list(conv(1))

    assert [option.id for option in snapshot.options] == [DEFAULT_MODEL]
    assert _default_ids(snapshot) == {DEFAULT_MODEL}


# ── TTL + single-flight ────────────────────────────────────────────────


async def test_ten_concurrent_lists_share_a_single_fetch() -> None:
    source = FakeSource("http", [_opt("deepseek/deepseek-flash")], delay=0.01)
    catalog = OpenCodeModelCatalog([source], FakeStore(model="deepseek/deepseek-flash"))

    snapshots = await asyncio.gather(*(catalog.list(conv(1)) for _ in range(10)))

    assert source.calls == 1
    assert len(snapshots) == 10
    assert all(snapshot.options for snapshot in snapshots)


async def test_ttl_expiry_triggers_exactly_one_new_fetch() -> None:
    clock = FakeClock()
    source = FakeSource("http", [_opt("deepseek/deepseek-flash")])
    catalog = OpenCodeModelCatalog([source], FakeStore(), ttl=300, clock=clock)

    await catalog.list(conv(1))
    await catalog.list(conv(1))
    assert source.calls == 1

    clock.now = 299.0
    await catalog.list(conv(1))
    assert source.calls == 1

    clock.now = 300.0
    snapshots = await asyncio.gather(*(catalog.list(conv(1)) for _ in range(5)))
    assert source.calls == 2
    assert len(snapshots) == 5


async def test_is_default_is_per_key_on_one_cached_fetch() -> None:
    source = FakeSource(
        "http",
        [_opt("deepseek/deepseek-flash"), _opt("deepseek/deepseek-v4-pro")],
    )
    store = KeyedStore(
        {
            "a": "deepseek/deepseek-flash",
            "b": "deepseek/deepseek-v4-pro",
        }
    )
    catalog = OpenCodeModelCatalog([source], store)

    first = await catalog.list(conv("a"))
    second = await catalog.list(conv("b"))

    assert source.calls == 1
    assert store.calls == 2
    assert _default_ids(first) == {"deepseek/deepseek-flash"}
    assert _default_ids(second) == {"deepseek/deepseek-v4-pro"}


# ── CLI source ─────────────────────────────────────────────────────────


async def test_cli_source_parses_verbose_records_and_applies_best_effort_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = "\n".join(
        [
            "deepseek/deepseek-flash",
            json.dumps(
                {
                    "id": "deepseek-flash",
                    "providerID": "deepseek",
                    "name": "DeepSeek Flash",
                    "capabilities": {"output": {"text": True}},
                }
            ),
            json.dumps({"id": "legacy", "providerID": "deepseek", "status": "deprecated"}),
            json.dumps(
                {
                    "id": "vision",
                    "providerID": "deepseek",
                    "capabilities": {"output": {"text": False}},
                }
            ),
            json.dumps({"id": "MiniMax-M3", "providerID": "minimax", "name": "MiniMax M3"}),
        ]
    )
    captured: dict[str, Any] = {}

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _completed(returncode=0, stdout=stdout)

    monkeypatch.setattr("services.model_catalog.subprocess.run", fake_run)

    options = await CliModelsSource("opencode").fetch()

    assert captured["args"] == ["opencode", "models", "--verbose"]
    assert captured["kwargs"]["check"] is False
    assert [option.id for option in options] == [
        "deepseek/deepseek-flash",
        "minimax/MiniMax-M3",
    ]
    assert [option.label for option in options] == ["DeepSeek Flash", "MiniMax M3"]


async def test_cli_source_parses_plain_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = "deepseek/deepseek-flash\nminimax/MiniMax-M3\nnot-a-model\n"

    def fake_run(_args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return _completed(returncode=0, stdout=stdout)

    monkeypatch.setattr("services.model_catalog.subprocess.run", fake_run)

    options = await CliModelsSource("opencode").fetch()

    assert [option.id for option in options] == [
        "deepseek/deepseek-flash",
        "minimax/MiniMax-M3",
    ]


async def test_cli_source_raises_on_non_zero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(_args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return _completed(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr("services.model_catalog.subprocess.run", fake_run)

    with pytest.raises(RuntimeError):
        await CliModelsSource("opencode").fetch()


# ── Aliases source ─────────────────────────────────────────────────────


async def test_alias_source_exposes_canonical_ids_not_alias_keys() -> None:
    options = await AliasModelsSource().fetch()

    ids = {option.id for option in options}
    canonical = {value for aliases in MODEL_ALIASES.values() for value in aliases.values()}
    assert ids == canonical
    assert "pro" not in ids
    assert "flash" not in ids
    assert all(option.provider == option.id.partition("/")[0] for option in options)
