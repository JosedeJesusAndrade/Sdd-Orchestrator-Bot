# ADR 0002 — `ConversationKey` opaco y migración de `sessions.json`

- **Estado:** Aceptado
- **Fecha:** 2026-11-13
- **Contexto del cambio:** `sdd/core-isolation` (diseño #513, tareas #514)
- **Ámbito:** identidad de conversación, persistencia, logging

## Contexto

Antes de este cambio, la identidad de una conversación era
`ConversationId = int | str`:

- Telegram usaba el `chat_id` **entero** (p. ej. `8664220427`, o negativo en
  grupos: `-100123`);
- Flet usaba el literal **`"flet"`**, codificado en varios sitios;
- `SessionStore` hacía `str(key)` al persistir;
- `mask_chat_id` asumía que el identificador era numérico.

Ese tipo nominal permitía que dos frontends colisionaran (`"flet"` podía
interpretarse como un chat crudo) y no expresaba ninguna frontera: mypy no
podía distinguir un `chat_id` de un `conversation_id`, y la GUI dependía de un
literal escondido. Con la llegada de Flet, la identidad tenía que volverse
explícita y namespaced sin romper las sesiones ya guardadas.

## Decisión

Introducir una clave **opaca y con prefijo**, definida en
`services/conversation.py`:

```python
ConversationKey = NewType("ConversationKey", str)

def make_conversation_key(prefix: str, raw: str) -> ConversationKey: ...
def split_conversation_key(key: ConversationKey) -> tuple[str, str]: ...
```

Valores canónicos:

- Telegram: `telegram:{chat_id}` — `frontends/telegram/keys.py::telegram_key`.
- Flet: `flet:local` — `flet_app/keys.py::flet_key()`, estable entre arranques.

Reglas:

- El core (`services/`, incluido `SessionStore`) solo recibe
  `ConversationKey`; nunca un `int` ni un `str` crudo.
- `NewType` da seguridad de tipos en mypy sin costo en runtime: el valor sigue
  siendo un `str`, JSON-friendly y portable Windows/Unix.
- `mask_conversation_key()` enmascara solo la parte local y conserva el
  prefijo, para logs forenses sin filtrar el chat id.

### Migración de datos (`sessions.json`)

`SessionStore._load` aplica `migrate_legacy_keys(data)` antes de servir
cualquier lectura:

| Clave legada | Clave canónica |
|---|---|
| `"8664220427"` (numérica, incluida negativa) | `"telegram:8664220427"` |
| `"flet"` | `"flet:local"` |
| ya prefijada | sin cambios |

Propiedades garantizadas (y probadas en
`tests/test_session_store_migration.py`):

- **Idempotente:** un archivo ya migrado no se reescribe.
- **Backup único:** antes de la primera reescritura se copia el original a
  `sessions.json.bak`; el backup nunca se sobrescribe en migraciones
  posteriores.
- **Canónica gana:** si conviven `"123"` y `"telegram:123"`, prevalece la
  entrada canónica; nunca se pisa el estado nuevo con datos viejos.
- **Falla segura:** un `sessions.json` corrupto no tumba el arranque; el
  contenido ilegible se conserva en el backup para recuperación forense.
- **Persistencia atómica:** la reescritura usa el mecanismo existente de
  `SessionStore`.

La migración emite el evento `conversation_key_migrated` con el conteo.

## Consecuencias

**Positivas**

- Una sola identidad para todos los frontends; imposible colisionar por
  accidente entre `telegram:123` y `flet:local`.
- El tipo hace que mypy detecte el uso de claves crudas en las firmas de los
  servicios.
- Las sesiones del bot sobreviven al cambio sin intervención manual.

**Negativas / costos**

- Una migración de datos es irreversible en la práctica: el `.bak` es la red de
  seguridad, no un rollback automático.
- `flet:local` fija una identidad de dispositivo; cuando Flet sea multiusuario
  hará falta una clave nueva (`flet:{uuid}`) y otra migración.
- `NewType` es borrado en runtime: la seguridad es estática, no defensa contra
  strings mal formados en tiempo de ejecución.

## Alternativas consideradas

1. **Mantener `int | str` (rechazada).** Es el statu quo. No expresa namespace,
   permite colisiones, obliga a `mask_chat_id` a asumir dígitos y no permite
   que mypy distinga una clave de conversación de un `chat_id`.
2. **`ConversationKey` como `uuid` (rechazada).** Un UUID por arranque rompe la
   continuidad: cada launch de Flet crearía una conversación nueva y perdería
   el historial y los settings. La continuidad pesa más que la unicidad
   automática en una app local de un solo usuario.
3. **Dataclass congelada `ConversationKey(prefix, raw)` (rechazada por ahora).**
   Es más rica en tipos, pero deja de ser trivialmente serializable como clave
   JSON y obliga a convertirla en cada lectura/escritura. `NewType(str)` con
   helpers obtiene el mismo contrato con menos fricción; se puede evolucionar
   después sin cambiar los valores persistidos.

## Evidencia

- `tests/test_conversation_key.py` — minting, validación y enmascarado.
- `tests/test_conversation_key_mint_sites.py` — los sitios que acuñan claves.
- `tests/test_session_store_migration.py` — migración numérica/negativa/Flet,
  idempotencia, backup único, colisión canónica, archivo corrupto.
- `services/session_store.py` — `_migrate_key` y `migrate_legacy_keys`.
- `frontends/telegram/keys.py`, `flet_app/keys.py` — acuñado por frontend.

## Referencias

- Diseño: `sdd/core-isolation/design` (#513), decisión D2/D3.
- Tareas: `sdd/core-isolation/tasks` (#514), Fase 3.
- ADR relacionado: `docs/adr/0001-frontend-agnostic-core.md`.
