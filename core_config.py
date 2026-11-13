"""Frontend-agnostic core configuration.

This module is the *core* half of the old ``config.py`` split (item 3a of
``core-isolation``). It holds everything a headless embedder needs — paths,
logging, the OpenCode/client settings, OpenAI, models and session storage —
and it NEVER fails fast at import time. There is no ``ALLOWED_CHAT_IDS`` here
and no ``SystemExit``: importing ``core_config`` (or any ``services.*`` that
depend on it) must succeed with zero Telegram environment.

The Telegram-only settings live in ``telegram_config`` (the *view*), which owns
the explicit ``load_telegram_config()`` fail-fast called by the entry points.
"""

import logging
import logging.handlers
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# ─── Paths ───
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(ENV_PATH)

# ─── Logging ───
# IMPORTANT: importing this module has ZERO logging side effects. The
# `opencode_bot` logger is created unconfigured on purpose; `setup_logger()`
# is called explicitly and exactly once by each entry point (bot.py,
# flet_app/main.py) as its first action. Before that call records fall through
# to logging.lastResort (stderr) — they are NEVER written to bot.log just
# because some script imported the library. tests/conftest.py also calls it
# (with BOT_LOG_FILE redirected to a temp path) as belt-and-suspenders.
LOG_DIR = Path(__file__).resolve().parent
# Env override (BOT_LOG_FILE) lets tests/ETC redirect the log elsewhere;
# the default stays the production bot.log.
LOG_FILE = Path(os.getenv("BOT_LOG_FILE", LOG_DIR / "bot.log"))
START_TIME = None  # set at startup

# Swallowed-exception logging verbosity — THE one knob controlling every
# log_exception() record (namespace `opencode_bot.swallowed.*`). Set it to
# logging.WARNING to silence all recovered-exception traces at once; DEBUG
# (default) keeps them visible in development. Deliberately a module constant
# (no env override): one typed, greppable place to flip.
#
# END-TO-END: this knob only works because the handlers below are set to DEBUG
# (they never filter). Verbosity is controlled by LOGGERS, not handlers — the
# `opencode_bot` logger at INFO sets normal verbosity, and this knob controls
# swallowed verbosity. If a handler were set to INFO it would drop DEBUG
# records regardless of this value (the #358 gotcha).
SWALLOWED_LOG_LEVEL = logging.DEBUG

# Output format for ALL handlers: "text" (key=value, default) or "json".
# Text is for humans (grep-friendly); JSON is for machines (jq / future
# Loki-Sentry shipping). Unknown values fall back to text.
# Normalized (strip + lower) so " JSON " from a .env file still works; the
# fallback path is only for real typos.
LOG_FORMAT = os.getenv("BOT_LOG_FORMAT", "text").strip().lower()

# The app logger exists at import time (modules call get_module_logger() while
# being imported) but has NO handlers until an entry point calls setup_logger().
logger = logging.getLogger("opencode_bot")
_logger_configured = False


def setup_logger() -> logging.Logger:
    """Configure rotating file + console logger with structured key=value format.

    Idempotent: the entry points call it once, tests/conftest.py calls it once,
    and a second call must not duplicate handlers (and must not re-open the
    log file). This function is the ONLY place that attaches the production
    file handler; importing this module never does.

    Why two different handlers?
      - Console: minimal prefix (timestamp + level + message). Interactive
        use — no need to grep by request_id, just scan the terminal.
      - File: includes [req=...] for correlation and full logger name for
        forensic search via `grep event=session_created bot.log`.

    Formatters are chosen by LOG_FORMAT (BOT_LOG_FORMAT): text renders
    extra={} fields as `key=value` pairs before the message; json emits one
    JSON object per line. Templates are only used in text mode. See
    utils/logging.py for details.
    """
    global _logger_configured
    if _logger_configured:
        return logger

    logger.setLevel(logging.INFO)

    # Import from utils here to avoid circular import (core_config is loaded
    # very early, before utils may be fully initialized).
    from utils.logging import RequestIdFilter, make_formatter

    # Forgiving resolution: a typo must never crash the bot (falls back to
    # text); the warning below records that it happened.
    fmt = LOG_FORMAT if LOG_FORMAT in ("text", "json") else "text"

    # ── Console handler (no request_id — too noisy for interactive use) ──
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_fmt = make_formatter(
        fmt,
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(console_fmt)
    logger.addHandler(console_handler)

    # ── File handler (with request_id for forensic correlation) ──
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.addFilter(RequestIdFilter())
    file_fmt = make_formatter(
        fmt,
        "%(asctime)s [%(levelname)s] [req=%(request_id)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_fmt)
    logger.addHandler(file_handler)

    # One-knob switch for swallowed-exception logging (see SWALLOWED_LOG_LEVEL).
    # Children (`opencode_bot.swallowed.<module>`) inherit this level.
    logging.getLogger("opencode_bot.swallowed").setLevel(SWALLOWED_LOG_LEVEL)

    # Emitted AFTER attaching handlers on purpose: before that, the record
    # would only reach logging.lastResort (stderr) and never bot.log.
    if fmt != LOG_FORMAT:
        logger.warning(
            "BOT_LOG_FORMAT inválido: %r. Se usará 'text'.",
            LOG_FORMAT,
            extra={"event": "log_format_fallback", "bogus_format": LOG_FORMAT},
        )

    _logger_configured = True
    return logger


# NOTE: no setup_logger() call here — see the "importing has zero logging side
# effects" note in the Logging section above. Entry points own the call.

# ─── OpenCode ───
OPENCODE_WORKDIR = os.getenv("OPENCODE_WORKDIR", str(BASE_DIR))
OPENCODE_TIMEOUT = int(os.getenv("OPENCODE_TIMEOUT", "300"))


def resolve_opencode_cmd() -> str:
    """Find the opencode executable.

    Portable by construction: ``shutil.which("opencode")`` is the candidate that
    resolves on Linux (the production VPS). The ``AppData/Roaming/npm`` entry is
    only a Windows dev-machine convenience — ``Path.expanduser`` turns it into a
    non-existent path on Linux, so it degrades gracefully instead of breaking.
    """
    import shutil

    known_npm_path = Path(r"~\AppData\Roaming\npm\opencode.cmd").expanduser()
    candidates = [
        ("OPENCODE_CMD env var", os.getenv("OPENCODE_CMD")),
        ("shutil.which('opencode')", shutil.which("opencode")),
        ("shutil.which('opencode.cmd')", shutil.which("opencode.cmd")),
        ("known npm path", str(known_npm_path)),
    ]
    for _source, path in candidates:
        if path and Path(path).is_file():
            return path
    return "opencode"


OPENCODE_CMD = os.getenv("OPENCODE_CMD") or resolve_opencode_cmd()

# ─── OpenCode server (v1 HTTP API) ───
# No import-time fail-fast here: library imports must stay side-effect free.
# OpenCodeServerManager.ensure_running() raises OpenCodeServerConfigError when
# the password is empty, so both entry points fail before serving.
OPENCODE_SERVER_HOST = os.getenv("OPENCODE_SERVER_HOST", "127.0.0.1")
OPENCODE_SERVER_PORT = int(os.getenv("OPENCODE_SERVER_PORT", "4096"))
OPENCODE_SERVER_PASSWORD = os.getenv("OPENCODE_SERVER_PASSWORD", "")
OPENCODE_SERVER_BINARY = os.getenv("OPENCODE_SERVER_BINARY") or OPENCODE_CMD
# Opt-in escape hatch for the loopback guard. The bot is the only client of the
# agent, so a non-loopback host is rejected unless this is explicitly enabled.
# It exists ONLY for deployments where the bot and the agent run on different
# machines; clients (Telegram, APK) must still talk to the bot, never to the
# engine. Validation happens at server construction, not at import time.
OPENCODE_SERVER_ALLOW_REMOTE = os.getenv("OPENCODE_SERVER_ALLOW_REMOTE", "").strip().lower() in {
    "1",
    "true",
    "yes",
}
# How long a permission dialog may wait for a human before auto-rejecting.
# 0 (default) = wait INDEFINITELY for the human (no deadline while the dialog
# is open): the agent must not proceed without consent, even if it takes hours.
# A positive value N = auto-reject after N seconds as a safety net, so the
# agent never stays blocked forever when nobody answers. Tune it to your
# workflow: while a dialog is open the prompt's OPENCODE_TIMEOUT is suspended,
# so a human wait never consumes the prompt budget.
OPENCODE_PERMISSION_TIMEOUT = int(os.getenv("OPENCODE_PERMISSION_TIMEOUT", "0"))
OPENCODE_BACKEND = os.getenv("OPENCODE_BACKEND", "server").strip().lower()

# ─── OpenAI ───
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# ─── Models ───
DEFAULT_MODEL = "deepseek/deepseek-flash"
MODEL_ALIASES = {
    "deepseek": {
        "pro": "deepseek/deepseek-v4-pro",
        "flash": "deepseek/deepseek-flash",
        "deepseek-v4-pro": "deepseek/deepseek-v4-pro",
        "deepseek-flash": "deepseek/deepseek-flash",
    },
    "minimax": {
        "m3": "minimax/MiniMax-M3",
        "m27": "minimax/MiniMax-M2.7",
        "m27-fast": "minimax/MiniMax-M2.7-highspeed",
        "minimax-m3": "minimax/MiniMax-M3",
        "minimax-m27": "minimax/MiniMax-M2.7",
        "minimax-m27-highspeed": "minimax/MiniMax-M2.7-highspeed",
    },
}


def resolve_model(alias_or_full: str) -> str:
    for _provider, aliases in MODEL_ALIASES.items():
        if alias_or_full in aliases:
            return aliases[alias_or_full]
    return alias_or_full


# ─── Sessions ───
SESSION_DB = Path(__file__).resolve().parent / "sessions.json"
SESSIONS_PATH = SESSION_DB  # alias for SessionStore

# ── Constants  ──────────────────────────────────────
DEFAULT_SESSION_NAME = "default"
PROGRESS_UPDATE_INTERVAL = 5  # seconds between "procesando..." updates
INTERNAL_SUBPROCESS_TIMEOUT = 10  # seconds for opencode session list / db queries

# DB schema exploration at startup (bot.py). False by default; set EXPLORE_DB=1.
EXPLORE_DB = os.getenv("EXPLORE_DB", "").lower() in ("1", "true", "yes")
