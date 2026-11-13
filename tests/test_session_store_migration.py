"""sessions.json key migration: prefixed keys, backup, idempotency, fail-safe.

Every test uses ``tmp_path`` — the repository's real sessions.json is never
read or rewritten by the suite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.conversation import ConversationKey
from services.session_store import SessionStore

TELEGRAM = ConversationKey("telegram:123")
FLET = ConversationKey("flet:local")


def _payload() -> dict[str, object]:
    return {
        "123": {
            "active": "default",
            "model": "legacy/model",
            "sessions": {
                "default": {
                    "id": None,
                    "title": "default",
                    "created": "2026-01-01T00:00:00+00:00",
                    "last_used": None,
                    "prompt_count": 2,
                }
            },
        },
        "flet": {
            "active": "default",
            "model": "flet/model",
            "sessions": {
                "default": {
                    "id": None,
                    "title": "default",
                    "created": "2026-01-01T00:00:00+00:00",
                    "last_used": None,
                    "prompt_count": 0,
                }
            },
        },
    }


def _write(path: Path, data: object) -> str:
    text = json.dumps(data, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    return text


async def test_migrates_numeric_and_flet_keys_and_creates_backup(tmp_path: Path) -> None:
    path = tmp_path / "sessions.json"
    original = _write(path, _payload())
    store = SessionStore(path)

    assert await store.get_model(TELEGRAM) == "legacy/model"
    assert await store.get_model(FLET) == "flet/model"

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert set(persisted) == {"telegram:123", "flet:local"}

    backup = tmp_path / "sessions.json.bak"
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == original


async def test_migration_is_idempotent_and_does_not_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "sessions.json"
    _write(path, _payload())
    await SessionStore(path).get_model(TELEGRAM)

    migrated_bytes = path.read_bytes()
    backup_bytes = (tmp_path / "sessions.json.bak").read_bytes()

    second = SessionStore(path)
    writes: list[object] = []
    monkeypatch.setattr(second, "_write_payload", lambda data: writes.append(data))

    assert await second.get_model(TELEGRAM) == "legacy/model"

    assert writes == [], "an already-migrated file must not be rewritten"
    assert path.read_bytes() == migrated_bytes
    assert (tmp_path / "sessions.json.bak").read_bytes() == backup_bytes


async def test_corrupt_file_is_preserved_in_the_backup(tmp_path: Path) -> None:
    path = tmp_path / "sessions.json"
    corrupt = "{ this is not valid json"
    path.write_text(corrupt, encoding="utf-8")

    store = SessionStore(path)
    # Starts fresh instead of crashing…
    assert await store.get_model(TELEGRAM) == "deepseek/deepseek-flash"

    # …and the unreadable payload survives for forensic recovery.
    backup = tmp_path / "sessions.json.bak"
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == corrupt


async def test_negative_telegram_ids_are_migrated(tmp_path: Path) -> None:
    path = tmp_path / "sessions.json"
    _write(path, {"-100123": {"model": "group/model"}})

    store = SessionStore(path)
    assert await store.get_model(ConversationKey("telegram:-100123")) == "group/model"

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert "telegram:-100123" in persisted
    assert "-100123" not in persisted


async def test_already_prefixed_key_wins_over_a_colliding_legacy_key(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sessions.json"
    _write(
        path,
        {
            "123": {"model": "legacy/model"},
            "telegram:123": {"model": "canonical/model"},
        },
    )

    store = SessionStore(path)
    assert await store.get_model(TELEGRAM) == "canonical/model"

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert set(persisted) == {"telegram:123"}


async def test_backup_is_written_once_and_never_clobbered(tmp_path: Path) -> None:
    path = tmp_path / "sessions.json"
    original = _write(path, _payload())
    await SessionStore(path).get_model(TELEGRAM)
    first_backup = (tmp_path / "sessions.json.bak").read_text(encoding="utf-8")
    assert first_backup == original

    # Simulate an external writer re-introducing a legacy key later.
    current = json.loads(path.read_text(encoding="utf-8"))
    current["999"] = {"model": "late/model"}
    _write(path, current)

    store = SessionStore(path)
    assert await store.get_model(ConversationKey("telegram:999")) == "late/model"

    # The backup still holds the FIRST pre-migration snapshot.
    assert (tmp_path / "sessions.json.bak").read_text(encoding="utf-8") == original
