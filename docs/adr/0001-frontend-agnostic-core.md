# ADR 0001 — Core frontend-agnóstico y adaptadores por frontend

- **Estado:** Aceptado
- **Fecha:** 2026-11-13
- **Contexto del cambio:** `sdd/core-isolation` (diseño #513, tareas #514)
- **Ámbito:** estructura de paquetes, límites de importación, composición

## Contexto

El proyecto nació como **un bot de Telegram**: `bot.py` construía una
`Application` de `python-telegram-bot`, los handlers eran el punto de entrada y
la lógica de negocio vivía en `services/` — pero ese `services/` también
contenía los adaptadores de Telegram (`telegram_adapter.py`,
`telegram_chat_view.py`, `message_sender.py`, `bot_port.py`,
`telegram_permission_presenter.py`). La consecuencia era que el "core" no era
importable sin Telegram.

Cuando llegó la GUI de Flet (`flet_app/`), el acoplamiento se volvió visible en
cuatro formas concretas:

1. **Import-time fail-fast.** `config.py` ejecutaba `SystemExit(1)` al
   importarse si `ALLOWED_CHAT_IDS` estaba vacío. Cualquier embedder que no
   fuera Telegram —incluida la propia Flet— moría al importar
   `services.composition`, que dependía de `config`.
2. **Tipos de transporte filtrados.** `ConversationId = int | str` mezclaba el
   `chat_id` entero de Telegram con el literal `"flet"`; `mask_chat_id`
   asumía dígitos.
3. **Copy de UI en el core.** `PromptService` decidía emojis y frases
   ("⏳ OpenCode procesando...", "✅ Completado."), así que la GUI heredaba el
   texto del bot.
4. **Dos caminos de autenticación.** `@authorized` (PTB) y el envío de Flet
   implementaban por separado la allow-list, el `request_id` y el logging.

El nombre del repositorio (`Sdd-Orchestrator-Telegram-Bot`) reflejaba ese
pasado: Telegram aparecía como dueño del producto. La decisión de este ADR es
el punto en que Telegram deja de ser el dueño y pasa a ser **un frontend más**;
la consecuencia visible es el rename previsto del repositorio a
`Sdd-Orchestrator-Bot` (el remoto de GitHub conserva todavía el nombre viejo;
el código ya no depende de él).

## Decisión

Separar el sistema en tres capas con una regla de importación verificable:

> `services/` (el core) **nunca** importa `telegram`, `flet`, `frontends/`,
> `handlers/` ni `bot.py`.

En concreto:

1. **Core puro en `services/`** más `core_config.py`, `formatting/`,
   `opencode/` y `persistence/`. Contiene dominio, orquestación, persistencia y
   los contratos (`Protocol`).
2. **Adaptadores de Telegram en `frontends/telegram/`**: `adapter.py`,
   `bot_port.py`, `message_sender.py`, `chat_view.py`,
   `permission_presenter.py`, `formatting.py`, `gate.py`, `keys.py`,
   `strings.py` y el contenedor DI de Telegram (`container.py`).
3. **Flet en `flet_app/`**: su propio `ChatView`, `PermissionPresenter`,
   strings y minting de claves.
4. **Composition roots**: `bot.py` y `flet_app/main.py` construyen el mismo
   grafo con `services.composition.build_prompt_stack()` e inyectan **sus**
   adaptadores. El core no conoce ninguno de los dos.
5. **Puertos explícitos** en el core: `ChatView` + `CoreStrings`
   (`services/chat_view.py`), `PermissionPort` / `PermissionPresenter`
   (`services/permissions.py`), `AIBackend` (`services/ai_backend.py`),
   `FrontendGate` (`services/frontend_gate.py`) y `ConversationKey`
   (`services/conversation.py`).

El límite no es una convención social: se comprueba en
`tests/test_core_isolation_boundary.py` con dos mecanismos —

- **subproceso:** importar cada módulo del core deja `telegram`/`flet`/
  `frontends` fuera de `sys.modules`;
- **escaneo estático:** ningún archivo de `services/**` contiene
  `import telegram` / `from telegram` / `import frontends` / `from frontends`.

## Consecuencias

**Positivas**

- El core es embebible: `python -c "import services.composition"` funciona con
  `ALLOWED_CHAT_IDS` vacío o ausente.
- Agregar un frontend no toca la lógica de negocio; solo se implementan los
  puertos.
- Los tests de caracterización de la Fase 0 (`/wdir`, `/pr`, `/session`,
  `/config`, voz) siguen verdes: la extracción no cambió comportamiento.
- La separación hizo aparecer y corregir defectos reales (fail-fast en import,
  copy en el core, doble autenticación) en lugar de esconderlos.

**Negativas / costos**

- Más paquetes y más indirección: un cambio de UI obliga a tocar el frontend,
  no solo el core (que es el objetivo, pero es trabajo extra).
- El wiring se duplica en dos composition roots (aunque el grafo compartido
  vive en `build_prompt_stack`).
- Migración única de datos (`sessions.json`) documentada en el ADR 0002.

**Neutras**

- `frontends/` es plural a propósito: deja espacio para `frontends/flet/` si la
  GUI migra a ese paquete en el futuro.
- No hay shims de compatibilidad permanentes en `services/`. Los módulos viejos
  se movieron y se actualizaron todas las importaciones de forma atómica.

## Alternativas consideradas

1. **Core como librería embebida en cada frontend (rechazada).** Cada frontend
   importaría una copia/wiring propio del core. Se rechazó porque duplica la
   composición, permite divergencia silenciosa y no impide que el "core" vuelva
   a depender del transporte. El objetivo es un core único, no N copias.
2. **Mantener Telegram dentro de `services/` (rechazada).** Es el statu quo:
   simple a corto plazo, pero imposibilita el límite y obliga a Flet a cargar
   PTB. Fue exactamente el problema que motivó el cambio.
3. **Un repositorio/servicio separado para el core (rechazada por ahora).**
   Aislaría el límite por proceso, pero agrega versionado, publicación y
   despliegue para un proyecto de un solo mantenedor. El límite de importación
   ya da la mayor parte del beneficio sin ese costo.

## Evidencia

- `tests/test_core_isolation_boundary.py` — subproceso + escaneo estático.
- `tests/seam_support.py` — helper compartido del probe de `sys.modules`.
- `tests/test_config_split.py` — `core_config` importa sin entorno Telegram.
- `tests/test_core_strings.py` — el core no decide copy; `CoreStrings`
  inyectado con centinelas.
- `tests/test_authorized.py` y `tests/test_frontend_gate.py` — un solo camino
  de autorización para ambos entry points.
- `services/composition.py` — `CoreStack` no contiene tipos de Telegram.
- Suite final: 719 tests verdes; `mypy .` 0; `ruff check .` limpio;
  `ruff format --check .` limpio.

## Referencias

- Diseño: `sdd/core-isolation/design` (#513).
- Tareas y fases: `sdd/core-isolation/tasks` (#514).
- ADR relacionado: `docs/adr/0002-conversation-key.md`.
