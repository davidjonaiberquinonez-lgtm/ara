# FASE 2.5 — DAEMONS / LONG-RUNNING: justificación de exención del timeout 300s

Fecha: 2026-08-11

Criterio de aceptación de la Tarea 1:
`rg -i "sqlsrv_connect|new PDO" --glob "*.php" | grep -v "bin/test_"` → los scripts
ejecutables (entry points) restantes DEBEN tener candado anti-zombi o estar documentados
aquí.

## 1. Scripts PHP ejecutables (entry points) — PROTEGIDOS en Fase 2.5

Candado anti-zombi aplicado (declare(ticks=1) + set_time_limit default 300s,
env `ARA_CLI_MAX_S` override tope 600s, watchdog por tick >240s → error_log
`FASE25_KILL:` + exit(1), shutdown con gc):

| Archivo | Conexión | Estado |
|---|---|---|
| `bin/ejecutar_tool_cli.php` | ConnectionWrapper (via tools) | Ya protegido (v4.14) |
| `public/api/whatsapp/webhook.php` | conectar_profit_read / ConnectionWrapper | Protegido ahora |
| `public/api/metricas.php` | conectar_profit_read (TecnologiaAdapter) | Protegido ahora |
| `chequeo/registro.php` | PDO MySQL legacy (barquisimeto) | Protegido ahora |
| `legacy_visor/registro.php` | PDO SQL Server ProfitPlus (stub plantilla) | Protegido ahora |
| `legacy_visor/lista.php` | PDO SQL Server ProfitPlus (stub plantilla) | Protegido ahora |

Nota: `chequeo/registro.php` y `legacy_visor/*.php` son endpoints web servidos por
Apache/PHP-FPM; el candado es además válido para invocación CLI puntual y no altera
lógica de negocio (solo añade límites de tiempo).

## 2. Clases / bibliotecas PHP — NO aplica candado a nivel de archivo

Estos archivos NO son scripts ejecutables: son clases, traits o helpers que
implementan su propio ciclo de vida y son invocados desde un entry point ya
protegido (runner `bin/ejecutar_tool_cli.php`, arneses `bin/test_*.php` o los
endpoints web de la sección 1). El candado anti-zombi vive en el entry point que los
invoca; inyectar `set_time_limit`/ticks dentro de una clase no tiene efecto global
sobre el proceso y rompería el patrón de las Skills.

Lista: `app/Services/ConnectionWrapper.php`, `app/Core/conectar_profit_read.php`,
`app/Services/NvidiaBrain/Adapters/BaseAdapter.php`,
`app/Services/NvidiaBrain/Tools/Almacen/AlmacenDbTrait.php`,
`app/Services/NvidiaBrain/Tools/Almacen/ProfitStockHelper.php`,
`app/Services/NvidiaBrain/Tools/Almacen/BultoCerradoRouterTool.php`,
`app/Services/NvidiaBrain/Tools/Almacen/StockSurtidoPrioritarioTool.php`,
`app/Services/NvidiaBrain/Tools/Almacen/BuscarInventarioTool.php`,
`app/Services/NvidiaBrain/Tools/Almacen/DetectorErroresNotaTool.php`,
`app/Services/NvidiaBrain/Tools/Almacen/FlujoNotasTiempoRealTool.php`,
`app/Services/NvidiaBrain/Tools/Almacen/MonitorModificacionesEliminacionesTool.php`,
`app/Services/NvidiaBrain/Tools/Almacen/RendimientoPreparadoresSmartAssignTool.php`,
`app/Services/NvidiaBrain/Tools/Almacen/TrazabilidadVidaUtilNotaTool.php`,
`app/Services/NvidiaBrain/Tools/Auditoria/ConsultarSaldoClienteTool.php`,
`app/Services/NvidiaBrain/Tools/Auditoria/DiscrepanciaTrasladosTool.php`,
`app/Services/NvidiaBrain/Tools/Compras/ReporteQuiebresComprasTool.php`,
`app/Services/NvidiaBrain/Tools/Despacho/ConsultarClienteTool.php`,
`app/Services/NvidiaBrain/Tools/Recepcion/ConciliarFacturaTool.php`,
`app/Services/NvidiaBrain/Tools/Orquestador/HermesChatTool.php`,
`app/Services/NvidiaBrain/Loggers/SqlServerIaLogger.php`,
`app/Http/Controllers/WhatsAppWebhookController.php`,
`src/Services/LegacyChequeoService.php`.

## 3. Fase 2.5 — hardening de conexiones SQL (T2/T3/T4/T5)

Esta sección documenta las garantías de la Fase 2.5 aplicadas además del candado
anti-zombi (sección 1), sobre `ConnectionWrapper` (el conector único del stack).

### T2 — Query timeout 30s (nativo ODBC + SQLSRV)
- `ConnectionWrapper` usa dispatcher `ejecutar()` → `ejecutarOdbc()` (driver real de
  esta máquina) / `ejecutarSqlSrv()` (producción). En ODBC el timeout se aplica con
  `odbc_setoption($conn, 1, 0, segundos)` (SQL_ATTR_QUERY_TIMEOUT a nivel conexión) —
  único mecanismo que corta `WAITFOR DELAY` en el driver legacy. Verificado en vivo:
  WAITFOR 35s → `ConnectionWrapperException` a los 30.0s (SQLSTATE S1T00).
- `$timeoutS` (login/conexión, env `PROFIT_CONNECT_TIMEOUT`, default 5s) y
  `$queryTimeout` (env `PROFIT_QUERY_TIMEOUT`, default 30s, tope 600s) se separaron en
  el constructor; públicos `setQueryTimeout()`/`getQueryTimeout()`.
- Bindings nombrados `:param` → `?` traducidos por `traducirBindings()` (compatible
  con `:c` de ConsultarClienteTool). `odbc_fetch_array` devuelve claves en minúsculas
  (idéntico case a PDO FETCH_ASSOC para co_art/art_des/clientes).

### T3 — Circuit breaker SP inexistente (SQLSTATE 2812)
- `ConnectionWrapper`: `spBloqueado()`, `registrarFalloSP()`, `resetFalloSP()` — 3
  fallos 2812 en ventana 60s → circuito abierto 300s (constantes `CB_*`). El arnés de
  prueba NO consulta `spBloqueado()` entre registros (consume el estado).
- `TecnologiaAdapter` integra el wrapper (lazy `circuitBreaker()`), fail-fast con
  `circuitoAbierto()` antes de ejecutar, registra fallo solo si `esSpInexistente()`
  (2812) y resetea tras éxito. Verificado: llamadas 1-3 ≈ 2ms c/u success=false,
  llamada 4 = 0ms fail-fast "circuito abierto".

### T4 — Prohibición de CRISTM25
- `ConnectionWrapper::validarEntorno()` lanza `EnvironmentException` si la BD resuelta
  es CRISTM25 (culpable = PROFIT_SQL_NAME o PROFIT_DB_NAME). El runtime resuelve la BD
  por prioridad PROFIT_SQL_NAME → PROFIT_DB_NAME, y las env reales del proceso
  prevalecen (getenv) — por eso `.env` puede contener `PROFIT_DB_NAME=CRISTM25` y el
  proceso resuelve PRUEB25 igualmente.
- `bin/verify_env.php`: chequea extensiones críticas (pdo_odbc|pdo_sqlsrv + odbc +
  curl/mbstring/json), valida no-CRISTM25, imprime config resuelta y queryTimeout.
  Exit 0/1/2/500. Verificado: normal OK (PRUEB25); forzando `PROFIT_SQL_NAME=CRISTM25`
  → exit 1 "Entorno prohibido".

### T5 — Mantenimiento SQL (DBA)
- `database/fase25_maintenance.sql` (idempotente): tabla `log_Fase25_Kills` (auditoría
  BC) + `sp_Fase25_MonitorConnections @top=100` + `sp_Fase25_KillSleeping @max_sleep_s=600
  @excluir_login=NULL` (cursor, audita antes de KILL, TRY/CATCH ignora SPIDs muertos) +
  `sp_Fase25_AlertThreshold @umbral=25`. Es guion para el DBA, no se ejecuta desde PHP.

## 4. Daemons / long-running Python — NO aplica candado PHP

Estos procesos corren 24/7 por diseño; el timeout de 300s NO aplica. Se protegen por
otros medios (fusible por subproceso, watchdog del sistema).

| Daemon | Archivo | Justificación / protección |
|---|---|---|
| Servidor web ARA | `ara/ARA_Brain/ara_server.py` | Flask/waitress long-running; las Skills PHP se ejecutan en subproceso con fusible propio (`_ejecutar_runner_tools`: Popen + kill 35s/300s según tool, propagación `ARA_CLI_MAX_S`). |
| Sincronizador de stock | `ara/ARA_Brain/vigilar_datos.py` | Loop periódico (60s stock / 300s ubicaciones) contra PRUEB25; NO batch. Conexión corta por ciclo con cierre en finally y try/except aislados. Tiene su propio temporizador de ciclo. |
| Orquestador Hermes | `C:\Users\Personal\AppData\Local\hermes` | hermes-agent (Python) long-running si se usa `hermes gateway`; el puente `hermes_chat` PHP corre con timeout propio 300s y proc_terminate. |

El watchdog de sistema (`bin/watchdog_mata_zombis.ps1`, tarea `\ARAWatchdogZombis`
cada 5 min) mata cualquier `php.exe`/`php-cgi.exe` vivo >60s (300s para delegadas
`python_*`/`hermes_chat`) como respaldo final para TODOS estos procesos, daemon o no.
