"""Neutral output cleaning for engine responses.

Frontend-agnostic: this module knows nothing about Telegram (or any other
frontend). It only strips ANSI/tool traces and assembles stdout+stderr into
the final response text. Telegram-specific formatting (MarkdownV2 escaping,
table conversion, Bot-API chunking) lives in ``frontends.telegram.formatting``.
"""

import re

from utils.logging import get_module_logger

logger = get_module_logger(__name__)


def _filter_stderr(stderr: str) -> str:
    """Filter stderr to only include meaningful response lines (not metadata)."""
    if not stderr or not stderr.strip():
        return ""
    lines = []
    for line in stderr.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if "build" in lower and chr(183) in stripped:
            continue
        if stripped.startswith("[INFO]") or stripped.startswith("[DEBUG]"):
            continue
        if stripped.startswith("[WARN"):
            continue
        lines.append(stripped)
    return "\n".join(lines)


def _remove_tool_traces(text: str) -> str:
    """Remove tool call trace lines (single and multi-line JSON).

    Detects lines starting with ⚙ (or its cp1252-mangled form âš™)
    and skips them plus any multi-line JSON that follows.
    """
    result = []
    in_tool_trace = False
    brace_depth = 0

    for line in text.split("\n"):
        stripped = line.strip()

        # Detect start of tool trace: ⚙ (U+2699) or âš™ (mangled UTF-8 via cp1252)
        if stripped.startswith("⚙") or stripped.startswith("âš™"):
            in_tool_trace = True
            brace_depth = 0

        if in_tool_trace:
            brace_depth += stripped.count("{") - stripped.count("}")
            if brace_depth <= 0 and ("{" in stripped or "}" in stripped):
                in_tool_trace = False
            continue

        result.append(line)

    return "\n".join(result)


def clean_opencode_output(text: str) -> str:
    """Remove ANSI escape codes and clean up terminal output for the frontend."""
    # 1. Remove ANSI escape sequences (ESC + CSI codes)
    ansi_pattern = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    text = ansi_pattern.sub("", text)

    # 2. Remove header lines (model info like "> build · deepseek-v4-pro" or
    #    "> sdd-orchestrator · MiniMax-M3"). The agent name can be any token.
    #    Critical: must strip ALL `> <token> · <model>` headers, otherwise the
    #    second-pass clean in _assemble_response keeps the header, and step 8
    #    below trims everything before the FIRST `>` line — which can drop the
    #    entire response body when the header was appended via stderr.
    text = re.sub(r"^> \S+ .*$", "", text, flags=re.MULTILINE)

    # 3. Remove tool call traces (single and multi-line JSON with ⚙ marker)
    text = _remove_tool_traces(text)

    # 4. Clean up trailing --- separator (leftover when stderr was only tool traces)
    text = re.sub(r"\n*---\s*$", "", text)

    # 5. Replace Unicode symbols with text equivalents
    text = text.replace("\u2731", "\u2192")

    # 6. Collapse multiple blank lines into one
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 7. Split into lines, strip whitespace
    lines = [line.strip() for line in text.split("\n")]

    # 8. Trim everything before first line starting with ">" (opencode logo/header)
    for i, line in enumerate(lines):
        if line.startswith(">"):
            lines = lines[i:]
            break

    # 9. Remove empty lines and filter out noise
    filtered = []
    for line in lines:
        if not line:
            continue
        lower = line.lower()
        if "auto-rejecting" in lower or "permission requested" in lower:
            continue
        if "user rejected permission" in lower:
            continue
        filtered.append(line)

    return "\n".join(filtered)


def _assemble_response(raw_stdout: str, raw_stderr: str) -> str:
    """Build the final response from stdout and stderr, filtering metadata."""
    stdout_text = raw_stdout.strip() if raw_stdout else ""
    meaningful_stderr = _filter_stderr(raw_stderr)

    if stdout_text:
        response = stdout_text
        if meaningful_stderr:
            response += "\n\n---\n" + meaningful_stderr
    else:
        response = meaningful_stderr if meaningful_stderr else raw_stderr.strip()

    return clean_opencode_output(response)
