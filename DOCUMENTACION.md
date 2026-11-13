# Sdd-Orchestrator-Telegram-Bot — Documentación técnica

Documentación del estado real del repositorio en `main` (HEAD `79a7eb1`).
Está dirigida a quien necesita mantener, extender o desplegar el bot.

> **Nombre del proyecto.** El directorio local, el remoto de GitHub
> (`JosedeJesusAndrade/Sdd-Orchestrator-Telegram-Bot`) y la clave de proyecto de
> Engram conservan el nombre histórico. El [ADR 0001](docs/adr/0001-frontend-agnostic-core.md)
> documenta el rename previsto a `Sdd-Orchestrator-Bot`: el código ya es un core
> frontend-agnóstico y Telegram es solo uno de sus frontends.

---

## Tabla de contenidos

1. [Introducción](#1-introducción)
2. [Arquitectura](#2-arquitectura)
3. [Configuración](#3-configuración)
4. [Modelos](#4-modelos)
5. [Sesiones](#5-sesiones)
6. [Backends de OpenCode](#6-backends-de-opencode)
7. [Comandos](#7-comandos)
8. [Permisos](#8-permisos)
9. [Frontend Flet](#9-frontend-flet)
10. [Autenticación](#10-autenticación)
11. [Logging](#11-logging)
12. [Tests](#12-tests)
13. [CI/CD y despliegue](#13-cicd-y-despliegue)
14. [Seguridad](#14-seguridad)
15. [Troubleshooting](#15-troubleshooting)
16. [Decisiones y ADRs](#16-decisiones-y-adrs)
17. [Notas para Linux/VPS](#17-notas-para-linuxvps)
18. [Apéndice: estructura del repositorio](#18-apéndice-estructura-del-repositorio)

---

## 1. Introducción

### 1.1 Propósito

Este proyecto es un **core frontend-agnóstico** para ejecutar prompts de
desarrollo contra un agente **OpenCode**. Expone ese core mediante dos
frontends:

- **Telegram** (`frontends/telegram/`, entry point `bot.py`), orientado a uso
  móvil y operación remota.
- **Flet** (`flet_app/`), una GUI de escritorio/Android de un solo usuario.

El core vive en `services/` y no conoce Telegram ni Flet. El agente OpenCode se
consume de dos formas:

- **Servidor** (`OPENCODE_BACKEND=server`, default): proceso `opencode serve`
  por HTTP/SSE, con permisos interactivos y streaming en vivo.
- **CLI** (`OPENCODE_BACKEND=cli`): `opencode run --format json` por prompt,
  como rollback sin diálogos de permiso.

El bot **no integra MCPs por sí mismo**. Context7, Engram, Notion y cualquier
otro MCP son configuración del agente OpenCode (`opencode.json`); el bot solo
transporta prompts y respuestas.

### 1.2 Casos de uso

- Enviar prompts SDD desde Telegram sin abrir una terminal.
- Gestionar varias sesiones por conversación (`/session`).
- Cambiar modelo por chat (`/model`, `/config`).
- Crear Pull Requests desde Telegram (`/pr`).
- Reiniciar el bot tras un `git pull` (`/update` + `launcher.bat`).
- Enviar notas de voz y transcribirlas con OpenAI Whisper (opcional).
- Aprobar o rechazar permisos del agente desde Telegram o Flet.
- Consultar el historial de conversaciones desde la GUI de Flet.

### 1.3 Stack real

| Componente | Tecnología | Observaciones |
|---|---|---|
| Lenguaje | Python `>=3.11` | `pyproject.toml` |
| Bot | `python-telegram-bot>=21.0` | Probado con 22.8 |
| GUI | `flet>=1.0` (extra `gui`) | Probado con 1.0.3 |
| HTTP/SSE | `httpx>=0.28` | Cliente del servidor OpenCode |
| Orquestador | OpenCode (`server` o `cli`) | El binario se autodetecta |
| Voz | `openai>=1.0.0` (opcional) | Whisper `whisper-1` |
| Lint | `ruff` | Reglas `E, F, I, N, W, UP, B, C4, SIM, T20` |
| Tipos | `mypy==2.3.1` | `strict = true`, con overrides para tests y librerías |
| Tests | `pytest` + `pytest-asyncio` | `asyncio_mode = "auto"` |
| CI | GitHub Actions | Python 3.11, 3.12 y 3.13 |

---

## 2. Arquitectura

### 2.1 Tres capas y una regla de importación

| Capa | Ubicación | Responsabilidad | Puede importar |
|---|---|---|---|
| Core | `services/`, `core_config.py`, `formatting/`, `opencode/`, `persistence/` | Dominio, orquestación, persistencia y puertos | stdlib y módulos del core (`core_config`, `formatting`, `opencode`, `persistence`, `utils`) |
| Frontends | `frontends/telegram/`, `flet_app/`, `handlers/`, `bot.py` | Transporte, UI, autenticación y composición | todo el core |
| API HTTP (futura) | — | Exponer el core por HTTP | el core |

La regla que sostiene el diseño es verificable:

> `services/` **nunca** importa `telegram`, `flet`, `frontends/`, `handlers/`
> ni `bot.py`.

Se comprueba en `tests/test_core_isolation_boundary.py` con dos mecanismos:
importación de cada módulo del core en un subproceso (sin `telegram`/`flet` en
`sys.modules`) y escaneo estático de `services/**`.

### 2.2 Composition roots

`bot.py` y `flet_app/main.py` son los *composition roots*. Ambos llaman a
`services.composition.build_prompt_stack()` y le inyectan sus propios
adaptadores:

```text
Telegram ──> bot.py ───────────────┐
                                   ├──> build_prompt_stack() ──> CoreStack
Flet ─────> flet_app/main.py ──────┘
```

`CoreStack` (`services/composition.py`) contiene el grafo compartido:
`SessionStore`, `AIProviderFactory`, `PromptService`, `StatusService`,
`HealthService`, `SessionService`, `SettingsService`, `WorkdirService`,
`PullRequestService`, `TranscriptionService`, `AgentSessions`,
`ConversationsService`, `start_time` y, solo en modo servidor, el manager, el
cliente HTTP, el `PermissionPort` y el `PermissionRouter`.

### 2.3 Puertos del core

| Puerto | Contrato | Implementación Telegram | Implementación Flet |
|---|---|---|---|
| `ChatView` + `CoreStrings` | `services/chat_view.py` | `frontends/telegram/chat_view.py` | `flet_app/flet_chat_view.py` |
| `ProgressHandle` | `services/chat_view.py` | `_TelegramProgressHandle` | `FletProgressHandle` |
| `PermissionPort` / `PermissionPresenter` | `services/permissions.py` | `frontends/telegram/permission_presenter.py` | `flet_app/flet_permission_presenter.py` |
| `AIBackend` | `services/ai_backend.py` | `opencode_server_backend.py` / `opencode_cli_backend.py` | igual |
| `FrontendGate` | `services/frontend_gate.py` | `TelegramGate` (`frontends/telegram/gate.py`) | `AllowAllGate` |
| `ConversationKey` | `services/conversation.py` | `frontends/telegram/keys.py` | `flet_app/keys.py` |
| `SessionStorePort` | `services/session_store.py` | `SessionStore` | `SessionStore` |
| `ProviderFactoryPort` | `services/ai_provider_factory.py` | `AIProviderFactory` | `AIProviderFactory` |

`PromptService` depende de `ChatView`, no de Telegram. El `ProgressHandle` es
opaco: el core no ve `message_id` ni operaciones de edición de la plataforma.

### 2.4 Flujo de un prompt

Con el backend por defecto (`server`):

1. Telegram entrega el `Update`; `@authorized` valida el chat.
2. El handler llama a `PromptService.execute(conversation_id, prompt, view)`.
3. `PromptService` verifica que no haya otro prompt en ejecución para esa
   conversación.
4. Lee modelo (`SessionStore.get_model`) y sesión activa
   (`get_active_session`).
5. Abre el placeholder de progreso con `ChatView.start_progress()`.
6. Crea un backend **por conversación** con `AIProviderFactory.create()`.
7. `OpenCodeServerBackend.execute()`:
   - asegura el servidor (`manager.ensure_running()`);
   - crea sesión si no hay `session_id`;
   - abre el stream SSE **antes** de enviar el prompt;
   - envía el prompt y acumula texto del asistente;
   - reenvía `permission.asked` al `PermissionRouter`;
   - publica progreso incremental vía `on_progress`.
8. `PromptService._deliver_response()` limpia el output
   (`formatting.markdown.clean_opencode_output` + `_assemble_response`) y lo
   entrega con `ChatView.send()`.
9. Persiste el `session_id` real si era una sesión nueva y incrementa
   `prompt_count`.

Con `OPENCODE_BACKEND=cli`, el paso 7 usa `OpenCodeCLIBackend`: un subproceso
`opencode run --format json` y un ticker de progreso cada 5 segundos.

### 2.5 Flujo de una nota de voz

1. El handler descarga el `.ogg` a un archivo temporal.
2. `TranscriptionService` valida que esté habilitado (`OPENAI_API_KEY`).
3. `WhisperTranscriber` llama a OpenAI (`whisper-1`, `response_format="text"`).
4. El texto se ejecuta como un prompt normal.
5. El archivo temporal se elimina siempre en `finally`.

### 2.6 Patrones y principios

| Patrón / principio | Implementación real |
|---|---|
| Dependency Injection | `AppContainer` de Telegram en `frontends/telegram/container.py`; `CoreStack` en `services/composition.py` |
| Factory | `AIProviderFactory.create()` crea una instancia **nueva** por conversación; no cachea |
| Strategy | El provider se selecciona por el setting `provider`; hoy solo existe `opencode` |
| Adapter | `TelegramAdapter` implementa `BotPort`; `TelegramChatView` implementa `ChatView` |
| Decorator | `@authorized` resuelve el gate desde `bot_data` |
| Protocol | `AIBackend`, `BotPort`, `ChatView`, `FrontendGate`, `PermissionPort` |
| Single Responsibility | Servicios de negocio en `services/`; handlers delgados en `handlers/` |

### 2.7 Componentes principales

| Componente | Rol | Ubicación |
|---|---|---|
| `bot.py` | Entry point de Telegram, registro de handlers, lifecycle | raíz |
| `flet_app/main.py` | Entry point de Flet, composición y warm-up | `flet_app/` |
| `build_prompt_stack()` | Composition root del core | `services/composition.py` |
| `AppContainer` | Payload DI de Telegram | `frontends/telegram/container.py` |
| `PromptService` | Orquestación de prompts, cancelación, entrega | `services/prompt_service.py` |
| `SessionStore` | Estado de sesiones, settings, migración | `services/session_store.py` |
| `OpenCodeServerBackend` | Transporte HTTP/SSE con permisos y streaming | `services/opencode_server_backend.py` |
| `OpenCodeCLIBackend` | Rollback por subproceso | `services/opencode_cli_backend.py` |
| `PermissionRouter` | Rendezvous de decisiones entre backend y UIs | `services/permission_router.py` |
| `MessageSender` | MarkdownV2, fallback y troceo de Telegram | `frontends/telegram/message_sender.py` |
| `formatting/markdown.py` | Limpieza neutral de output (sin I/O) | `formatting/` |
| `handlers/` | Adaptadores delgados de comandos y mensajes | `handlers/` |

### 2.8 Formateo y troceo del output

- **Neutral** (`formatting/markdown.py`): `clean_opencode_output()` quita
  ANSI, headers `> agente · modelo`, tool traces (incluida la variante cp1252
  descrita por sus code points, sin incrustar bytes corruptos), líneas de
  auto-rechazo y líneas vacías repetidas. `_filter_stderr()` y
  `_assemble_response()` combinan stdout/stderr.
- **Telegram** (`frontends/telegram/formatting.py`): `split_message(text, max_len)`
  parte el texto; `max_len` es obligatorio y lo inyecta el frontend (4000,
  `TELEGRAM_MAX_MESSAGE_LENGTH`).
- `MessageSender.send_formatted()` intenta MarkdownV2 y, si falla, reintenta en
  texto plano sin `*`, `` ` ``, `#` ni `_`.

---

## 3. Configuración

### 3.1 Dos mitades, sin `SystemExit` en import

La configuración está partida en dos módulos:

- **`core_config.py`**: paths, logging, OpenCode, OpenAI, modelos y sesiones.
  Importarlo **no** falla y **no** configura logging.
- **`telegram_config.py`**: token y `ALLOWED_CHAT_IDS`. `load_telegram_config()`
  valida la allow-list de forma explícita y falla con `SystemExit(1)` si está
  vacía o no es numérica. Se llama desde `bot.py` antes de construir la app.

`python -c "import services.composition"` funciona sin variables de Telegram.
El detalle está en [ADR 0001](docs/adr/0001-frontend-agnostic-core.md).

### 3.2 Variables de entorno

La plantilla comentada es `.env.example`.

| Variable | Obligatoria | Default | Descripción |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Sí en la práctica | `""` | Token de @BotFather. `load_telegram_config()` no lo valida; PTB falla al construir la app si es inválido. |
| `ALLOWED_CHAT_IDS` | Sí | `""` | CSV de enteros. Una lista vacía o no numérica es fatal. |
| `OPENCODE_BACKEND` | No | `server` | `server` o `cli`. Cualquier otro valor falla en `build_prompt_stack()`. |
| `OPENCODE_WORKDIR` | No | raíz del repo (`BASE_DIR`) | Workdir por defecto; cada chat puede sobreescribirlo. |
| `OPENCODE_TIMEOUT` | No | `300` | Timeout del prompt en segundos. En modo servidor es un deadline **idle**. |
| `OPENCODE_CMD` | No | autodetección | Ruta al binario de OpenCode (`PATH`, `opencode.cmd`, npm en Windows). |
| `OPENCODE_PERMISSION_TIMEOUT` | No | `0` | `0` = esperar al humano sin límite; `>0` = auto-rechazar tras N segundos. |
| `OPENCODE_SERVER_HOST` | No | `127.0.0.1` | Solo loopback (`127.0.0.1`, `::1`, `localhost`) salvo opt-in. |
| `OPENCODE_SERVER_PORT` | No | `4096` | Puerto del servidor. |
| `OPENCODE_SERVER_PASSWORD` | Sí en modo `server` | `""` | Basic auth (usuario `opencode`). Vacío = fallo al arrancar. |
| `OPENCODE_SERVER_BINARY` | No | `OPENCODE_CMD` | Binario para `opencode serve`. |
| `OPENCODE_SERVER_ALLOW_REMOTE` | No | deshabilitado | `1`/`true`/`yes` permite un host no loopback. |
| `OPENAI_API_KEY` | No | `""` | Habilita transcripción de voz. |
| `BOT_LOG_FORMAT` | No | `text` | `text` (key=value) o `json`. Un valor inválido cae a `text` con warning. |
| `BOT_LOG_FILE` | No | `<repo>/bot.log` | Archivo de log rotativo (5 MB, 3 backups). |
| `EXPLORE_DB` | No | deshabilitado | `1`/`true`/`yes` explora el esquema de `opencode.db` al arrancar. |

Notas:

- `OPENCODE_TIMEOUT` y `OPENCODE_PERMISSION_TIMEOUT` se convierten con `int()`;
  un valor no numérico falla al importar `core_config`.
- El servidor escucha solo en loopback porque el bot es su único cliente. Un
  host distinto lanza `OpenCodeServerConfigError` salvo
  `OPENCODE_SERVER_ALLOW_REMOTE=1`.
- `OPENCODE_SERVER_PASSWORD` nunca se escribe en los logs.

### 3.3 Allow-list de chat IDs

El bot solo responde a chats incluidos en `ALLOWED_CHAT_IDS`. Los mensajes de
otros chats se ignoran silenciosamente (se registra `event=unauthorized`).

Para agregar un chat:

1. Habla con el bot desde el chat deseado.
2. Ejecuta `python get_chat_id.py` y envía `/start`.
3. Agrega el ID a `ALLOWED_CHAT_IDS`.
4. Reinicia el bot.

Gotcha: `get_chat_id.py` carga `TELEGRAM_BOT_TOKEN` desde
`Path(__file__).resolve().parent.parent / ".env"`, es decir, el directorio
**padre** del repositorio. Si tu `.env` está en la raíz del repo, exporta la
variable en el entorno o ajusta esa ruta antes de ejecutarlo.

### 3.4 Configuración por chat

`/config` lee y escribe settings por conversación. Las claves válidas son:

| Clave | Validación | Persistencia |
|---|---|---|
| `model` | `resolve_model()` (alias o `provider/model`) | `settings.model` |
| `timeout` | entero en `(0, 86400]` | `settings.timeout` |
| `workdir` | ruta tal cual | `settings.workdir` |
| `provider` | string libre; si no está registrado, el prompt falla al crear el backend | `settings.provider` |

Los settings se guardan en `sessions.json` bajo la clave `settings` de cada
conversación. **Importante:** el modelo que usa la ejecución se lee con
`SessionStore.get_model()` desde la clave **superior** `model`; `/config model`
escribe en `settings.model` y, en este HEAD, no cambia el modelo efectivo del
prompt. Usa `/model <alias>` para eso. `/config` tampoco refleja
necesariamente el modelo fijado con `/model`.

---

## 4. Modelos

### 4.1 Alias reales

`core_config.MODEL_ALIASES` define exactamente:

| Alias | Modelo resuelto |
|---|---|
| `pro` | `deepseek/deepseek-v4-pro` |
| `flash` | `deepseek/deepseek-flash` |
| `deepseek-v4-pro` | `deepseek/deepseek-v4-pro` |
| `deepseek-flash` | `deepseek/deepseek-flash` |
| `m3` | `minimax/MiniMax-M3` |
| `m27` | `minimax/MiniMax-M2.7` |
| `m27-fast` | `minimax/MiniMax-M2.7-highspeed` |
| `minimax-m3` | `minimax/MiniMax-M3` |
| `minimax-m27` | `minimax/MiniMax-M2.7` |
| `minimax-m27-highspeed` | `minimax/MiniMax-M2.7-highspeed` |

`DEFAULT_MODEL` es `deepseek/deepseek-flash`. `resolve_model()` devuelve el
valor tal cual si no coincide con ningún alias, y el backend lo pasa al agente
con formato `provider/model`.

### 4.2 Agregar un modelo

Si el agente OpenCode ya soporta el modelo, no hace falta código: usa
`/model provider/model` o agrega un alias en `core_config.MODEL_ALIASES`.

### 4.3 Agregar un provider

1. Implementa el protocolo `AIBackend` (`services/ai_backend.py`), incluido el
   atributo `supports_live_progress` y el parámetro `on_progress` de
   `execute()`.
2. Regístralo en `AIProviderFactory.register(nombre, clase, **kwargs)`.
3. Ajusta `services/composition.py` para registrar el provider.
4. Selecciónalo con el setting `provider` (hoy `/config provider`).

`AIProviderFactory.create()` siempre construye una instancia nueva; no hay
singletons compartidos entre conversaciones.

---

## 5. Sesiones

### 5.1 Concepto

Cada conversación (`telegram:{chat_id}` o `flet:local`) mantiene sesiones
nombradas. Una sola está activa. Una sesión puede existir sin ID real de
OpenCode (`id: null`): se materializa en el próximo prompt.

### 5.2 `SessionStore` — internals

`services/session_store.py` es la única fuente de verdad del estado de
sesiones. Usa:

- `asyncio.Lock` para serializar el acceso concurrente;
- caché TTL de 2 segundos para evitar lecturas de disco por handler;
- escritura atómica (`sessions.json.tmp` → `replace`);
- migración idempotente de claves legadas;
- backup `sessions.json.bak` antes de la primera reescritura por migración;
- copia de un JSON corrupto a `sessions.json.corrupt-<timestamp>` y arranque
  limpio.

API principal:

```python
get_active_session(conversation_id) -> SessionInfo | None
create_session(conversation_id, name) -> SessionInfo
switch_session(conversation_id, name) -> SessionInfo
delete_session(conversation_id, name) -> str | None
list_sessions(conversation_id) -> list[SessionInfo]
get_model / set_model
increment_prompt_count(conversation_id) -> int
update_session_id / update_session_title
get_chat_setting / set_chat_setting / get_all_chat_settings
reset_session(conversation_id)
```

### 5.3 Formato de `sessions.json`

```json
{
  "telegram:8664220427": {
    "active": "bot_telegram",
    "model": "deepseek/deepseek-flash",
    "settings": {
      "timeout": 900,
      "workdir": "C:/proyectos/mi-repo",
      "provider": "opencode"
    },
    "sessions": {
      "bot_telegram": {
        "id": "ses_20477ac36ffec2aYyBiD359Zfp",
        "title": "OpenCode remoto con bot de Telegram",
        "created": "2026-05-14T06:39:51+00:00",
        "last_used": "2026-05-14T16:16:07+00:00",
        "prompt_count": 11
      }
    }
  }
}
```

| Campo | Descripción |
|---|---|
| `active` | Nombre de la sesión activa |
| `model` | Modelo efectivo de la conversación (lo usa `PromptService`) |
| `settings` | Settings por chat de `/config` y `/wdir` (`model`, `timeout`, `workdir`, `provider`) |
| `id` | ID real de OpenCode (`ses_...`) o `null` |
| `title` | Título legible |
| `created` / `last_used` | ISO 8601 UTC |
| `prompt_count` | Prompts ejecutados |

### 5.4 Captura del ID real

El bot no inventa IDs y **no** consulta `opencode session list` al terminar:

- **Server:** crea la sesión con `POST /session` y recibe el `id` explícito.
- **CLI:** `opencode run --format json` emite NDJSON con `sessionID`; si el
  parseo falla, se recupera con la regex `ses_...` del stdout.

`PromptService._persist_session_id()` guarda el ID solo si la sesión activa aún
no tenía uno.

### 5.5 Discovery y adopción

- `/session discover`: lista sesiones de OpenCode con su estado de adopción
  (máximo 15 filas en la vista de Telegram).
- `/session adopt <id> <nombre>`: valida el ID contra el catálogo exacto
  (`OpenCodeSessions.get_session` sobre la DB), crea la sesión local y guarda
  el ID. El nombre debe cumplir `^[a-zA-Z0-9_-]{1,30}$`.
- `/session list`: mezcla sesiones del bot y sesiones de OpenCode no adoptadas.

### 5.6 Continuidad

`PromptService` lee la sesión activa. Si tiene `real_id`, continúa con
`--continue --session <id>` (CLI) o usa ese `session_id` (server). Si no lo
tiene, inicia una sesión nueva. **No existe expiración por inactividad**: no hay
TTL de 30 minutos ni lógica equivalente.

### 5.7 `/new`

`SessionStore.reset_session()` limpia el `id`, reinicia `prompt_count` y
conserva el nombre y el modelo. El próximo prompt crea una sesión nueva en
OpenCode.

### 5.8 Borrado

`SessionService.delete()` borra la sesión local y, si tenía `real_id`, ejecuta
`opencode session delete <id>` con timeout de
`INTERNAL_SUBPROCESS_TIMEOUT` (10 s). Si era la activa, cambia a la siguiente; si
no quedan, crea `default`.

---

## 6. Backends de OpenCode

### 6.1 Selección

`OPENCODE_BACKEND` decide el transporte en `build_prompt_stack()`:

- `server` (default): registra `OpenCodeServerBackend`.
- `cli`: registra `OpenCodeCLIBackend`.

Cualquier otro valor lanza `ValueError` al construir el core.

### 6.2 Modo servidor (default)

`OpenCodeServerBackend` habla con `opencode serve`:

- `OpenCodeServerManager.ensure_running()` adopta un servidor sano o lanza uno
  y espera `GET /global/health`.
- Basic auth con usuario `opencode` y `OPENCODE_SERVER_PASSWORD`.
- Loopback obligatorio salvo `OPENCODE_SERVER_ALLOW_REMOTE`.
- Cada request lleva `?directory=<workdir de la conversación>`; el servidor
  publica sesiones, eventos y permisos por directorio.
- El stream SSE se abre **antes** de enviar el prompt para no perder un
  `permission.asked`.
- El texto del asistente llega en deltas y se reenvía a `on_progress`
  (throttled, solo la cola).
- El timeout es un deadline **idle**: se reinicia con cada evento. Esperar a un
  humano no consume el presupuesto del prompt.
- `permission.asked` se delega al `PermissionRouter`. Sin presenter, el router
  rechaza (fail closed).
- El supervisor del manager revisa la salud cada 30 s y reinicia tras 3 fallos
  consecutivos.
- La GUI y el bot pueden compartir el servidor; `stop()` solo mata el proceso
  que el manager lanzó (ownership).

### 6.3 Modo CLI (rollback)

`OpenCodeCLIBackend` ejecuta:

```text
opencode run --format json [--model <model>] [--agent <agent>] \
  [--continue --session <id>] "<prompt>"
```

- Usa `asyncio.create_subprocess_exec` con `NO_COLOR=1`.
- Parsea NDJSON (`type == "text"` → `part.text`; `sessionID` → continuidad).
- Si el formato cambia, devuelve el stdout crudo y recupera el ID con regex.
- Timeout por `asyncio.wait_for`; al expirar mata el proceso
  (`taskkill /F /T` en Windows, `terminate()` en POSIX).
- No ofrece permisos interactivos: el agente los auto-rechaza en modo no
  interactivo.
- El progreso es un ticker de tiempo (5 s), no streaming real.

### 6.4 Lifecycle del servidor

`services/server_lifecycle.py`:

1. `ensure_running()` antes de empezar a hacer polling.
2. `run_forever()` como tarea supervisora.
3. En el apagado: cancelar el supervisor y llamar `manager.stop()`.
4. Un fallo de configuración o de arranque se registra y termina con
   `SystemExit(1)`, sin traceback crudo.

`warm_up_server()` es la variante de la GUI: nunca lanza; devuelve `False` y la
GUI muestra un aviso para reintentar en el próximo mensaje.

---

## 7. Comandos

### 7.1 Tabla completa

| Comando | Handler | Comportamiento real |
|---|---|---|
| `/start` | `handlers/commands.py` | Mensaje de bienvenida |
| `/help` | `handlers/commands.py` | Lista de comandos |
| `/status` | `handlers/commands.py` | Sesión activa, ID, modelo, rama git, primera/última interacción, prompts, uptime |
| `/new` | `handlers/commands.py` | Limpia el ID de la sesión activa |
| `/model [alias]` | `handlers/commands.py` | Muestra o cambia el modelo efectivo (`SessionStore.set_model`) |
| `/config [key] [value]` | `handlers/commands.py` | Lee o escribe settings por chat |
| `/health` | `handlers/commands.py` | Versión, uptime, usuario del bot, conectividad, sesiones OpenCode, workdir, modelo |
| `/cancel` | `handlers/commands.py` | Cancela el prompt en ejecución de esa conversación |
| `/session new\|list\|switch\|delete\|info\|discover\|adopt` | `handlers/sessions.py` | Gestión multi-sesión |
| `/open <prompt>` | `handlers/commands.py` | Envía un prompt explícito |
| `/wdir [proyecto]` | `handlers/ci.py` | Lista o cambia el workdir entre proyectos con `.git` |
| `/pr [título]` | `handlers/ci.py` | Crea rama, commit, push y PR con `gh` |
| `/update` | `handlers/ci.py` | Apagado graceful con exit code 42 |
| `/session_preview` | `handlers/admin.py` | Diagnóstico: estado local + sesiones OpenCode |
| `/test_md` | `handlers/admin.py` | Prueba de renderizado MarkdownV2 |
| _(texto libre)_ | `handlers/messages.py` | Prompt directo a la sesión activa |
| _(nota de voz)_ | `handlers/messages.py` | Descarga, transcribe y ejecuta |
| _(botón de permiso)_ | `handlers/permissions.py` | Resuelve `perm:<id>:<once\|always\|reject>` |

### 7.2 `/config`

```text
/config                    # muestra model, timeout, workdir, provider
/config model pro          # escribe settings.model (no cambia el prompt; usa /model)
/config timeout 900        # entero (0, 86400]
/config workdir <ruta>
/config provider opencode
```

`/config <key>` sin valor muestra el valor efectivo con defaults.

### 7.3 `/model`

- Sin argumentos: muestra el modelo actual.
- Con argumento: resuelve alias (`pro`, `flash`, `m3`, `m27`, `m27-fast`, …) y
  guarda el valor en la clave superior `model`.

### 7.4 `/session`

| Subcomando | Efecto |
|---|---|
| `/session new <nombre>` | Crea una sesión (ID `null`) y la activa |
| `/session list` | Lista sesiones del bot + sesiones OpenCode no adoptadas |
| `/session switch <nombre>` | Cambia la activa |
| `/session delete <nombre>` | Borra localmente y en OpenCode si tiene ID |
| `/session info [nombre]` | Detalle; sin nombre usa la activa |
| `/session discover` | Lista sesiones OpenCode y su estado de adopción |
| `/session adopt <id> <nombre>` | Adopta un ID real; nombre `[a-zA-Z0-9_-]{1,30}` |

### 7.5 `/wdir`

- Sin argumentos: lista proyectos bajo `OPENCODE_WORKDIR` que contengan `.git`.
- Con argumento: resuelve por nombre, alias o prefijo; si hay ambigüedad,
  muestra candidatos.
- Persiste `settings.workdir`; afecta al próximo prompt.

### 7.6 `/pr`

Pipeline de cinco etapas en `PullRequestService`:

1. `git checkout -b feature/<slug>-<hex4>`
2. `git add .`
3. `git commit -m <título>`
4. `git push -u origin <rama>`
5. `gh pr create --title <título> --body <body> --base main --head <rama>`

El body sale de `CHANGELOG.md` del workdir (se lee y se **elimina**); si no
existe, usa `PR generated via Telegram Bot.`. Sin cambios para commitear,
devuelve a la rama base y responde `No hay cambios para commitear`.

Requisitos: `git`, `gh` instalado y autenticado, permisos de escritura.

### 7.7 `/update`

El handler no llama `os._exit` directamente: escribe `exit_code = 42` y activa
`stop_event`. `run_bot()` ejecuta el apagado graceful y termina con
`sys.exit(42)`. `launcher.bat` detecta el código 42, ejecuta
`git checkout main`, `git pull origin main` y reinicia con `python bot.py`.

---

## 8. Permisos

La política vive en la configuración del agente (`opencode.json`), no en el
bot. Cada clave (`bash`, `edit`, `read`, `external_directory`, …) se resuelve a
`allow`, `ask` o `deny`; gana la última regla que coincide.

Flujo:

1. El servidor emite `permission.asked`.
2. `OpenCodeServerBackend` lo convierte en `PermissionRequest`.
3. `PermissionRouter.request_permission()` lo difunde a todos los presenters
   (Telegram y Flet) y espera la primera respuesta.
4. El primer `resolve_from_ui()` gana; los clics duplicados se ignoran.
5. El backend responde al servidor con `POST /permission/{id}/reply`.

Reglas:

- `OPENCODE_PERMISSION_TIMEOUT=0` (default): espera al humano indefinidamente.
- `>0`: auto-rechaza tras N segundos (más 1 s de gracia en el backend).
- Sin presenters: rechazo inmediato (fail closed).
- Si la decisión no se pudo aplicar en el servidor, se marca
  `permission_reply_not_accepted` y la UI lo muestra.
- `external_directory` controla accesos fuera del worktree.
- `tools` está deprecado; prefiere `permission` para conservar los diálogos.

---

## 9. Frontend Flet

### 9.1 Ejecución

```bash
pip install -e ".[gui]"
python -m flet_app.main
```

Desde la raíz del repo. El entry point también funciona con
`flet run flet_app/main.py` (el módulo ajusta `sys.path` cuando se ejecuta como
script).

### 9.2 Pantallas y componentes

- `flet_app/screens/chat_screen.py`: top bar, greeting, conversación y composer.
- `flet_app/screens/conversations_screen.py`: lista de conversaciones con
  estados loading/empty/error; al abrir, adopta o cambia la sesión y carga el
  historial.
- `flet_app/components/`: `composer`, `greeting`, `message_bubble`, `top_bar`.
- `flet_app/core/design_system.py`: tokens visuales.
- `flet_app/core/navigation.py`: `ScreenHost` (swap de controles, sin router).

### 9.3 Puertos

- `FletChatView` implementa `ChatView`; `source_label = "📱 Flet"`.
- `FletPermissionPresenter` muestra `ft.AlertDialog` modales, uno a la vez, con
  cola para solicitudes simultáneas.
- `AllowAllGate` es la política v1: acceso físico al dispositivo.
- La clave es **`flet:local`** (`flet_app/keys.py`), estable entre arranques.

### 9.4 Warm-up y servidor compartido

- `main()` construye el core, monta la UI y luego ejecuta `warm_up_gui()`:
  adopta o lanza el servidor antes del primer prompt.
- Si el warm-up falla, muestra un aviso; el siguiente mensaje reintenta.
- Al cerrar la GUI **nunca** se detiene el servidor: el bot puede compartirlo.
- Si no hay workdir explícito para `flet:local`, la GUI lo fija a `BASE_DIR`.

### 9.5 Empaquetado

`pyproject.toml` declara el extra `gui` y los paquetes del core, pero **no**
incluye `flet_app` en `[tool.setuptools].packages`. En modo editable desde la
raíz funciona; para una instalación no editable habría que agregarlo.

---

## 10. Autenticación

### 10.1 Telegram

- `ALLOWED_CHAT_IDS` es la allow-list.
- `load_telegram_config()` la valida y actualiza en sitio.
- `run_bot()` guarda `TelegramGate(ALLOWED_CHAT_IDS)` en
  `bot_data[GATE_KEY]`.
- `@authorized` (`handlers/__init__.py`) resuelve el gate y llama
  `authorize_request()`; si el gate falta o deniega, registra y retorna sin
  responder al chat.
- Los callbacks de permisos también pasan por `@authorized`.

### 10.2 Flet

- `AllowAllGate` autoriza todo: el modelo de seguridad es el acceso físico al
  dispositivo.
- El camino `authorize_request()` / `finish_request()` es el mismo que Telegram,
  así que los eventos de logging y el `request_id` no divergen.

---

## 11. Logging

### 11.1 Configuración

`core_config.setup_logger()` se llama **una vez** como primera acción de cada
entry point (`bot.py`, `flet_app/main.py`) y en `tests/conftest.py` con un
archivo temporal. Importar módulos no configura logging.

- `BOT_LOG_FORMAT=text`: `key=value` legible y greppable.
- `BOT_LOG_FORMAT=json`: una línea JSON por registro (útil para `jq`/Loki).
- `BOT_LOG_FILE`: archivo rotativo de 5 MB con 3 backups.
- Consola: nivel DEBUG, prefijo corto.
- Archivo: nivel DEBUG, incluye `[req=<request_id>]` y el nombre completo del
  logger.

`get_module_logger(__name__)` namespaced bajo `opencode_bot.*` para que todos
los registros propaguen a los handlers de `setup_logger()`.

### 11.2 Campos y privacidad

- `request_id` se propaga por `contextvars` y lo inyecta `RequestIdFilter`.
- `chat_id` se transforma siempre en `chat_id_masked` (primeros 2 y últimos 2
  dígitos).
- `mask_conversation_key()` conserva el namespace: `telegram:86******27`,
  `flet:lo*al`.
- Las credenciales en prompts se redactan antes de loggear (`sk-`, `Bearer `,
  `token=`, etc.).
- `log_exception()` escribe en `opencode_bot.swallowed.<module>` con
  traceback; `SWALLOWED_LOG_LEVEL` es el interruptor único.

### 11.3 Eventos representativos

`bot_start`, `bot_stop`, `handler_entry`, `handler_exit`, `unauthorized`,
`prompt_start`, `prompt_received`, `prompt_complete`, `prompt_error`,
`prompt_cancelled`, `session_created`, `session_switched`, `session_deleted`,
`session_reset`, `session_id_captured`, `model_changed`,
`conversation_key_migrated`, `subprocess_start`, `subprocess_complete`,
`subprocess_timeout`, `subprocess_error`, `opencode_response_delivered`,
`delivery_partial`, `permission_asked`, `permission_resolved`,
`permission_timeout`, `permission_cancelled`, `permission_budget_expired`,
`permission_turn_ended`, `opencode_server_*`, `transcribe_voice*`.

---

## 12. Tests

### 12.1 Estado actual

En `main` (HEAD `79a7eb1`) la suite recolecta **719 tests** con
`pytest --collect-only -q`.

```bash
# Suite completa
.venv\Scripts\python.exe -m pytest

# Un archivo
.venv\Scripts\python.exe -m pytest tests/test_session_store.py -v

# Con cobertura (requiere instalar pytest-cov; no está en el extra dev)
.venv\Scripts\python.exe -m pytest --cov=. --cov-report=term-missing
```

Configuración en `pyproject.toml`: `asyncio_mode = "auto"`, `testpaths =
["tests"]`, `--strict-markers`.

### 12.2 Grupos importantes

| Grupo | Archivos | Qué verifica |
|---|---|---|
| Límite del core | `test_core_isolation_boundary.py`, `test_config_split.py`, `test_core_strings.py` | `services/` no importa frontends; config importable sin Telegram; el core no decide copy |
| Caracterización | `test_characterization_*.py` (wdir, voice, session routing/info, pr, config) | La extracción no cambió comportamiento |
| Sesiones | `test_session_store.py`, `test_session_store_migration.py`, `test_session_service.py`, `test_session_commands.py` | CRUD, migración, backup, routing tipado |
| Backends | `test_opencode_server_backend.py`, `test_opencode_server_client.py`, `test_opencode_server_manager.py`, `test_opencode_sessions.py` | SSE, permisos, lifecycle, SQL |
| Permisos | `test_permission_router.py`, `test_permission_callback.py`, `test_telegram_permission_presenter.py`, `test_flet_permission_presenter.py` | Rendezvous, timeouts, presenters |
| Flet | `test_flet_render.py`, `test_flet_lifecycle.py`, `test_conversations_screen.py`, `test_navigation.py` | Render, warm-up, pantallas |
| Logging | `test_log_*.py`, `test_json_formatter.py`, `test_keyvalue_formatter.py` | Formatters, propagación, request_id |
| Servicios | `test_prompt_service_live_progress.py`, `test_workdir_service.py`, `test_pull_request_service.py`, `test_transcription_service.py`, `test_status_service.py`, `test_health_service.py`, `test_settings_service.py` | Lógica de negocio |
| Auth | `test_authorized.py`, `test_frontend_gate.py` | Un solo camino de autorización |

---

## 13. CI/CD y despliegue

### 13.1 GitHub Actions

`.github/workflows/ci.yml` corre en push a `main` y en Pull Requests:

- matriz Python **3.11, 3.12 y 3.13**;
- `pip install -e ".[dev]"`;
- `ruff check .`;
- `mypy . --strict`;
- `pytest tests/ -v --tb=short`.

### 13.2 Pre-commit

`.pre-commit-config.yaml` usa:

- `astral-sh/ruff-pre-commit` con `ruff --fix` y `ruff-format`;
- `pre-commit/mirrors-mypy` con `--strict`, excluyendo `tmp/` y
  `get_chat_id.py`.

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

### 13.3 `launcher.bat`

`launcher.bat` es el babysitter de Windows:

1. Activa `.venv` si existe.
2. Ejecuta `python bot.py`.
3. Si el código de salida es `42`: `git checkout main`, `git pull origin main`
   y vuelve a arrancar.
4. Cualquier otro código: pausa y muestra el estado.

`run_bot.bat` es el arranque manual sin auto-restart.

### 13.4 Exit code 42

`/update` activa el apagado graceful y el proceso termina con `sys.exit(42)`.
El launcher usa ese valor no estándar para distinguir una actualización de un
fallo.

---

## 14. Seguridad

- **Allow-list:** solo chats autorizados; denegación silenciosa con log.
- **Ejecución paralela:** `PromptService` rechaza un segundo prompt para la
  misma conversación (`PromptAlreadyRunningError`).
- **IDs de sesión:** se validan con `^ses_[A-Za-z0-9]+$` antes de consultar la
  DB; el SQL se interpola con escape de comillas (`''`) y la CLI no ofrece
  parámetros bind.
- **Servidor:** loopback por defecto, Basic auth obligatoria, ownership del
  proceso, `?directory=` por conversación.
- **Permisos:** fail closed sin presenters; el humano decide `allow`/`always`/
  `reject`.
- **Logs:** `chat_id` enmascarado, claves de conversación enmascaradas,
  credenciales redactadas, password del servidor nunca se registra.
- **Secretos:** no commitear `.env`; rotar el token y la password.

---

## 15. Troubleshooting

### El bot no responde

1. Verifica que el proceso esté vivo (`launcher.bat` o `python bot.py`).
2. Revisa `TELEGRAM_BOT_TOKEN`.
3. Confirma que el chat esté en `ALLOWED_CHAT_IDS`.
4. Busca `event=unauthorized` o errores de red en `bot.log`.

### Falla al arrancar por `OPENCODE_SERVER_PASSWORD`

El modo `server` exige Basic auth. Define la variable
(`openssl rand -hex 16`) o usa `OPENCODE_BACKEND=cli`.

### `OPENCODE_SERVER_HOST no es una dirección loopback`

Usa `127.0.0.1`, `::1` o `localhost`. Si el agente vive en otra máquina,
define `OPENCODE_SERVER_ALLOW_REMOTE=1` y protege el puerto con TLS y firewall.

### La respuesta nunca llega / SSE mudo

Suele faltar o ser incorrecto el `?directory=`. Verifica `OPENCODE_WORKDIR` y
el workdir del chat (`/wdir` o `/config workdir`).

### Timeout de prompt

Aumenta `OPENCODE_TIMEOUT`. Recuerda que en modo servidor es un deadline idle:
los eventos lo reinician, y una espera de permiso lo suspende.

### `/pr` no encuentra cambios

El pipeline hace commit de todo (`git add .`). Si el árbol está limpio, responde
`No hay cambios para commitear`. Si no hay `CHANGELOG.md`, usa el body por
defecto; no genera un PR con body vacío.

### `gh: command not found` / autenticación

Instala GitHub CLI y ejecuta `gh auth login`. En Windows, `launcher.bat` agrega
`C:\Program Files\GitHub CLI` al `PATH`.

### `get_chat_id.py` no ve el token

El script lee `.env` del directorio **padre** del repo. Exporta
`TELEGRAM_BOT_TOKEN` o ajusta `ENV_PATH`.

### La GUI de Flet no arranca

Instala el extra: `pip install -e ".[gui]"`. En modo no editable, `flet_app`
no está declarado en `[tool.setuptools].packages`; ejecuta desde la raíz o
agrega el paquete.

### `opencode session list` no parsea

El parser espera el formato de dos o más espacios con `ses_...`, título y hora.
Si el CLI cambió, revisa `event=oc_session_parse_empty`. La ejecución de
prompts no depende de ese parseo: el ID llega explícito del backend.

---

## 16. Decisiones y ADRs

- [ADR 0001 — Core frontend-agnóstico](docs/adr/0001-frontend-agnostic-core.md):
  separación en tres capas, puertos y composition roots.
- [ADR 0002 — `ConversationKey` opaco](docs/adr/0002-conversation-key.md):
  claves namespaced y migración de `sessions.json`.

Decisiones vigentes:

- **DI sobre globals:** `CoreStack`/`AppContainer`; facilita mocks y evita
  imports circulares.
- **Protocols sobre ABCs:** duck typing y mocks sin herencia.
- **`asyncio.Lock` + caché TTL:** menos I/O y acceso seguro en un solo event
  loop.
- **Apagado graceful:** `/update` ya no usa `os._exit`; activa `stop_event` y
  termina con `sys.exit(42)`.
- **`CHANGELOG.md` se consume en `/pr`:** se usa como body y se elimina.
- **`CONTAINER_KEY`/`GATE_KEY` como constantes:** evitan magic strings en
  `bot_data`.

---

## 17. Notas para Linux/VPS

- El código es portable: `pathlib`, `shutil.which`, `asyncio`; la única rama
  Windows es `taskkill` en el backend CLI y las rutas de `gh` en
  `pull_request_service`.
- `launcher.bat` es exclusivo de Windows. En Linux usa `systemd`, `supervisor`
  o un script equivalente que reinicie tras el exit code 42.
- Crea el venv e instala dependencias:

  ```bash
  python3 -m venv .venv
  . .venv/bin/activate
  pip install -e ".[dev]"
  ```

- Define las variables en el entorno o en `.env`; no depende de rutas de
  Windows.
- Mantén `OPENCODE_SERVER_HOST` en loopback. Si el agente está en otra máquina,
  usa `OPENCODE_SERVER_ALLOW_REMOTE=1` con TLS, firewall y password fuerte.
- Para la GUI en un servidor sin display, usa el modo Android/remoto de Flet o
  ejecútala en el dispositivo; el bot no la necesita.

---

## 18. Apéndice: estructura del repositorio

```text
Sdd-Orchestrator-Telegram-Bot/
├── bot.py                          # Entry point de Telegram
├── core_config.py                  # Config del core (sin SystemExit en import)
├── telegram_config.py              # Token + allow-list + fail-fast explícito
├── launcher.bat                    # Babysitter Windows (exit 42 -> pull + restart)
├── run_bot.bat                     # Arranque manual
├── get_chat_id.py                  # Utilidad para obtener chat_id
├── sessions.json                   # Persistencia local (no versionada)
├── sessions.example.json           # Plantilla
├── pyproject.toml                  # Deps, ruff, mypy, pytest, paquetes
├── .env.example                    # Plantilla de variables
│
├── services/                       # Core PTB-free
│   ├── composition.py              # build_prompt_stack() / CoreStack
│   ├── conversation.py             # ConversationKey
│   ├── chat_view.py                # ChatView / CoreStrings / ProgressHandle
│   ├── frontend_gate.py            # FrontendGate + authorize_request
│   ├── permissions.py              # PermissionPort / Presenter / Decision
│   ├── permission_router.py        # Rendezvous de permisos
│   ├── prompt_service.py           # Orquestación de prompts
│   ├── session_store.py            # Estado + migración
│   ├── session_service.py          # CRUD/discover/adopt
│   ├── session_commands.py         # Parser tipado de /session
│   ├── settings_service.py         # /config
│   ├── workdir_service.py          # /wdir
│   ├── pull_request_service.py     # /pr
│   ├── transcription_service.py    # Whisper
│   ├── status_service.py           # Datos de /status
│   ├── health_service.py           # Datos de /health
│   ├── conversations_service.py    # Historial de Flet
│   ├── agent_sessions.py           # Puertos de historial
│   ├── ai_backend.py               # Protocol AIBackend
│   ├── ai_provider_factory.py      # Factory de providers
│   ├── opencode_cli_backend.py     # Rollback CLI
│   ├── opencode_server_backend.py  # Backend HTTP/SSE
│   ├── server_lifecycle.py         # Start/stop/supervisor
│   └── opencode_server/            # Manager, client, errores
│
├── frontends/telegram/             # Adaptadores de Telegram
│   ├── adapter.py                  # BotPort
│   ├── bot_port.py                 # Protocol de transporte
│   ├── chat_view.py                # ChatView + progreso
│   ├── message_sender.py           # MarkdownV2 + fallback + split
│   ├── formatting.py               # Escapes, tablas, chunks
│   ├── permission_presenter.py     # Botones inline
│   ├── gate.py                     # TelegramGate
│   ├── keys.py                     # telegram:{id}
│   ├── strings.py                  # CoreStrings de Telegram
│   └── container.py                # AppContainer
│
├── handlers/                       # Handlers delgados
│   ├── __init__.py                 # @authorized
│   ├── _guards.py                  # Narrowing tipado de Update
│   ├── commands.py                 # /start /help /status /new /model /config /health /cancel /open
│   ├── sessions.py                 # /session
│   ├── messages.py                 # Texto y voz
│   ├── permissions.py              # Callback de botones
│   ├── admin.py                    # /session_preview /test_md
│   └── ci.py                       # /pr /update /wdir
│
├── flet_app/                       # Frontend Flet
│   ├── main.py                     # Entry point
│   ├── flet_chat_view.py           # ChatView
│   ├── flet_permission_presenter.py
│   ├── flet_strings.py
│   ├── keys.py                     # flet:local
│   ├── components/                 # composer, greeting, message_bubble, top_bar
│   ├── core/                       # design_system, navigation
│   └── screens/                    # chat_screen, conversations_screen
│
├── formatting/markdown.py          # Limpieza neutral de output
├── locales/                        # i18n (es)
├── opencode/                       # Cliente CLI/DB de OpenCode
├── persistence/sessions.py         # DAL de sessions.json
├── utils/                          # logging, time_formatting
├── tests/                          # 719 tests
├── docs/adr/                       # ADR 0001, 0002
└── .github/workflows/ci.yml        # CI matrix 3.11–3.13
```

### Archivos legados de la documentación anterior

Los siguientes ya **no existen** en `main` y no deben citarse como rutas
actuales:

- `config.py` → reemplazado por `core_config.py` + `telegram_config.py`.
- `services/container.py` → `frontends/telegram/container.py`.
- `services/telegram_adapter.py` → `frontends/telegram/adapter.py`.
- `services/message_sender.py` → `frontends/telegram/message_sender.py`.
- `services/bot_port.py` → `frontends/telegram/bot_port.py`.

---

*Documentación técnica del repositorio Sdd-Orchestrator-Telegram-Bot. Estado:
HEAD `79a7eb1`, rama `main`.*
