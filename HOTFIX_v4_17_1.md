# HOTFIX v4.17.1 — PARCHE DEFENSA: KILL SWITCH PARA CONEXIONES FANTASMA DE KICKSERVER

**Fecha:** 2026-08-11 · **Servidor SQL:** 192.168.4.20,1433 (PROFITSERVER) · **Estado:** listo para producción

---

## 1. Resumen del problema

**KICKSERVER** (servidor Apache que sirve el ERP Profit) abre conexiones SQL Server que **no cierra**: quedan SPIDs en estado `sleeping` acumulados en `sys.dm_exec_sessions`. Al acumularse cientos de estas conexiones fantasma, SQL Server se satura y **se tumba**.

Evidencia en vivo (verificación previa al hotfix, `master`):
```
session_id | host_name  | program_name          | db       | status   | dormido_sec
---------- | ---------- | --------------------- | -------- | -------- | -----------
95         | KICKSERVER | Apache HTTP Server    | CRISTM25 | sleeping | 122
188        | KICKSERVER | Apache HTTP Server    | CRISTM25 | sleeping | 284
...        | KICKSERVER | Apache HTTP Server    | CRISTM25 | sleeping | ...
TOTAL KICKSERVER = 15 sesiones vivas
```
De ellas, **10 llevaban más de 10 minutos dormidas** (elegibles para el kill switch).

El parche tiene **dos frentes** (dos "Prompts" de código, según el plan de la orden):

- **Prompt A — cierre forzado en código:** hace que el código de aplicación (Apache/PHP) cierre correctamente las conexiones ODBC al terminar cada request, para que no se acumulen. Es la causa raíz.
- **Prompt B — kill switch defensivo:** un vigilante independiente que detecta y mata las conexiones fantasma que *ya* quedaron acumuladas, como red de seguridad aunque Prompt A tarde o falle.

Este documento cubre **Prompt B** (los entregables del HOTFIX v4.17.1) y referencia cómo monitorear junto al healthcheck de Prompt A.

---

## 2. Qué hace cada componente

### 2.1 Prompt A — cierre forzado en código (referencia)
- Responsable de que cada request de KICKSERVER cierre su conexión ODBC en un `finally` (nunca dejar `sleeping`).
- **Cómo monitorear:** ejecutar `bin/healthcheck_connections.php` (del Prompt A) para ver cuántas conexiones quedan abiertas/dormidas después de un período de tráfico.

### 2.2 Prompt B — kill switch defensivo (ESTE HOTFIX)

**`bin/sql_kill_switch.php`** — vigilante automático:
1. Conecta a `master` (192.168.4.20,1433, profit/profit).
2. Busca SPIDs con `host_name = 'KICKSERVER'` y `program_name LIKE '%Apache%'`, `status = 'sleeping'`, dormidos **> 10 minutos**.
3. Ejecuta `KILL <session_id>` por cada uno.
4. Loguea en `logs/kill_switch_YYYY-MM-DD.txt`: `fecha | SPID | dormido s`.
5. Si no hay fantasmas → loguea `Scan OK, 0 kills`.
6. Si mata **> 10** en una pasada → alerta en `error_log`: `KILL_SWITCH_ALERT: Se mataron N conexiones fantasmas de KICKSERVER`.
7. Ejecución **< 5 s** garantizada (candado anti-zombi + query timeout 4 s).

**`bin/emergency_release.php`** — liberación de emergencia (manual):
- Mata **TODOS** los SPIDs `host_name = 'KICKSERVER'` con `is_user_process = 1`, **sin importar el status**.
- Imprime la lista de SPIDs y retorna JSON: `{"released": N, "spids": [51, 77, ...]}`.
- **Usar solo cuando Profit se pegue** y haya que liberar inmediatamente.

**`database/job_kill_switch.sql`** — refuerzo a nivel SQL Server:
- Tabla `dbo.ara_log_kills` (auditoría de cada KILL: session_id, host_name, program_name, db_name, dormido_seg, killed_at).
- SP `dbo.sp_ara_kill_sleeping_kickserver` (inserta en `ara_log_kills` antes de cada KILL y retorna cuántos mató).
- SQL Agent Job **`ARA_KillSwitch_Kickserver`** que corre el SP **cada 5 minutos**.
- **Si no hay permisos de SQL Agent:** comentar el bloque del job; quedan tabla + SP como opción manual.

---

## 3. Cómo ejecutar `emergency_release.php`

Solo cuando el Profit esté pegado y se requiera liberación inmediata:

```powershell
# PowerShell (modo seguro: lista candidatos SIN matar)
php bin/emergency_release.php --dry-run

# Liberación real: mata TODOS los SPIDs KICKSERVER is_user_process=1
php bin/emergency_release.php
```

Salida esperada:
```
SPIDs KICKSERVER liberados: 95, 188, 274, ...
{"success":true,"dry_run":false,"released":15,"spids":[95,188,274,345,...],"fallidos":[],"elapsed_ms":120}
```

> ⚠️ `emergency_release.php` mata **sin importar el status** (incluso sesiones activas de KICKSERVER). Por eso solo debe usarse en emergencia; el uso rutinario es `sql_kill_switch.php` (solo sleeping > 10 min).

---

## 4. Cómo ejecutar y monitorear

### Kill switch (rutina)
```powershell
# Modo seguro de verificación (lista sin matar)
php bin/sql_kill_switch.php --dry-run

# Ejecución real (mata sleeping > 10 min y loguea)
php bin/sql_kill_switch.php
```
Salida esperada:
```json
{"success":true,"dry_run":false,"scanned":10,"killed":10,"spids":[95,188,274,345,370,378,379,380,381,391],"fallidos":[],"log":"C:\\ARA_PROYECT\\logs\\kill_switch_2026-08-11.txt","elapsed_ms":120}
```
Si no hay fantasmas: `{"scanned":0,"killed":0,...}` y el log registra `Scan OK, 0 kills`.

### Log de auditoría
```powershell
Get-Content logs\kill_switch_2026-08-11.txt
```
Cada línea: `2026-08-11 14:37:05 | SPID 95 | dormido 122 s`

### Monitoreo combinado (rutina recomendada)
1. **Prompt A:** `php bin/healthcheck_connections.php` — cuántas conexiones quedan dormidas.
2. **Prompt B:** `php bin/sql_kill_switch.php` — mata y loguea los fantasmas > 10 min.
3. **SQL Server (auditoría del SP):**
   ```sql
   SELECT * FROM master.dbo.ara_log_kills ORDER BY id DESC;
   EXEC master.dbo.sp_ara_kill_sleeping_kickserver;
   ```

### Automatización
- **Opción A (recomendada si hay permisos):** ejecutar `database/job_kill_switch.sql` para crear el Job `ARA_KillSwitch_Kickserver` cada 5 min.
- **Opción B (sin SQL Agent):** programar en el Programador de Tareas de Windows:
  - `C:\tools\php\php.exe C:\ARA_PROYECT\bin\sql_kill_switch.php`
  - Frecuencia sugerida: cada 5 minutos.

---

## 5. Criterios de aceptación — verificación

| Criterio | Estado |
|---|---|
| `sql_kill_switch.php` ejecuta sin errores y loguea | ✅ (dry-run: `scanned:10`, log creado) |
| `emergency_release.php` ejecuta y retorna JSON válido con SPIDs | ✅ (dry-run: `released:15`, JSON válido) |
| `database/job_kill_switch.sql` ejecutable (tabla + SP al menos) | ✅ (sintaxis validada; job requiere SQLAgentOperatorRole — comentable) |
| Ningún cambio afecta datos de negocio | ✅ SELECT/KILL solo sobre `sys.dm_exec_sessions`; escrituras solo en `master.ara_log_kills` y logs locales |

---

## 6. NO HACER (restricciones del parche)

- ❌ No matar SPIDs de otros hosts (solo `KICKSERVER`).
- ❌ No matar SPIDs `running` en `sql_kill_switch.php` (solo `sleeping` > 10 min).
- ❌ No escribir en tablas de negocio (PRUEB25/CRISTM25) — solo `master`/`msdb`/logs locales.

---

## 7. Archivos del hotfix

| Archivo | Descripción |
|---|---|
| `bin/sql_kill_switch.php` | Kill switch automático (sleeping > 10 min, log + alerta) |
| `bin/emergency_release.php` | Liberación manual de emergencia (todos los SPIDs KICKSERVER) |
| `database/job_kill_switch.sql` | Tabla `ara_log_kills` + SP + SQL Agent Job cada 5 min |
| `logs/kill_switch_YYYY-MM-DD.txt` | Log de auditoría (se genera al ejecutar) |
| `HOTFIX_v4_17_1.md` | Este documento |
