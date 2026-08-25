# GB10 — Arquitectura de 3 capas (corregida)

> Corrige `PROMPT_CLAUDE_CODE_GB10.md`: ese mandato asumía que `PRUEB25` era
> MySQL en `192.168.4.148:3306`. **Es falso.** `PRUEB25` es SQL Server
> (`192.168.4.20:1433`, user `profit`), copia espejo de pruebas de `CRISTM25`
> (también SQL Server, mismo host). El MySQL de `192.168.4.148:3306` es una
> base **distinta**: `barquisimeto` (legacy PHP: `rep_not`, `gestion`,
> `asignaciones_preparacion`). Esta carpeta reemplaza el mandato original con
> los tipos de motor reales.

La GB10 física todavía no está en sitio (comprada, en tránsito). Todo aquí
queda **listo para ejecutar**, no se ha corrido nada ni se tocó el `.env` de
producción actual.

## Piezas staged (2026-08-12) — ninguna activada, ninguna importada por código activo

- `inference/engine_config.py` + `inference/README.md`: abstracción
  Ollama/vLLM/Triton por env `INFERENCE_ENGINE` (default `ollama`, sin
  cambio de comportamiento hoy). Decisión de motor final se toma con la GDX
  en mano y benchmarks reales, no antes.
- `hardware_detect.py`: detecta GPU NVIDIA vía `nvidia-smi` (sin deps
  nuevas). Reemplaza el hardcoding actual de recursos limitados en
  `ara_server.py` cuando llegue la GDX — no tocado todavía.
- `auth/token_auth.py`: bearer estático (default) o JWT opcional para
  endpoints internos de la GDX. No reemplaza `CustomerAuthenticator.php`
  (eso es autenticación de clientes, esto es servicio-a-servicio).
- `POSTGRES_MIGRACION.md`: consideración de SQLite→PostgreSQL para
  `ara_llm.db` (Capa 3) — **no decidido**, migrar solo si hay evidencia real
  de `database is locked` bajo carga concurrente en la GDX.

Todo probado con `ara/venv/Scripts/python.exe` (el venv que ya existía en el
repo, no se creó uno nuevo): `py_compile` limpio en los 3 `.py`,
`hardware_detect.py` corre y reporta correctamente "sin GPU" en esta máquina
de desarrollo.

## Arquitectura real

```
CAPA 1 — Fuentes remotas (solas de verdad, SOLO LECTURA)
  ├─ PRUEB25   SQL Server @ 192.168.4.20:1433  (ERP Profit, espejo de CRISTM25)
  │             tablas usadas hoy: art (campo7=ubicación), st_almac (stock_act)
  └─ barquisimeto  MySQL @ 192.168.4.148:3306  (legacy operativo)
                tablas: rep_not, gestion, asignaciones_preparacion

CAPA 2 — Espejo local en la GB10 (donde corren los adaptadores PHP/Python)
  ├─ SQL Server Express/local, mirror de las tablas de PRUEB25 que se consultan
  └─ MySQL local, mirror de barquisimeto (rep_not/gestion/asignaciones_preparacion)
  Los mismos nombres de tabla y columnas — PHP (ConnectionWrapper) solo cambia
  de host vía env, sin tocar código.

CAPA 3 — ARA_LLM @ ./ara_llm.db (SQLite local en GB10)
  contexto_llm, cache_adaptadores, logs_operacion, circuit_breakers, embeddings
  (sin cambios respecto al mandato original — esto sí era correcto)
```

### Por qué separar así
El objetivo (confirmado por el usuario) es que, tras montar la estación de IA
local, el volumen de consultas por departamento va a ser grande. Sin espejo
local, cada tool PHP/Python golpea la red hacia `.20:1433` o `.148:3306` en
cada consulta. La Capa 2 absorbe ese tráfico; la Capa 1 se sincroniza cada
`SYNC_INTERVAL_SECONDS` en vez de en cada request.

## Patrón de sincronización reutilizado

`ara/ARA_Brain/vigilar_datos.py` ya implementa el patrón correcto para leer
Profit (SQL Server) sin bloquear el ERP: conexión pyodbc **corta** (abre →
`fetchall()` → cierra, nunca persistente), `WITH (NOLOCK)` en las lecturas,
reintentos con backoff fijo. Los scripts de esta carpeta siguen ese mismo
patrón en vez de inventar uno nuevo.

**Corregido (2026-08-19):** al revés de lo que decía esta nota antes —
`vigilar_datos.py` tenía el default de `_DB` clavado en `PRUEB25` (comentario
propio "v4.15: ningún host apunta a CRISTM25"), y por eso el stock que
mostraba ARA quedaba desfasado del real (caso verificado: CR000278 mostraba
228 en despacho BQTO en vez de 268 real). `PRUEB25` resultó estar
significativamente atrasado respecto a `CRISTM25` en `st_almac`, no un simple
espejo al día. Ya arreglado: el default de `_DB` en `vigilar_datos.py` es
ahora `CRISTM25`, igual que el resto del stack PHP/Python. Sigue priorizando
`PROFIT_SQL_*` sobre `PROFIT_DB_*`, y sigue siendo solo lectura (`WITH
(NOLOCK)`, conexión corta) — el cambio de base no agrega carga de escritura
ni cola sobre producción.

## Tablas a espejar

Verificadas en vivo (`bin/listar_tablas_pruebas25.php`, INFORMATION_SCHEMA.TABLES
contra PRUEB25 real, 231 tablas totales, solo lectura) cruzadas con lo que los
tools de NvidiaBrain consultan hoy — nada adivinado:

- MySQL `barquisimeto` → `rep_not`, `gestion`, `asignaciones_preparacion`.
- SQL Server `PRUEB25` → `art`, `clientes`, `factura`, `st_almac`, `not_ent`,
  `reng_nde`, `reng_fac`.

**Bug corregido (2026-08-11, misma noche):** `ConciliarFacturaTool.php` tenía
`const TABLA = 'FacturasDrogueria'` hardcodeado (tabla inexistente en
PRUEB25). Cambiado a `const TABLAS = ['FacturasDrogueria', 'factura']`
resuelto vía `resolverTabla()`, igual patrón que el resto de los tools;
también se envolvió su transacción en `finally { $pdo = null;
gc_collect_cycles(); }`. Verificado en vivo: ya resuelve `factura` y avanza
a `resolverColumnas()`. Queda abierto (no bug de esta corrección): ningún
candidato de columna calza para el monto de la factura — pendiente para
cuando se trabaje ese adaptador.

Candidatos muertos que existen en el código pero NUNCA calzan contra PRUEB25
real (quedan como fallback inofensivo en `resolverTabla()`, no rompen nada):
`InventarioDrogueria`, `ClientesDrogueria`, `saCliente`, `saStock`, `existencia`,
`articulos`. `usuarios` sí existe pero en MySQL `barquisimeto` (verificado en
vivo: 14 tablas reales), no en PRUEB25 — `GestionSuperEsteroideSearchTool` la
usa correctamente contra ese lado.

## Runbook — cuando llegue la GB10

1. Instalar Python ≥3.11, y motor local de BD:
   - SQL Server Express (o LocalDB) para el mirror de Capa 2 lado Profit.
   - MySQL/MariaDB para el mirror de Capa 2 lado barquisimeto.
2. `pip install -r ara/requirements.txt -r gb10/requirements_gb10.txt`
   (agrega `pymysql` y `python-dotenv`; `pyodbc` ya está en `ara/requirements.txt`).
3. Copiar `gb10/.env.gb10.example` → `.env.gb10`, rellenar contraseñas
   reales (nunca commitear). Confirmar `MIRROR_TABLES_SQLSERVER`.
4. `python gb10/init_ara_llm.py` — crea Capa 3 (SQLite).
5. `python gb10/mirror_sqlserver_pruebas25.py --schema-only` luego
   `python gb10/mirror_mysql_barquisimeto.py --schema-only` — crean el
   esquema en los mirrors locales (Capa 2) sin datos.
6. `python gb10/validate_3capas.py` — debe reportar 3/3 capas OK.
7. Arrancar el sync continuo: `python gb10/sync_cron.py` (o como servicio).
8. Apuntar `ConnectionWrapper` de PHP a la Capa 2 vía env
   (`PROFIT_SQL_HOST=127.0.0.1`, `MYSQL_HOST=127.0.0.1`, etc. — **NO** editar
   los defaults hardcodeados en el código, solo las env vars del proceso).
9. Arrancar `ara_server.py` normalmente.

## Restricciones (heredadas del mandato original, sin cambios)

- NUNCA escribir en `PRUEB25` ni en `barquisimeto` desde la GB10 (Capa 2 es
  solo lectura respecto a Capa 1; solo el sync escribe, y solo en el mirror
  local).
- NUNCA hardcodear contraseñas.
- NO modificar el esquema real de `PRUEB25` ni `barquisimeto`.
- Si una tabla o dato no está confirmado, detenerse y preguntar antes de
  espejarlo (ver sección "Tablas a espejar").
