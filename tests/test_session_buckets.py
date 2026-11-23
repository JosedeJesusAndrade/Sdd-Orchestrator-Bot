"""Contract for the pure date-bucketing helper (SH-04 / D7).

Pins the five labels, the midnight boundaries, the hidden empties, the
newest-first ordering and the orphan handling — with no ``flet`` in play.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from flet_app.core.session_buckets import bucket_sessions
from flet_app.ui_strings import (
    BUCKET_LAST_7_DAYS,
    BUCKET_LAST_30_DAYS,
    BUCKET_OLDER,
    BUCKET_TODAY,
    BUCKET_YESTERDAY,
)
from services.agent_sessions import ConversationSummary
from tests.seam_support import probe_import

# A deterministic reference day (local noon) used by every boundary test.
_NOW = datetime(2026, 11, 23, 12, 0)
_NOW_MS = int(_NOW.timestamp() * 1000)
_MIDNIGHT = datetime(2026, 11, 23, 0, 0)


def _ms(day_offset: int = 0, *, at_midnight: bool = True) -> int:
    base = _MIDNIGHT if at_midnight else _NOW
    return int((base + timedelta(days=day_offset)).timestamp() * 1000)


def _summary(
    updated_at: int,
    *,
    real_id: str = "ses_1",
    adopted_name: str | None = None,
) -> ConversationSummary:
    return ConversationSummary(
        real_id=real_id,
        title=real_id,
        created_at=updated_at,
        updated_at=updated_at,
        message_count=1,
        directory="/repo",
        agent="opencode",
        adopted_name=adopted_name,
    )


def test_empty_input_returns_no_buckets() -> None:
    assert bucket_sessions([], _NOW_MS) == []


def test_all_five_labels_in_order_with_sessions_across_the_ranges() -> None:
    sessions = [
        _summary(_ms(0), real_id="today"),  # today
        _summary(_ms(-1), real_id="yest"),  # yesterday
        _summary(_ms(-3), real_id="week"),  # 3 days ago -> 7
        _summary(_ms(-10), real_id="month"),  # 10 days ago -> 30
        _summary(_ms(-40), real_id="old"),  # 40 days ago -> older
    ]

    buckets = bucket_sessions(sessions, _NOW_MS)

    assert [label for label, _ in buckets] == [
        BUCKET_TODAY,
        BUCKET_YESTERDAY,
        BUCKET_LAST_7_DAYS,
        BUCKET_LAST_30_DAYS,
        BUCKET_OLDER,
    ]
    assert [ids[0].real_id for _, ids in buckets] == [
        "today",
        "yest",
        "week",
        "month",
        "old",
    ]


def test_empty_buckets_are_hidden() -> None:
    sessions = [_summary(_ms(0), real_id="today"), _summary(_ms(-40), real_id="old")]

    buckets = bucket_sessions(sessions, _NOW_MS)

    assert [label for label, _ in buckets] == [BUCKET_TODAY, BUCKET_OLDER]


def test_midnight_boundaries_are_inclusive_on_the_upper_bucket() -> None:
    sessions = [
        _summary(_ms(0), real_id="today"),  # exactly today 00:00 -> Hoy
        _summary(_ms(-1), real_id="yesterday"),  # exactly yesterday 00:00 -> Ayer
        _summary(_ms(-7), real_id="week_floor"),  # exactly 7 days ago 00:00 -> 7
        _summary(_ms(-30), real_id="month_floor"),  # exactly 30 days ago 00:00 -> 30
        _summary(_ms(-31), real_id="older"),  # 31 days ago -> Más antiguas
    ]

    buckets = dict(bucket_sessions(sessions, _NOW_MS))

    assert buckets[BUCKET_TODAY][0].real_id == "today"
    assert buckets[BUCKET_YESTERDAY][0].real_id == "yesterday"
    assert buckets[BUCKET_LAST_7_DAYS][0].real_id == "week_floor"
    assert buckets[BUCKET_LAST_30_DAYS][0].real_id == "month_floor"
    assert buckets[BUCKET_OLDER][0].real_id == "older"


def test_orphans_are_bucketed_by_updated_at() -> None:
    # same day bucket, one bot-bound and one orphan; both keyed by updated_at.
    sessions = [
        _summary(_ms(0, at_midnight=False), real_id="bound", adopted_name="chat-1"),
        _summary(_ms(0, at_midnight=False) - 60_000, real_id="orphan", adopted_name=None),
    ]

    buckets = bucket_sessions(sessions, _NOW_MS)

    assert len(buckets) == 1
    label, items = buckets[0]
    assert label == BUCKET_TODAY
    assert {item.real_id for item in items} == {"bound", "orphan"}


def test_sessions_are_newest_first_within_a_bucket() -> None:
    sessions = [
        _summary(_ms(0, at_midnight=False) - 3_600_000, real_id="older_today"),
        _summary(_ms(0, at_midnight=False), real_id="newer_today"),
    ]

    ((label, items),) = bucket_sessions(sessions, _NOW_MS)

    assert label == BUCKET_TODAY
    assert [item.real_id for item in items] == ["newer_today", "older_today"]


def test_helper_module_is_flet_free() -> None:
    returncode, loaded = probe_import("flet_app.core.session_buckets")

    assert returncode == 0
    assert loaded == set()
