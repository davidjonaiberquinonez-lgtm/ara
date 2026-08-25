<?php

declare(strict_types=1);

declare(ticks=1);

// ═══════════════════════════════════════════════════════════════════════
// HOTFIX v4.17.1 — LIBERACIÓN DE EMERGENCIA DE CONEXIONES KICKSERVER
// -----------------------------------------------------------------------
// Uso SOLO cuando Profit/SQL Server se pegue y haya que liberar de forma
// inmediata: mata TODOS los SPIDs de KICKSERVER con is_user_process = 1,
// sin importar el status (a diferencia de sql_kill_switch.php que solo
// mata sleeping > 10 min).
//
// Uso:
//   php bin/emergency_release.php            → ejecuta KILL y retorna JSON
//   php bin/emergency_release.php --dry-run  → lista SIN matar (seguro)
//
// Restricciones de seguridad:
//   - SOLO host_name = 'KICKSERVER' (nunca otros hosts).
//   - SOLO is_user_process = 1 (nunca sesiones del sistema SQL).
//   - Nunca se mata a sí mismo (session_id <> @@SPID).
//   - Máx 5s por pasada (candado anti-zombi + query timeout 4s).
// ═══════════════════════════════════════════════════════════════════════

$eR_maxS = (int) (getenv('ARA_CLI_MAX_S') ?: '5');
$eR_maxS = max(3, min(600, $eR_maxS));
set_time_limit($eR_maxS);
ini_set('max_execution_time', (string) $eR_maxS);
ini_set('memory_limit', '64M');

$eR_inicio = microtime(true);
register_shutdown_function(static function (): void {
    gc_collect_cycles();
});
register_tick_function(static function () use ($eR_inicio, $eR_maxS): void {
    if ((microtime(true) - $eR_inicio) > $eR_maxS) {
        if (defined('STDERR')) {
            fwrite(STDERR, '{"success":false,"error":"emergency_release: timeout preventivo (' . $eR_maxS . 's). Proceso abortado."}');
        }
        exit(1);
    }
});

$eR_dryRun = in_array('--dry-run', $_SERVER['argv'] ?? [], true);

if (!extension_loaded('odbc')) {
    fwrite(STDERR, '{"success":false,"error":"emergency_release: falta la extensión odbc."}' . PHP_EOL);
    exit(500);
}

/** Salida JSON de una línea (estándar de los runners del proyecto). */
function eR_emitir(array $payload, int $exitCode = 0): never
{
    $json = json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    echo ($json === false ? '{"success":false,"error":"Salida no serializable"}' : $json) . PHP_EOL;
    exit($exitCode);
}

// ── 1) Conexión a master ────────────────────────────────────────────────
$eR_dsn = 'Driver={SQL Server};Server=192.168.4.20,1433;Database=master;Connection Timeout=4';
$eR_conn = @odbc_connect($eR_dsn, 'profit', 'profit');
if ($eR_conn === false) {
    eR_emitir(['success' => false, 'error' => 'No se pudo conectar a master: ' . odbc_errormsg()], 1);
}
@odbc_setoption($eR_conn, 1, 0, 4); // SQL_ATTR_QUERY_TIMEOUT = 4s

// ── 2) TODOS los SPIDs de KICKSERVER (is_user_process = 1) ──────────────
$eR_sql = "SELECT session_id, DB_NAME(database_id) AS db_name, status,
                  DATEDIFF(SECOND, last_request_start_time, GETDATE()) AS dormido_sec
           FROM sys.dm_exec_sessions
           WHERE host_name = 'KICKSERVER'
             AND is_user_process = 1
             AND session_id <> @@SPID";
$eR_stmt = @odbc_exec($eR_conn, $eR_sql);
if ($eR_stmt === false) {
    $eR_msg = (string) odbc_errormsg($eR_conn);
    @odbc_close($eR_conn);
    eR_emitir(['success' => false, 'error' => 'Fallo al listar sesiones: ' . $eR_msg], 1);
}

$eR_spids = [];
while (($eR_fila = @odbc_fetch_array($eR_stmt)) !== false) {
    $eR_spids[] = (int) $eR_fila['session_id'];
}
@odbc_free_result($eR_stmt);

// ── 3) KILL de todos (o listado en dry-run) ─────────────────────────────
$eR_matados = [];
$eR_fallidos = [];
foreach ($eR_spids as $eR_spid) {
    if (!$eR_dryRun) {
        $eR_kill = @odbc_exec($eR_conn, 'KILL ' . $eR_spid);
        if ($eR_kill === false) {
            $eR_fallidos[] = ['spid' => $eR_spid, 'error' => (string) odbc_errormsg($eR_conn)];
            continue;
        }
        @odbc_free_result($eR_kill);
    }
    $eR_matados[] = $eR_spid;
}
@odbc_close($eR_conn);

// ── 4) Lista impresa + JSON de retorno ──────────────────────────────────
echo ($eR_dryRun ? '[DRY-RUN] SPIDs KICKSERVER candidatos: ' : 'SPIDs KICKSERVER liberados: ')
    . implode(', ', $eR_matados) . PHP_EOL;

eR_emitir([
    'success'  => true,
    'dry_run'  => $eR_dryRun,
    'released' => count($eR_matados),
    'spids'    => $eR_matados,
    'fallidos' => $eR_fallidos,
    'elapsed_ms' => (int) ((microtime(true) - $eR_inicio) * 1000),
], 0);
