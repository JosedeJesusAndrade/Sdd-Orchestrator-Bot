"""Pure date-bucketing for the drawer's session list (SH-04 / D7).

Given the conversation summaries and the current epoch-ms, group them into the
five Spanish date buckets ordered newest-first. Empty buckets are dropped so no
orphan header renders, and every session is bucketed by ``updated_at`` — an
orphan (``adopted_name is None``) is bucketed exactly like a bot-bound one.

Pure: no ``flet``, no I/O, inputs -> outputs.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from flet_app.ui_strings import (
    BUCKET_LAST_7_DAYS,
    BUCKET_LAST_30_DAYS,
    BUCKET_OLDER,
    BUCKET_TODAY,
    BUCKET_YESTERDAY,
)
from services.agent_sessions import ConversationSummary

#: A bucket is its label plus the (newest-first) sessions that fall into it.
Bucket = tuple[str, tuple[ConversationSummary, ...]]

_BUCKET_ORDER: tuple[str, ...] = (
    BUCKET_TODAY,
    BUCKET_YESTERDAY,
    BUCKET_LAST_7_DAYS,
    BUCKET_LAST_30_DAYS,
    BUCKET_OLDER,
)


def _day_start(now_ms: int) -> datetime:
    """Local midnight of the calendar day containing ``now_ms``."""
    now = datetime.fromtimestamp(now_ms / 1000)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def bucket_sessions(
    sessions: Sequence[ConversationSummary],
    now_ms: int,
) -> list[Bucket]:
    """Group ``sessions`` into the five date buckets, empties omitted."""
    today = _day_start(now_ms)
    yesterday = today - timedelta(days=1)
    week_floor = today - timedelta(days=7)
    month_floor = today - timedelta(days=30)

    grouped: dict[str, list[ConversationSummary]] = {label: [] for label in _BUCKET_ORDER}

    for summary in sessions:
        updated = datetime.fromtimestamp(summary.updated_at / 1000)
        if updated >= today:
            label = BUCKET_TODAY
        elif updated >= yesterday:
            label = BUCKET_YESTERDAY
        elif updated >= week_floor:
            label = BUCKET_LAST_7_DAYS
        elif updated >= month_floor:
            label = BUCKET_LAST_30_DAYS
        else:
            label = BUCKET_OLDER
        grouped[label].append(summary)

    buckets: list[Bucket] = []
    for label in _BUCKET_ORDER:
        items = grouped[label]
        if not items:
            continue
        items.sort(key=lambda summary: summary.updated_at, reverse=True)
        buckets.append((label, tuple(items)))
    return buckets
