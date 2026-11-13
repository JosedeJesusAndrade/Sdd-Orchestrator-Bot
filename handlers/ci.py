"""CI/CD handlers — PR creation, self-update, and project switching.

These commands enable the full CI/CD pipeline from the street:
  /pr <title> — Create a GitHub PR with CHANGELOG.md as body
  /update    — Self-restart bot with exit code 42 for git pull + reload
  /wdir [project] — List or switch OpenCode workdir (project folder)
"""

from typing import cast

from telegram import Update
from telegram.ext import ContextTypes

from frontends.telegram.container import AppContainer
from frontends.telegram.keys import telegram_key
from handlers import authorized
from handlers._guards import require_chat
from services.pull_request_service import (
    NothingToCommitError,
    PullRequestError,
)
from services.workdir_service import (
    AmbiguousWorkdirError,
    ProjectsSnapshot,
    ResolveResult,
    WorkdirNoProjectsError,
    WorkdirNotFoundError,
    WorkdirPermissionDeniedError,
    WorkdirRootNotFoundError,
)
from telegram_config import CONTAINER_KEY


def _get_container(context: ContextTypes.DEFAULT_TYPE) -> AppContainer:
    """Extract the typed AppContainer from PTB context."""
    return cast(AppContainer, context.application.bot_data[CONTAINER_KEY])


_PR_STAGE_ERRORS: dict[str, str] = {
    "branch": "❌ Error creando rama:\n`{err}`",
    "add": "❌ Error en git add:\n`{err}`",
    "commit": "❌ Error en commit:\n`{err}`",
    "push": "❌ Error en push:\n`{err}`",
    "pr": "❌ Error creando PR:\n`{err}`",
}


def _format_pr_error(error: PullRequestError) -> str:
    """Map a typed stage error to the Telegram copy."""
    template = _PR_STAGE_ERRORS.get(error.stage, _PR_STAGE_ERRORS["pr"])
    return template.format(err=error.stderr)


@authorized
async def pr_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create a GitHub PR: /pr <title>

    Thin adapter: parse the title, delegate to ``PullRequestService`` and render
    the typed result/error. The git/gh pipeline and the CHANGELOG lifecycle live
    in ``services/pull_request_service.py`` so other frontends can reuse them.
    """
    chat_id = require_chat(update).id
    key = telegram_key(chat_id)
    container = _get_container(context)
    sender = container.message_sender
    service = container.pull_request_service

    title = " ".join(context.args) if context.args else "Update from Telegram"
    branch_name = service.new_branch_name(title)

    await sender.send_plain(chat_id, f"📦 Creando PR: *{title[:80]}*...\nRama: `{branch_name}`")

    try:
        result = await service.create(key, title, branch_name=branch_name)
    except NothingToCommitError:
        await sender.send_plain(chat_id, "⚠️ No hay cambios para commitear.")
        return
    except PullRequestError as error:
        await sender.send_plain(chat_id, _format_pr_error(error))
        return

    await sender.send_plain(
        chat_id,
        f"✅ PR creada exitosamente.\n\n📝 *Título:* {result.title[:100]}\n"
        f"🔗 {result.url or result.output[:200]}",
    )


@authorized
async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Self-restart the bot: /update

    Triggers a GRACEFUL shutdown with exit code 42.
    The full cleanup sequence runs (session save, connectivity cancel,
    app.stop/shutdown), THEN sys.exit(42) is called.
    The launcher.bat babysitter detects code 42 and does git pull + restart.
    """
    chat_id = require_chat(update).id
    container = _get_container(context)
    sender = container.message_sender

    await sender.send_plain(
        chat_id, "🔄 Recibido. Iniciando apagado controlado...\nEl bot volverá en ~5 segundos."
    )

    from utils.logging import get_module_logger

    logger = get_module_logger(__name__)
    logger.info(
        "Auto-restart requested",
        extra={
            "event": "auto_restart",
            "exit_code": 42,
        },
    )

    # Trigger graceful shutdown via the shared stop_event + exit_code
    # The bot will: save sessions → cancel monitor → stop polling → shutdown → sys.exit(42)
    app = context.application
    app.bot_data["exit_code"][0] = 42
    app.bot_data["stop_event"].set()


def _format_listing(snapshot: ProjectsSnapshot) -> str:
    """Render a projects snapshot (the Telegram view)."""
    error = snapshot.error
    if isinstance(error, WorkdirRootNotFoundError):
        return f"❌ Directorio no encontrado: `{error.root}`"
    if isinstance(error, WorkdirPermissionDeniedError):
        return "❌ Sin permisos para listar directorios."
    if isinstance(error, WorkdirNoProjectsError):
        return "⚠️ No se encontraron proyectos con `.git` en el directorio."

    lines = ["⚙️ *Proyectos disponibles:*\n"]
    for entry in snapshot.entries:
        marker = "🟢" if entry.is_current else "  "
        lines.append(f"{marker} `{entry.alias}` → {entry.name}")
    return "\n".join(lines)


def _format_switch(result: ResolveResult) -> str:
    """Render a resolution result (the Telegram view)."""
    error = result.error
    if isinstance(error, WorkdirRootNotFoundError):
        return f"❌ Directorio no encontrado: `{error.root}`"
    if isinstance(error, WorkdirPermissionDeniedError):
        return "❌ Sin permisos para listar directorios."
    if isinstance(error, WorkdirNoProjectsError):
        return "⚠️ No se encontraron proyectos con `.git` en el directorio."
    if isinstance(error, AmbiguousWorkdirError):
        match_list = ", ".join(error.candidates)
        return f"⚠️ Múltiples coincidencias: {match_list}. Sé más específico."
    if isinstance(error, WorkdirNotFoundError):
        return f"❌ Proyecto no encontrado: `{result.query}`"

    assert result.entry is not None  # every failure path is handled above
    workdir = str(result.entry.path)
    return f"✅ Workdir cambiado a:\n`{workdir}`\n\nEl próximo prompt usará este proyecto."


@authorized
async def wdir_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List or switch OpenCode workdir: /wdir [project]

    Thin adapter: parse the args, delegate to ``WorkdirService`` and render the
    returned DTO/error. The discovery, aliasing, matching and persistence live
    in ``services/workdir_service.py`` so other frontends can reuse them.
    """
    chat_id = require_chat(update).id
    key = telegram_key(chat_id)
    container = _get_container(context)
    sender = container.message_sender
    service = container.workdir_service

    if not context.args:
        text = _format_listing(await service.list(key))
    else:
        text = _format_switch(await service.switch(key, " ".join(context.args)))

    await sender.send_plain(chat_id, text)
