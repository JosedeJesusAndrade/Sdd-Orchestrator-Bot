# Sdd-Orchestrator-Bot

A personal **AI development gateway**: one bot that bridges your clients (Telegram and a Flet GUI) to a CLI coding agent — owning the agent lifecycle, the interactive permissions, and the session continuity so you can drive real software work from a phone.

![Python](https://img.shields.io/badge/python-3.13-3776AB?logo=python&logoColor=white)
![Tests](https://img.shields.io/badge/tests-1114%20passing-brightgreen)
![mypy](https://img.shields.io/badge/mypy-strict-2A6DB2)
![ruff](https://img.shields.io/badge/ruff-clean-261230?logo=ruff&logoColor=white)

## What it is

Working with a coding agent normally means sitting at a terminal. This project removes that constraint: the server runs on a Linux VPS, the app lives on your phone, and a single bot is the bridge between them. It receives prompts, streams the agent's progress back, forwards permission requests for you to approve, and keeps every conversation resumable across restarts.

The bot is deliberately **frontend-agnostic**. All business logic lives in a pure `services/` core; Telegram (`frontends/telegram/`) and the Flet app (`flet_app/`) are thin adapters that implement the core's ports. The same `build_prompt_stack()` composition root feeds both, so the two clients share one orchestration path instead of diverging. Swapping or adding a frontend — or the AI backend behind the `AIBackend` port — never touches the domain.

Today the implemented agent backend is **OpenCode** (HTTP server by default, CLI as a rollback). The `AIBackend` port is the seam where another CLI agent (Codex, Claude Code, …) would plug in without changing the core.

### Telegram at a glance

| Command | What it does |
|---|---|
| `/status` | Active session, model, uptime |
| `/model <alias>` | Switch model (alias or catalog id) |
| `/session new · list · switch · delete` | Manage named sessions |
| `/config` | Per-chat model, timeout, workdir, provider |
| `/cancel` | Cancel the running prompt |
| `/open <prompt>` | Send an explicit prompt |
| `/pr <title>` | Open a GitHub PR with `gh` |
| `/update` | `git pull` + graceful restart (exit code `42`) |
| _(free text)_ | Prompt the active session |

The full command reference lives in [DOCUMENTACION.md](DOCUMENTACION.md) §7.

## Screenshots

> Placeholders — capture these three screens from the running app and drop them into `docs/screenshots/` with these exact names.

![Chat with legible Markdown rendering](docs/screenshots/chat.png)

![Session drawer with recent sessions](docs/screenshots/drawer.png)

![Directory explorer with CRUD and recent folders](docs/screenshots/explorer.png)

## Features

- **Chat with live streaming and legible Markdown.** Telegram renders MarkdownV2 with automatic plain-text fallback and long-message splitting; the Flet UI shows an animated assistant tail. Optional voice-note transcription (Whisper) when `OPENAI_API_KEY` is set.
- **Sessions: switch, recent, new.** Named sessions per conversation, bucketed by recency in the drawer, with per-session workdirs. State is persisted and thread-safe.
- **Interactive permissions on both frontends.** An agent `ask` becomes inline buttons in Telegram or a modal dialog in Flet. Human waits are unbounded by default and don't consume the prompt timeout.
- **Directory explorer.** Browse, create, rename, delete, and select workdirs from a breadcrumb view, plus the five most recent folders per conversation — guarded by a home perimeter and symlink-escape checks.
- **Edit mode.** Rename bot-bound session titles inline from the drawer; failures keep edit mode on so you can retry.
- **Model picker backed by the real catalog.** The picker reads the agent's live `providers` list and shows only agent-executable models, grouped by provider, with the conversation's active model marked.
- **Server lifecycle with a supervisor.** The bot adopts a healthy `opencode serve` or spawns its own, waits on `/global/health`, and supervises it; shutdown only stops instances it owns, so the bot and GUI can share one server.
- **Two frontends, one core.** Telegram in production and a mobile-first Flet 1.0.3 GUI, both composed from the same services and ports.

## Architecture

```text
  Telegram (phone) ──┐
                     ├── ports ──►  CORE (services/)  ──►  AIBackend  ──►  OpenCode server / CLI
  Flet GUI (phone) ──┘             domain · orchestration · persistence
```

The rule that holds the design together is a single, testable one:

> `services/` never imports `telegram`, `flet`, `frontends/`, `handlers/`, or `bot.py`.

It is not a convention — `tests/test_core_isolation_boundary.py` imports every core module in a subprocess and asserts Telegram/Flet stay out of `sys.modules`, then statically scans `services/**` for forbidden imports. A future "just import telegram in the core" fails the suite.

### Three layers

| Layer | Lives in | Responsibility | May import |
|---|---|---|---|
| Core | `services/`, `core_config.py`, `formatting/`, `opencode/`, `persistence/` | Domain, orchestration, persistence, contracts | stdlib + `core_config` |
| Frontends | `frontends/telegram/`, `flet_app/`, `handlers/`, `bot.py` | Transport, UI, auth, composition | the whole core |
| API (future) | — | Expose the core over HTTP | the core |

### Ports

| Port | Contract | Telegram | Flet |
|---|---|---|---|
| `ChatView` + `CoreStrings` | `services/chat_view.py` | `frontends/telegram/chat_view.py` | `flet_app/flet_chat_view.py` |
| `PermissionPort` / `PermissionPresenter` | `services/permissions.py` | `frontends/telegram/permission_presenter.py` | `flet_app/flet_permission_presenter.py` |
| `AIBackend` | `services/ai_backend.py` | OpenCode server / CLI backend | same |
| `FrontendGate` | `services/frontend_gate.py` | `TelegramGate` (allow-list) | `AllowAllGate` |
| `ConversationKey` | `services/conversation.py` | `telegram:{chat_id}` | `flet:local` |

`bot.py` and `flet_app/main.py` are the composition roots: each calls `build_prompt_stack()` and injects its own adapters. The core never knows either frontend exists.

Design rationale, rejected alternatives, and the `sessions.json` migration are documented in:

- [ADR 0001 — Frontend-agnostic core](docs/adr/0001-frontend-agnostic-core.md)
- [ADR 0002 — Opaque conversation key](docs/adr/0002-conversation-key.md)

## Stack

- **Python 3.13** (declared `requires-python >= 3.11`; CI covers 3.11–3.13)
- **python-telegram-bot 21+** for the Telegram transport
- **Flet 1.0.3** for the mobile-first GUI (`[gui]` extra)
- **httpx** for the OpenCode HTTP/SSE client
- **SQLite** for fast, read-only session reads directly from `opencode.db` (with CLI fallback)
- **pytest + pytest-asyncio** for tests, **mypy --strict** for types, **ruff** for lint/format
- **OpenCode** as the agent backend (server mode by default, CLI rollback)

## Quick start

**Prerequisites:** Python 3.11+, Node.js + the OpenCode CLI (`npm install -g opencode-ai`), a Telegram bot token from [@BotFather](https://t.me/BotFather), and your chat id (run `get_chat_id.py`). The Flet app needs the `gui` extra.

Use the interpreter inside the virtualenv for every command: `.venv\Scripts\python.exe` on Windows, `.venv/bin/python` on POSIX.

### POSIX

```bash
git clone https://github.com/JosedeJesusAndrade/Sdd-Orchestrator-Bot.git
cd Sdd-Orchestrator-Bot

python3 -m venv .venv
source .venv/bin/activate
.venv/bin/python -m pip install -e ".[dev,gui]"

cp .env.example .env          # then fill in TELEGRAM_BOT_TOKEN, ALLOWED_CHAT_IDS, …

.venv/bin/python bot.py                 # Telegram bot
.venv/bin/flet run flet_app/main.py     # Flet GUI
.venv/bin/python -m pytest              # test suite
```

### Windows (PowerShell)

```powershell
git clone https://github.com/JosedeJesusAndrade/Sdd-Orchestrator-Bot.git
cd Sdd-Orchestrator-Bot

python -m venv .venv
.\.venv\Scripts\Activate.ps1
.\.venv\Scripts\python.exe -m pip install -e ".[dev,gui]"

Copy-Item .env.example .env   # then fill in TELEGRAM_BOT_TOKEN, ALLOWED_CHAT_IDS, …

.\.venv\Scripts\python.exe bot.py                # Telegram bot
.\.venv\Scripts\flet.exe run flet_app/main.py    # Flet GUI
.\.venv\Scripts\python.exe -m pytest             # test suite
```

`pip install -e ".[dev]"` is enough if you only need the Telegram bot and the tooling; add `[gui]` for the Flet app. The server mode also requires `OPENCODE_SERVER_PASSWORD` (Basic auth); set `OPENCODE_BACKEND=cli` to run without it. Every variable is documented in [`.env.example`](.env.example) and [DOCUMENTACION.md](DOCUMENTACION.md) §3.

## Project status

- ✅ **1,114 tests passing** (2 POSIX-only tests skipped on Windows)
- ✅ **mypy --strict:** 0 errors across 196 files
- ✅ **ruff:** lint and format clean
- ✅ **3 SDD changes archived:** `core-isolation` (frontend-agnostic core + ADRs), `flet-data-layer` (services, DTOs, headless controllers), `flet-ui` (chat-first mobile shell, drawer, explorer, edit mode, model picker)

This is a **personal project** maintained by a single author. It is production-shaped (supervised server, enforced architecture boundary, strict typing) but built for one operator's workflow.

## Documentation

- [DOCUMENTACION.md](DOCUMENTACION.md) — deep technical reference: architecture, configuration, sessions, backends, permissions, tests, troubleshooting
- [docs/adr/](docs/adr/) — Architecture Decision Records with context, alternatives, and consequences

## License

Personal project. No license file is included in the repository; contact the author before redistributing or using the code in production.
