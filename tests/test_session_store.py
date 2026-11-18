"""Unit tests for SessionStore."""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from services.session_store import SessionStore
from tests.conversation_keys import conv


@pytest.fixture
def tmp_store():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "test_sessions.json"
        store = SessionStore(path)
        yield store


def test_get_model_default(tmp_store):
    """get_model returns DEFAULT_MODEL for unknown chat."""
    model = asyncio.run(tmp_store.get_model(conv(12345)))
    assert model == "deepseek/deepseek-flash"


def test_set_and_get_model(tmp_store):
    """set_model persists and get_model retrieves."""
    asyncio.run(tmp_store.set_model(conv(12345), "test-model"))
    model = asyncio.run(tmp_store.get_model(conv(12345)))
    assert model == "test-model"


def test_create_session(tmp_store):
    """create_session adds a named session."""
    session = asyncio.run(tmp_store.create_session(conv(12345), "test-session"))
    assert session.name == "test-session"
    assert session.is_active is True
    assert session.real_id is None
    assert session.prompt_count == 0


def test_list_sessions(tmp_store):
    """list_sessions returns all sessions for a chat."""
    asyncio.run(tmp_store.create_session(conv(12345), "session-a"))
    asyncio.run(tmp_store.create_session(conv(12345), "session-b"))
    sessions = asyncio.run(tmp_store.list_sessions(conv(12345)))
    assert len(sessions) == 2
    names = {s.name for s in sessions}
    assert names == {"session-a", "session-b"}


def test_increment_prompt_count(tmp_store):
    """increment_prompt_count increases counter."""
    asyncio.run(tmp_store.create_session(conv(12345), "test"))
    count = asyncio.run(tmp_store.increment_prompt_count(conv(12345)))
    assert count == 1
    count = asyncio.run(tmp_store.increment_prompt_count(conv(12345)))
    assert count == 2


def test_chat_settings(tmp_store):
    """get/set_chat_setting persists per-chat config."""
    asyncio.run(tmp_store.set_chat_setting(conv(12345), "model", "custom-model"))
    model = asyncio.run(tmp_store.get_chat_setting(conv(12345), "model"))
    assert model == "custom-model"

    # Default returned for unknown key
    timeout = asyncio.run(tmp_store.get_chat_setting(conv(12345), "timeout", 300))
    assert timeout == 300


# ── P4: workdir, ensure_active, legacy records ─────────────────────────


def test_create_session_stamps_workdir_and_last_used(tmp_store):
    now = "2026-11-17T00:00:00+00:00"
    created = asyncio.run(
        tmp_store.create_session(conv(12345), "chat-1", workdir="C:/wA", last_used=now)
    )

    assert created.workdir == "C:/wA"
    assert created.last_used == now

    listed = asyncio.run(tmp_store.list_sessions(conv(12345)))
    assert listed[0].workdir == "C:/wA"
    assert listed[0].last_used == now
    active = asyncio.run(tmp_store.get_active_session(conv(12345)))
    assert active is not None and active.workdir == "C:/wA"


def test_create_session_without_workdir_leaves_it_none(tmp_store):
    created = asyncio.run(tmp_store.create_session(conv(12345), "plain"))

    assert created.workdir is None
    assert created.last_used is None


def test_legacy_record_without_workdir_reads_none_without_migration(tmp_path: Path):
    path = tmp_path / "sessions.json"
    legacy = {
        "telegram:1": {
            "active": "default",
            "sessions": {
                "default": {
                    "id": "ses_old",
                    "title": "default",
                    "created": "2026-05-19T00:00:00+00:00",
                    "last_used": None,
                    "prompt_count": 0,
                }
            },
        }
    }
    path.write_text(json.dumps(legacy), encoding="utf-8")
    store = SessionStore(path)

    sessions = asyncio.run(store.list_sessions(conv("telegram:1")))

    assert len(sessions) == 1
    assert sessions[0].workdir is None
    # No migration: the legacy payload has no ``workdir`` key and no .bak
    # is produced (the .bak is only written when keys are migrated).
    assert not (tmp_path / "sessions.json.bak").exists()


def test_ensure_active_creates_the_default_on_a_fresh_key(tmp_store):
    session = asyncio.run(tmp_store.ensure_active(conv(12345)))

    assert session.name == "default"
    assert session.is_active is True
    assert [s.name for s in asyncio.run(tmp_store.list_sessions(conv(12345)))] == ["default"]


def test_ensure_active_is_idempotent(tmp_store):
    asyncio.run(tmp_store.ensure_active(conv(12345)))
    asyncio.run(tmp_store.ensure_active(conv(12345)))

    assert [s.name for s in asyncio.run(tmp_store.list_sessions(conv(12345)))] == ["default"]


def test_ensure_active_activates_an_existing_default(tmp_path: Path):
    path = tmp_path / "sessions.json"
    payload = {
        "telegram:1": {
            "sessions": {
                "default": {
                    "id": None,
                    "title": "default",
                    "created": "2026-05-19T00:00:00+00:00",
                    "last_used": None,
                    "prompt_count": 0,
                },
                "chat-1": {
                    "id": "ses_1",
                    "title": "chat-1",
                    "created": "2026-05-19T00:00:00+00:00",
                    "last_used": None,
                    "prompt_count": 0,
                },
            }
        }
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    store = SessionStore(path)

    session = asyncio.run(store.ensure_active(conv("telegram:1")))

    assert session.name == "default"
    assert session.is_active is True
    active = asyncio.run(store.get_active_session(conv("telegram:1")))
    assert active is not None and active.name == "default"


def test_ensure_active_returns_the_active_session_unchanged(tmp_store):
    asyncio.run(tmp_store.create_session(conv(12345), "chat-1"))

    session = asyncio.run(tmp_store.ensure_active(conv(12345)))

    assert session.name == "chat-1"
    assert [s.name for s in asyncio.run(tmp_store.list_sessions(conv(12345)))] == ["chat-1"]
