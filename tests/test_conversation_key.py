"""Unit tests for the opaque ``ConversationKey`` and key-aware masking."""

from __future__ import annotations

import pytest

from flet_app.keys import flet_key
from frontends.telegram.keys import telegram_key
from services.conversation import (
    ConversationKey,
    make_conversation_key,
    split_conversation_key,
)
from utils.logging import mask_conversation_key


def test_make_conversation_key_namespaces_the_local_part() -> None:
    assert make_conversation_key("telegram", "123") == ConversationKey("telegram:123")
    assert make_conversation_key("flet", "local") == ConversationKey("flet:local")


def test_make_conversation_key_rejects_empty_prefix() -> None:
    with pytest.raises(ValueError):
        make_conversation_key("", "123")


def test_make_conversation_key_rejects_nested_separator_in_prefix() -> None:
    with pytest.raises(ValueError):
        make_conversation_key("tele:gram", "123")


def test_make_conversation_key_rejects_empty_local_part() -> None:
    with pytest.raises(ValueError):
        make_conversation_key("telegram", "")


def test_telegram_key_uses_the_namespaced_chat_id() -> None:
    assert telegram_key(8664220427) == ConversationKey("telegram:8664220427")
    assert telegram_key(-100123) == ConversationKey("telegram:-100123")


def test_flet_key_is_stable_and_local() -> None:
    # NOT a uuid: persistence continuity across launches depends on this.
    assert flet_key() == ConversationKey("flet:local")
    assert flet_key("custom") == ConversationKey("flet:custom")


def test_flet_entry_point_mints_the_stable_key() -> None:
    from flet_app.main import CONVERSATION_ID

    assert CONVERSATION_ID == flet_key() == ConversationKey("flet:local")


def test_split_conversation_key_returns_namespace_and_local_part() -> None:
    assert split_conversation_key(ConversationKey("telegram:123")) == ("telegram", "123")
    assert split_conversation_key(ConversationKey("flet:local")) == ("flet", "local")
    assert split_conversation_key(ConversationKey("legacy")) == ("", "legacy")


def test_mask_conversation_key_keeps_namespace_and_masks_tail() -> None:
    assert mask_conversation_key("telegram:8664220427") == "telegram:86******27"
    assert mask_conversation_key("flet:local") == "flet:lo*al"


def test_mask_conversation_key_falls_back_for_un_namespaced_values() -> None:
    assert mask_conversation_key("12345") == "12*45"
    assert mask_conversation_key("42") == "**"
