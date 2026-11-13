"""Telegram-specific chunking for Bot-API-sized messages.

The neutral output cleaning lives in ``formatting.markdown`` (the core);
``MessageSender`` handles MarkdownV2 + plain fallback. This module owns only
the splitting of long text into Bot-API-sized chunks.

``split_message`` has NO default length on purpose: the Telegram limit belongs
to ``telegram_config`` and is injected by the frontend (``MessageSender`` /
``TelegramChatView``). The core never reads a Telegram limit.
"""

from __future__ import annotations


def split_message(text: str, max_len: int) -> list[str]:
    """Split long text into Telegram-friendly chunks at natural boundaries.

    ``max_len`` is REQUIRED: it comes from ``telegram_config`` and is injected
    by the Telegram frontend, so the core never owns a Bot-API limit.

    P5: Optimization opportunity — the current O(n) rfind loop could be replaced
    with a single regex split, but rfind is acceptable for typical response sizes.
    """
    if len(text) <= max_len:
        return [text]

    parts = []
    remaining = text
    total = (len(text) + max_len - 1) // max_len

    idx = 1
    while remaining:
        if len(remaining) <= max_len:
            parts.append(f"(parte {idx}/{total})\n{remaining}")
            break

        chunk = remaining[:max_len]
        split_at = max(
            chunk.rfind("\n\n"),
            chunk.rfind(". "),
            chunk.rfind("\n"),
            chunk.rfind(" "),
        )

        if split_at == -1 or split_at < max_len // 2:
            split_at = max_len

        chunk = remaining[: split_at + 1].rstrip()
        parts.append(f"(parte {idx}/{total})\n{chunk}")
        remaining = remaining[split_at + 1 :].lstrip()
        idx += 1

    return parts
