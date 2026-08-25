<?php

declare(strict_types=1);

declare(ticks=1);

// ═══════════════════════════════════════════════════════════════════════
// HOTFIX v4.17.1 — HEALTHCHECK DE CONEXIONES KICKSERVER
// -----------------------------------------------------------------------
// Verifica cuántas sesiones de usuario deja KICKSERVER dormidas en SQL
// Server (192.168.4.20). Si supera el umbral (15), es una ALERTA: sale 1
// y loguea HOTFIX_ALERT en error_log (para el watchdog/servicio externo).
//
// Uso (cron / tarea programada, cada 5 min):
//   php bin/healthcheck_connections.php
//
// Salida (STDOUT, una línea JSON):
//   {"status":"OK","kickserver_sessions":N,"threshold":15}
//   {"status":"ALERT","kickserver_sessions":N,"threshold":15}
//
// Códigos de salida:
//   0   OK (sesiones ≤ umbral)
//   1   ALERTA (sesiones > umbral) — loguea HOTFIX_ALERT
//   2   error inesperado / sin conexión
//   500 extensión odbc faltante
//
// Umbral configurable con env ARA_HC_THRESHOLD (default 15).
// ═══════════════════════════════════════════════════════════════════════

$hc_maxS = (int) (getenv('ARA_CLI_MAX_S') ?: '10');
$hc_maxS = max(3, min(600, $hc_maxS));
set_time_limit($hc_maxS);
ini_set('max_execution_time', (string) $hc_maxS);
ini_set('memory_limit', '64M');

$hc_inicio = microtime(true);
register_shutdown_function(static function (): void {
    // HOTFIX v4.17.1: cerrar conexiones SQL Server abiertas (sin pool dormido).
    if (isset($GLOBALS['hc_conn']) && is_resource($GLOBALS['hc_conn'])) {
        @odbc_close($GLOBALS['hc_conn']);
    }
    gc_collect_cycles();
});
register_tick_function(static function () use ($hc_inicio, $hc_maxS): void {
    if ((microtime(true) - $hc_inicio) > $hc_maxS) {
        if (defined('STDERR')) {
            fwrite(STDERR, '{"status":"ERROR","error":"healthcheck_connections: timeout preventivo (' . $hc_maxS . 's). Proceso abortado."}');
        }
        exit(2);
    }
});

$hc_umbral = max(1, (int) (getenv('ARA_HC_THRESHOLD') ?: '15'));

if (!extension_loaded('odbc')) {
    fwrite(STDERR, '{"status":"ERROR","error":"healthcheck_connections: falta la extensión odbc."}' . PHP_EOL);
    exit(500);
}

/** Salida JSON de una línea (estándar de los runners del proyecto). */
function hc_emitir(array $payload, int $exitCode): never
{
    $json = json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    echo ($json === false ? '{"status":"ERROR","error":"Salida no serializable"}' : $json) . PHP_EOL;
    exit($exitCode);
}

// ── 1) Conexión a master ────────────────────────────────────────────────
$hc_host = getenv('PROFIT_SQL_HOST') ?: getenv('PROFIT_DB_HOST') ?: '192.168.4.20';
$hc_port = getenv('PROFIT_SQL_PORT') ?: getenv('PROFIT_DB_PORT') ?: '1433';
$hc_user = getenv('PROFIT_SQL_USER') ?: getenv('PROFIT_DB_USER') ?: 'profit';
$hc_pass = getenv('PROFIT_SQL_PASS') ?: getenv('PROFIT_DB_PASS') ?: 'profit';
$hc_driver = getenv('PROFIT_SQL_DRIVER') ?: getenv('PROFIT_DB_DRIVER') ?: 'SQL Server';

$hc_dsn = 'Driver={' . $hc_driver . '};Server=' . $hc_host . ',' . $hc_port
        . ';Database=master;Connection Timeout=5';
$hc_conn = @odbc_connect($hc_dsn, $hc_user, $hc_pass);
if ($hc_conn === false) {
    hc_emitir(['status' => 'ERROR', 'error' => 'No se pudo conectar a master: ' . odbc_errormsg()], 2);
}
@odbc_setoption($hc_conn, 1, 0, 5); // SQL_ATTR_QUERY_TIMEOUT = 5s

// ── 2) Sesiones de usuario de KICKSERVER ────────────────────────────────
$hc_sql = "SELECT COUNT(*) AS total
           FROM sys.dm_exec_sessions
           WHERE host_name = 'KICKSERVER'
             AND is_user_process = 1
             AND session_id <> @@SPID";
$hc_stmt = @odbc_exec($hc_conn, $hc_sql);
if ($hc_stmt === false) {
    $hc_msg = (string) odbc_errormsg($hc_conn);
    @odbc_close($hc_conn);
    hc_emitir(['status' => 'ERROR', 'error' => 'Fallo al contar sesiones: ' . $hc_msg], 2);
}

$hc_total = 0;
if (($hc_fila = @odbc_fetch_array($hc_stmt)) !== false) {
    $hc_total = (int) ($hc_fila['total'] ?? 0);
}
@odbc_free_result($hc_stmt);
@odbc_close($hc_conn);

// ── 3) Umbral y salida ──────────────────────────────────────────────────
$hc_payload = [
    'status'             => $hc_total > $hc_umbral ? 'ALERT' : 'OK',
    'kickserver_sessions'=> $hc_total,
    'threshold'          => $hc_umbral,
    'elapsed_ms'         => (int) ((microtime(true) - $hc_inicio) * 1000),
];

if ($hc_total > $hc_umbral) {
    error_log(
        'HOTFIX_ALERT: ' . $hc_total . ' sesiones dormidas de KICKSERVER en '
        . $hc_host . ' (umbral ' . $hc_umbral . '). Ejecutar bin/sql_kill_switch.php'
    );
    hc_emitir($hc_payload, 1);
}

hc_emitir($hc_payload, 0);
