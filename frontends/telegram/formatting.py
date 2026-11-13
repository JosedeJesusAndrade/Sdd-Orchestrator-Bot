"""Telegram-specific markdown formatting and chunking.

The neutral output cleaning lives in ``formatting.markdown`` (the core). This
module owns only the Telegram-flavored pieces: MarkdownV2 escaping, table
conversion, and splitting into Bot-API-sized chunks.

``split_message`` has NO default length on purpose: the Telegram limit belongs
to ``telegram_config`` and is injected by the frontend (``MessageSender`` /
``TelegramChatView``). The core never reads a Telegram limit.
"""

from __future__ import annotations

import re


def telegramify_markdown(text: str) -> str:
    """Convert markdown to Telegram MarkdownV2 compatible format.

    Telegram MarkdownV2 supports: *bold*, _italic_, __underline__, ~strikethrough~,
    ||spoiler||, `code`, ```pre```, [links](url)

    It does NOT support: tables, HTML, images.
    """
    lines = text.split("\n")
    result = []
    in_table = False
    table_lines: list[str] = []

    def flush_table() -> None:
        nonlocal table_lines
        if table_lines:
            result.append("```")
            result.extend(table_lines)
            result.append("```")
            table_lines = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        next_stripped = lines[i + 1].strip() if i + 1 < len(lines) else ""

        # Detect table: line starts/ends with | AND next line is separator (|---|)
        if stripped.startswith("|") and stripped.endswith("|"):
            if not in_table and next_stripped.startswith("|") and "---" in next_stripped:
                in_table = True
            if in_table:
                # Skip separator lines (|---|---|)
                if not all(c in "|-: " for c in stripped.replace("|", "-")):
                    clean = stripped.strip("|").strip()
                    table_lines.append(f"│ {clean} │")
                continue
        else:
            if in_table:
                flush_table()
                in_table = False
            result.append(line)

    if in_table:
        flush_table()

    final = "\n".join(result)
    final = re.sub(r"\|[-:\s|]+\|", "", final)

    return final


def sanitize_markdownv2(text: str) -> str:
    """Escape MarkdownV2 special characters for Telegram.

    Escape rules per Telegram Bot API:
    - Outside code blocks/inline: _ * [ ] ( ) ~ ` > # + - = | { } . !
    - Inside code blocks/inline: ` (backtick) and \\ (backslash)
    """
    result = []
    in_code_block = False
    in_inline_code = False

    i = 0
    while i < len(text):
        if text[i : i + 3] == "```":
            in_code_block = not in_code_block
            result.append("```")
            i += 3
            continue

        if not in_code_block and text[i] == "`":
            in_inline_code = not in_inline_code
            result.append("`")
            i += 1
            continue

        ch = text[i]

        if in_code_block or in_inline_code:
            if ch in ("`", "\\"):
                result.append("\\" + ch)
            else:
                result.append(ch)
        else:
            if ch in "_*[]()~`>#+-=|{}.!":
                result.append("\\" + ch)
            else:
                result.append(ch)

        i += 1

    return "".join(result)


def minimal_escape_mdv2(text: str) -> str:
    """Escape underscores for MarkdownV2, preserving code blocks.

    Escapes ALL underscores outside code blocks and inline code.
    This is safe for OpenCode output where underscores are typically
    literal (variable_names, file_names) rather than formatting.
    """
    parts = re.split(r"(```[\s\S]*?```|`[^`\n]*?`)", text)
    result = []
    for i, part in enumerate(parts):
        if not part:
            continue
        if i % 2 == 1:
            result.append(part)
        else:
            # Escape MarkdownV2 reserved chars that commonly appear as literals
            # _ (underscore): variable_names, __init__, _private
            # . (period): end of sentences, file extensions
            # ! (exclamation): end of sentences
            # Escape ALL MarkdownV2 reserved chars that are commonly literal in OpenCode output
            # Safe to escape everywhere outside code: they're almost NEVER intentional formatting
            for ch in ("_", ".", "!", "+", "-", "=", "#", ">", "(", ")"):
                part = part.replace(ch, "\\" + ch)
            result.append(part)
    return "".join(result)


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
