"""Unit tests for SettingsService (Fase B).

Covers defaults, persistence, key/value validation (including timeout),
``get_value`` and model-alias resolution. Uses a recording fake for the
unit contract and a real ``SessionStore`` on ``tmp_path`` for the
round-trip persistence check.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from core_config import DEFAULT_MODEL, OPENCODE_TIMEOUT
from services.effective_workdir import env_fallback_workdir
from services.model_catalog import ModelCatalogSnapshot, ModelOption, OpenCodeModelCatalog
from services.session_store import SessionStore
from services.settings_service import (
    ChatSettings,
    InvalidProviderError,
    InvalidSettingValueError,
    InvalidWorkdirError,
    SettingsService,
    UnknownSettingError,
)
from services.workdir_failures import WorkdirFailureKind
from services.workdir_service import WorkdirService
from tests.conversation_keys import conv


class FakeStore:
    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        self.settings = dict(settings or {})
        self.set_calls: list[tuple[object, str, object]] = []

    async def get_all_chat_settings(self, conversation_id: object) -> dict[str, Any]:
        return dict(self.settings)

    async def get_chat_setting(self, conversation_id: object, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    async def set_chat_setting(self, conversation_id: object, key: str, value: object) -> None:
        self.settings[key] = value
        self.set_calls.append((conversation_id, key, value))

    async def get_model(self, conversation_id: object) -> str:
        return cast(str, self.settings.get("model", DEFAULT_MODEL))

    async def set_model(self, conversation_id: object, model: str) -> None:
        self.settings["model"] = model


def _service(store: FakeStore | SessionStore) -> SettingsService:
    return SettingsService(cast(SessionStore, store))


# ── Defaults ───────────────────────────────────────────────────────────


async def test_get_returns_defaults_when_store_is_empty() -> None:
    service = _service(FakeStore())

    settings = await service.get(conv(42))

    assert settings == ChatSettings(
        model=DEFAULT_MODEL,
        timeout=OPENCODE_TIMEOUT,
        workdir=str(env_fallback_workdir()),
        provider="opencode",
    )


async def test_get_applies_defaults_only_where_values_are_missing() -> None:
    service = _service(FakeStore({"model": "custom/model", "timeout": 45}))

    settings = await service.get(conv(42))

    assert settings.model == "custom/model"
    assert settings.timeout == 45
    assert settings.workdir == str(env_fallback_workdir())
    assert settings.provider == "opencode"


# ── set(): persistence and validation ──────────────────────────────────


async def test_set_persists_and_get_reflects_it(tmp_path: Path) -> None:
    store = FakeStore()
    home = _home(tmp_path)
    project = home / "project"
    project.mkdir()
    service = SettingsService(
        cast(SessionStore, store),
        workdir_service=WorkdirService(cast(Any, store), home=home),
    )

    value = await service.set(conv(42), "workdir", str(project))

    assert value == str(project.resolve())
    assert store.set_calls[0] == (conv(42), "workdir", str(project.resolve()))
    assert [call[:2] for call in store.set_calls] == [
        (conv(42), "workdir"),
        (conv(42), "recent_workdirs"),
    ]
    assert (await service.get(conv(42))).workdir == str(project.resolve())


async def test_set_timeout_coerces_to_int_and_persists() -> None:
    store = FakeStore()
    service = _service(store)

    value = await service.set(conv(42), "timeout", "120")

    assert value == 120
    assert isinstance(value, int)
    assert store.settings["timeout"] == 120


@pytest.mark.parametrize(
    ("raw", "resolved"),
    [
        ("pro", "deepseek/deepseek-v4-pro"),
        ("flash", "deepseek/deepseek-flash"),
        ("custom/model", "custom/model"),
    ],
)
async def test_set_model_resolves_aliases(raw: str, resolved: str) -> None:
    store = FakeStore()
    service = _service(store)

    value = await service.set(conv(42), "model", raw)

    assert value == resolved
    assert store.settings["model"] == resolved
    assert (await service.get(conv(42))).model == resolved


async def test_set_model_updates_the_effective_model_read_by_prompts(tmp_path: Path) -> None:
    """Regression: ``/config model`` must change what ``/model`` and prompts use.

    ``SettingsService`` now writes the SAME top-level key ``SessionStore.get_model``
    exposes (the accessor ``PromptService`` reads), instead of the inert
    ``settings.model`` mirror.
    """
    store = SessionStore(tmp_path / "sessions.json")
    service = _service(store)

    await service.set(conv("telegram:42"), "model", "pro")

    effective = "deepseek/deepseek-v4-pro"
    assert await store.get_model(conv("telegram:42")) == effective
    assert (await service.get(conv("telegram:42"))).model == effective
    assert await service.get_value(conv("telegram:42"), "model") == effective

    persisted = json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))
    assert persisted["telegram:42"]["model"] == effective
    assert "settings" not in persisted["telegram:42"]


@pytest.mark.parametrize("raw", ["abc", "12.5", "", "10s"])
async def test_set_timeout_non_numeric_raises_and_does_not_persist(raw: str) -> None:
    store = FakeStore()
    service = _service(store)

    with pytest.raises(InvalidSettingValueError) as exc:
        await service.set(conv(42), "timeout", raw)

    assert exc.value.args == (raw,)
    assert store.set_calls == []
    assert store.settings == {}


@pytest.mark.parametrize("raw", ["-5", "0"])
async def test_set_timeout_non_positive_raises_and_does_not_persist(raw: str) -> None:
    store = FakeStore()
    service = _service(store)

    with pytest.raises(InvalidSettingValueError) as exc:
        await service.set(conv(42), "timeout", raw)

    assert exc.value.args == ("El timeout debe ser mayor que 0 segundos.",)
    assert store.set_calls == []
    assert store.settings == {}


async def test_set_timeout_above_upper_bound_raises_and_does_not_persist() -> None:
    store = FakeStore()
    service = _service(store)

    with pytest.raises(InvalidSettingValueError) as exc:
        await service.set(conv(42), "timeout", "999999")

    assert exc.value.args == ("El timeout no puede superar los 86400 segundos (24 horas).",)
    assert store.set_calls == []
    assert store.settings == {}


async def test_set_timeout_accepts_valid_values_at_bounds() -> None:
    store = FakeStore()
    service = _service(store)

    assert await service.set(conv(42), "timeout", "300") == 300
    assert await service.set(conv(42), "timeout", "86400") == 86400
    assert store.settings["timeout"] == 86400


async def test_set_unknown_key_raises_and_does_not_write() -> None:
    store = FakeStore()
    service = _service(store)

    with pytest.raises(UnknownSettingError) as exc:
        await service.set(conv(42), "theme", "dark")

    assert exc.value.args == ("theme",)
    assert store.set_calls == []


# ── get_value() ────────────────────────────────────────────────────────


async def test_get_value_returns_default_for_unset_key() -> None:
    service = _service(FakeStore())

    assert await service.get_value(conv(42), "timeout") == OPENCODE_TIMEOUT
    assert await service.get_value(conv(42), "model") == DEFAULT_MODEL


async def test_get_value_returns_stored_value() -> None:
    service = _service(FakeStore({"timeout": 45}))

    assert await service.get_value(conv(42), "timeout") == 45


async def test_get_value_unknown_key_raises() -> None:
    service = _service(FakeStore())

    with pytest.raises(UnknownSettingError) as exc:
        await service.get_value(conv(42), "theme")

    assert exc.value.args == ("theme",)


# ── Persistence round trip through the real store ──────────────────────


async def test_settings_round_trip_through_real_session_store(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.json")
    home = _home(tmp_path)
    project = home / "project"
    project.mkdir()
    service = SettingsService(store, workdir_service=WorkdirService(store, home=home))

    await service.set(conv("flet-user"), "workdir", str(project))

    settings = await service.get(conv("flet-user"))
    assert settings.workdir == str(project.resolve())

    persisted = json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))
    assert persisted["flet-user"]["settings"]["workdir"] == str(project.resolve())


# ── SET-01: workdir delegates to WorkdirService.select ─────────────────


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


async def test_set_workdir_delegates_to_select(tmp_path: Path) -> None:
    store = FakeStore()
    home = _home(tmp_path)
    project = home / "proj"
    project.mkdir()
    service = SettingsService(
        cast(SessionStore, store),
        workdir_service=WorkdirService(cast(Any, store), home=home),
    )

    value = await service.set(conv(42), "workdir", str(project))

    assert value == str(project.resolve())
    assert store.set_calls[0] == (conv(42), "workdir", str(project.resolve()))
    assert [call[:2] for call in store.set_calls] == [
        (conv(42), "workdir"),
        (conv(42), "recent_workdirs"),
    ]


async def test_set_workdir_non_git_is_accepted(tmp_path: Path) -> None:
    store = FakeStore()
    home = _home(tmp_path)
    plain = home / "plain"
    plain.mkdir()
    service = SettingsService(
        cast(SessionStore, store),
        workdir_service=WorkdirService(cast(Any, store), home=home),
    )

    assert await service.set(conv(42), "workdir", str(plain)) == str(plain.resolve())


async def test_set_workdir_without_service_raises_and_does_not_write() -> None:
    """F3: a missing ``WorkdirService`` must not bypass the single writer."""
    store = FakeStore()
    service = _service(store)

    with pytest.raises(InvalidWorkdirError) as exc:
        await service.set(conv(42), "workdir", "C:/project")

    assert exc.value.kind is None
    assert isinstance(exc.value, InvalidSettingValueError)
    assert store.set_calls == []
    assert store.settings == {}


async def test_set_workdir_outside_home_is_rejected(tmp_path: Path) -> None:
    store = FakeStore()
    home = _home(tmp_path)
    service = SettingsService(
        cast(SessionStore, store),
        workdir_service=WorkdirService(cast(Any, store), home=home),
    )

    with pytest.raises(InvalidWorkdirError) as exc:
        await service.set(conv(42), "workdir", str(tmp_path / "elsewhere"))

    assert exc.value.kind is WorkdirFailureKind.OUTSIDE_ROOT
    assert store.set_calls == []


async def test_set_workdir_missing_is_rejected(tmp_path: Path) -> None:
    store = FakeStore()
    home = _home(tmp_path)
    service = SettingsService(
        cast(SessionStore, store),
        workdir_service=WorkdirService(cast(Any, store), home=home),
    )

    with pytest.raises(InvalidWorkdirError) as exc:
        await service.set(conv(42), "workdir", str(home / "nope"))

    assert exc.value.kind is WorkdirFailureKind.NOT_FOUND
    assert store.set_calls == []


# ── SET-01: provider validated against the catalog ─────────────────────


class _FakeCatalog:
    def __init__(self, snapshot: ModelCatalogSnapshot) -> None:
        self._snapshot = snapshot

    async def list(self, conversation_id: object) -> ModelCatalogSnapshot:
        return self._snapshot


def _snapshot(
    *, options: tuple[ModelOption, ...], source: str, degraded: bool
) -> ModelCatalogSnapshot:
    return ModelCatalogSnapshot(
        options=options,
        groups=(),
        source=source,
        degraded=degraded,
        fetched_at=datetime.now(UTC),
    )


def _provider_service(store: FakeStore, snapshot: ModelCatalogSnapshot) -> SettingsService:
    return SettingsService(
        cast(SessionStore, store),
        model_catalog=cast(OpenCodeModelCatalog, _FakeCatalog(snapshot)),
    )


async def test_set_provider_known_is_accepted_and_persisted() -> None:
    store = FakeStore()
    snapshot = _snapshot(
        options=(ModelOption(id="deepseek/deepseek-flash", provider="DeepSeek", label="Flash"),),
        source="http",
        degraded=False,
    )
    service = _provider_service(store, snapshot)

    value = await service.set(conv(42), "provider", "deepseek")

    assert value == "deepseek"
    assert store.settings["provider"] == "deepseek"


async def test_set_provider_unknown_is_rejected() -> None:
    store = FakeStore()
    snapshot = _snapshot(
        options=(ModelOption(id="deepseek/deepseek-flash", provider="DeepSeek", label="Flash"),),
        source="http",
        degraded=False,
    )
    service = _provider_service(store, snapshot)

    with pytest.raises(InvalidProviderError) as exc:
        await service.set(conv(42), "provider", "nope")

    assert exc.value.provider == "nope"
    assert isinstance(exc.value, InvalidSettingValueError)
    assert store.set_calls == []


@pytest.mark.parametrize(
    "snapshot",
    [
        _snapshot(options=(), source="empty", degraded=True),
        _snapshot(options=(), source="aliases", degraded=True),
    ],
)
async def test_set_provider_degraded_catalog_accepts(
    snapshot: ModelCatalogSnapshot,
) -> None:
    store = FakeStore()
    service = _provider_service(store, snapshot)

    assert await service.set(conv(42), "provider", "anything") == "anything"
    assert store.settings["provider"] == "anything"
