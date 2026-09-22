<?php

declare(strict_types=1);

declare(ticks=1);

// ═══════════════════════════════════════════════════════════════════════
// HOTFIX v4.17.1 — KILL SWITCH CONEXIONES FANTASMA KICKSERVER
// AMPLIADO v4.47 — REGLA GENERAL DE BLOQUEADORES ACTIVOS
// -----------------------------------------------------------------------
// KICKSERVER (Apache) deja SPIDs sleeping acumulados que tumban el
// SQL Server (192.168.4.20). Este script, como parte del parche defensivo:
//   1. Conecta a master (profit/profit, SOLO sesiones/sys views).
//   2. Lista candidatos de DOS reglas independientes:
//      a) KICKSERVER: program_name LIKE '%Apache%', sleeping > 10 min.
//      b) BLOQUEADOR ACTIVO (v4.47, a pedido del usuario tras el incidente
//         real de 2026-08-12 con node-mssql en 192.168.4.23 dejando
//         transacciones sin COMMIT/ROLLBACK y tumbando en cascada a otras
//         10+ sesiones): cualquier SPID que sea AHORA MISMO el
//         blocking_session_id de otra sesión bloqueada por más de 15s
//         (30s si el programa bloqueador es 'JVT PEDIDOS', v4.53).
//         No filtra por host/programa — reacciona al COMPORTAMIENTO real
//         (está bloqueando a alguien), no a una lista fija de sospechosos.
//   3. Ejecuta KILL por cada uno (re-verificado justo antes de matar).
//   4. Loguea en logs/kill_switch_YYYY-MM-DD.txt, con el motivo de cada kill.
//   5. Si mata > 10 en una pasada, alerta a error_log.
//   NO toca datos de negocio: solo elimina conexiones dormidas o bloqueadoras.
//
// Uso:
//   php bin/sql_kill_switch.php            → ejecuta KILL de los fantasmas
//   php bin/sql_kill_switch.php --dry-run  → lista candidatos SIN matar
//                                            (verificación segura)
//
// Restricciones de seguridad:
//   - Regla KICKSERVER: SOLO host_name = 'KICKSERVER', SOLO sleeping > 10 min.
//   - Regla bloqueador activo: SOLO si es blocking_session_id verificado de
//     otra sesión ahora mismo, con más de 15s de espera acumulada (30s para
//     'JVT PEDIDOS') — nunca mata contención normal/momentánea (ej. un lock
//     de 1s que se resuelve solo).
//   - Nunca se mata a sí mismo (session_id <> @@SPID).
//   - Máx 5s por pasada (candado anti-zombi + query timeout 4s).
// ═══════════════════════════════════════════════════════════════════════

$kS_maxS = (int) (getenv('ARA_CLI_MAX_S') ?: '5');
$kS_maxS = max(3, min(600, $kS_maxS));
set_time_limit($kS_maxS);
ini_set('max_execution_time', (string) $kS_maxS);
ini_set('memory_limit', '64M');

$kS_inicio = microtime(true);
register_shutdown_function(static function (): void {
    gc_collect_cycles();
});
register_tick_function(static function () use ($kS_inicio, $kS_maxS): void {
    if ((microtime(true) - $kS_inicio) > $kS_maxS) {
        if (defined('STDERR')) {
            fwrite(STDERR, '{"success":false,"error":"sql_kill_switch: timeout preventivo (' . $kS_maxS . 's). Proceso abortado."}');
        }
        exit(1);
    }
});

$kS_dryRun = in_array('--dry-run', $_SERVER['argv'] ?? [], true);

if (!extension_loaded('odbc')) {
    fwrite(STDERR, '{"success":false,"error":"sql_kill_switch: falta la extensión odbc."}' . PHP_EOL);
    exit(500);
}

/** Salida JSON de una línea (estándar de los runners del proyecto). */
function kS_emitir(array $payload, int $exitCode = 0): never
{
    $json = json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    echo ($json === false ? '{"success":false,"error":"Salida no serializable"}' : $json) . PHP_EOL;
    exit($exitCode);
}

// ── 1) Conexión a master (solo necesita vistas del sistema) ──────────────
$kS_dsn = 'Driver={SQL Server};Server=192.168.4.20,1433;Database=master;Connection Timeout=4';
$kS_conn = @odbc_connect($kS_dsn, 'profit', 'profit');
if ($kS_conn === false) {
    kS_emitir(['success' => false, 'error' => 'No se pudo conectar a master: ' . odbc_errormsg()], 1);
}
@odbc_setoption($kS_conn, 1, 0, 4); // SQL_ATTR_QUERY_TIMEOUT = 4s

// ── 2a) Candidatos regla KICKSERVER: sleeping > 10 min, Apache ───────────
// DESACTIVADA a pedido explícito del usuario (2026-08-13): el dashboard
// (watchdog_dashboard_server.py) reveló que 36 de 42 kills históricos eran
// de esta regla — se pidió dejar de matar sesiones de Apache/KICKSERVER.
// Se deja el código intacto (solo el flag en false) para poder reactivarla
// fácilmente si el problema original de v4.17.1 vuelve a manifestarse.
$kS_matarKickserver = false;

$kS_candidatos = [];
if ($kS_matarKickserver) {
    $kS_sql = "SELECT s.session_id, DATEDIFF(SECOND, s.last_request_start_time, GETDATE()) AS dormido_sec,
                      s.host_name, s.login_name, s.program_name, c.client_net_address
               FROM sys.dm_exec_sessions s
               LEFT JOIN sys.dm_exec_connections c ON c.session_id = s.session_id
               WHERE s.host_name = 'KICKSERVER'
                 AND s.program_name LIKE '%Apache%'
                 AND s.status = 'sleeping'
                 AND DATEDIFF(MINUTE, s.last_request_start_time, GETDATE()) > 10
                 AND s.session_id <> @@SPID";
    $kS_stmt = @odbc_exec($kS_conn, $kS_sql);
    if ($kS_stmt === false) {
        $kS_msg = (string) odbc_errormsg($kS_conn);
        @odbc_close($kS_conn);
        kS_emitir(['success' => false, 'error' => 'Fallo al listar sesiones: ' . $kS_msg], 1);
    }

    while (($kS_fila = @odbc_fetch_array($kS_stmt)) !== false) {
        $kS_candidatos[] = [
            'spid'        => (int) $kS_fila['session_id'],
            'dormido_sec' => (int) $kS_fila['dormido_sec'],
            'motivo'      => 'KICKSERVER_APACHE_10MIN',
            'host'        => (string) ($kS_fila['host_name'] ?? ''),
            'ip'          => (string) ($kS_fila['client_net_address'] ?? ''),
            'login'       => (string) ($kS_fila['login_name'] ?? ''),
            'programa'    => (string) ($kS_fila['program_name'] ?? ''),
        ];
    }
    @odbc_free_result($kS_stmt);
}

// ── 2b) Candidatos regla BLOQUEADOR ACTIVO (v4.47): quien esté AHORA MISMO
//        bloqueando a otra sesión por más de 15s, sin importar host/programa.
//        DISTINCT porque un mismo SPID puede bloquear a varias víctimas a
//        la vez (una fila por víctima en dm_exec_requests).
// AMPLIADO v4.51 — se agrega el texto SQL real (most_recent_sql_handle) de
// cada candidato ANTES de matarlo. Motivo: el SPID se recicla en cuanto la
// sesión se desconecta (SQL Server reasigna el mismo número a otra conexión
// distinta segundos después), así que si no capturamos la query en el
// instante del KILL, esa evidencia se pierde para siempre — consultarla
// después con un SPID viejo trae la sesión NUEVA que ocupa ese número, no
// la que causó el bloqueo. Este texto se archiva aparte, en
// logs/evidencia_bloqueos_sql/, para sustentar reportes al equipo dueño
// de la app que sigue dejando transacciones sin cerrar.
// AMPLIADO v4.52 — se suma sys.dm_exec_input_buffer: sql_text solo trae la
// plantilla con @parámetros (sin valores reales); el input_buffer trae lo
// que el cliente mandó tal cual, con los valores reales de esa consulta
// puntual (ej. qué co_cli, qué fact_num) — evidencia mucho más útil que
// solo la plantilla para sustentar un reporte al dueño de la app.
// AMPLIADO v4.53 — a pedido del usuario, los procesos 'JVT PEDIDOS'
// (verificado en vivo contra sys.dm_exec_sessions: program_name = 'JVT
// PEDIDOS', usado por los ejecutivos de venta desde sus PCs EJECUTIVO-*/
// VENT-*/DESKTOP-*) reciben 15s MÁS de tolerancia que el resto antes de ser
// candidatos a KILL, porque sus transacciones de pedido tardan naturalmente
// más que una consulta de otro sistema.
// AMPLIADO v4.65 (16/09) — a pedido del usuario, umbral general subido de
// 15s a 45s: se detectó "profit-api" (host EXODO/.23) creando cotizaciones
// reales (INSERT INTO cotiz_c) con bloqueos de 15-28s que ya caían en el
// umbral viejo y se mataban a mitad de una escritura legítima. JVT sigue
// con +15s sobre el general (ahora 60s) para preservar la misma tolerancia
// relativa que tenía antes.
$kS_sqlBloq = "SELECT b.session_id, b.dormido_sec,
                      s.host_name, s.login_name, s.program_name, c.client_net_address,
                      t.text AS sql_text, ib.event_info AS input_buffer
               FROM (
                   SELECT r.blocking_session_id AS session_id,
                          MAX(r.wait_time) / 1000 AS dormido_sec
                   FROM sys.dm_exec_requests r
                   JOIN sys.dm_exec_sessions bs ON bs.session_id = r.blocking_session_id
                   WHERE r.blocking_session_id <> 0
                     AND r.blocking_session_id <> @@SPID
                     AND r.wait_time > (CASE WHEN bs.program_name LIKE '%JVT%' THEN 60000 ELSE 45000 END)
                   GROUP BY r.blocking_session_id
               ) b
               JOIN sys.dm_exec_sessions s ON s.session_id = b.session_id
               LEFT JOIN sys.dm_exec_connections c ON c.session_id = b.session_id
               OUTER APPLY sys.dm_exec_sql_text(c.most_recent_sql_handle) t
               OUTER APPLY sys.dm_exec_input_buffer(b.session_id, NULL) ib";
$kS_stmtBloq = @odbc_exec($kS_conn, $kS_sqlBloq);
if ($kS_stmtBloq !== false) {
    while (($kS_fila = @odbc_fetch_array($kS_stmtBloq)) !== false) {
        $kS_candidatos[] = [
            'spid'          => (int) $kS_fila['session_id'],
            'dormido_sec'   => (int) $kS_fila['dormido_sec'],
            'motivo'        => 'BLOQUEADOR_ACTIVO_45S',
            'host'          => (string) ($kS_fila['host_name'] ?? ''),
            'ip'            => (string) ($kS_fila['client_net_address'] ?? ''),
            'login'         => (string) ($kS_fila['login_name'] ?? ''),
            'programa'      => (string) ($kS_fila['program_name'] ?? ''),
            'sql_text'      => (string) ($kS_fila['sql_text'] ?? ''),
            'input_buffer'  => (string) ($kS_fila['input_buffer'] ?? ''),
        ];
    }
    @odbc_free_result($kS_stmtBloq);
}
// Si esta consulta falla no aborta el script entero — la regla KICKSERVER
// original sigue funcionando igual que antes de esta ampliación.

// ── 3) KILL por cada candidato (o solo listado en dry-run) ───────────────
$kS_matados = [];
$kS_fallidos = [];
foreach ($kS_candidatos as $kS_c) {
    $kS_spid = $kS_c['spid'];
    if (!$kS_dryRun) {
        $kS_kill = @odbc_exec($kS_conn, 'KILL ' . $kS_spid);
        if ($kS_kill === false) {
            // La sesión pudo morir entre el SELECT y el KILL (6106/2084): no es fatal.
            $kS_fallidos[] = ['spid' => $kS_spid, 'error' => (string) odbc_errormsg($kS_conn)];
            continue;
        }
        @odbc_free_result($kS_kill);
        $kS_matados[] = $kS_c;
    } else {
        $kS_matados[] = $kS_c;
    }
}
@odbc_close($kS_conn);

// ── 4) Log en logs/kill_switch_YYYY-MM-DD.txt ────────────────────────────
$kS_logDir = __DIR__ . '/../logs';
if (!is_dir($kS_logDir)) {
    @mkdir($kS_logDir, 0777, true);
}
$kS_logFile = $kS_logDir . '/kill_switch_' . date('Y-m-d') . '.txt';
$kS_lineas = [];
foreach ($kS_matados as $kS_c) {
    $kS_lineas[] = sprintf(
        '%s | SPID %d | dormido/bloqueando %d s | motivo %s | host %s | ip %s | login %s | programa %s',
        date('Y-m-d H:i:s'),
        $kS_c['spid'],
        $kS_c['dormido_sec'],
        $kS_c['motivo'] ?? 'DESCONOCIDO',
        $kS_c['host'] ?? '',
        $kS_c['ip'] ?? '',
        $kS_c['login'] ?? '',
        $kS_c['programa'] ?? ''
    );
}

if ($kS_dryRun) {
    $kS_lineas[] = sprintf(
        'Scan OK, %d candidato(s) detectado(s) (dry-run, SIN KILL)',
        count($kS_candidatos)
    );
} elseif ($kS_matados === []) {
    $kS_lineas[] = 'Scan OK, 0 kills';
}

if ($kS_lineas !== []) {
    @file_put_contents($kS_logFile, implode(PHP_EOL, $kS_lineas) . PHP_EOL, FILE_APPEND | LOCK_EX);
}

// ── 4b) Evidencia con texto SQL completo (v4.51) ─────────────────────────
// Archivo aparte, una línea JSON por kill, para no romper el parser del
// log de metadata (kill_switch_*.txt) que usa el dashboard. El SPID solo,
// sin este texto, deja de servir como evidencia en cuanto SQL Server lo
// recicla — esto es lo que sustenta un reclamo formal al dueño de la app.
if (!$kS_dryRun && $kS_matados !== []) {
    $kS_evidDir = $kS_logDir . '/evidencia_bloqueos_sql';
    if (!is_dir($kS_evidDir)) {
        @mkdir($kS_evidDir, 0777, true);
    }
    $kS_evidFile = $kS_evidDir . '/' . date('Y-m-d') . '.jsonl';
    $kS_evidLineas = [];
    foreach ($kS_matados as $kS_c) {
        if (($kS_c['sql_text'] ?? '') === '') {
            continue; // nada que archivar (regla KICKSERVER no captura sql_text)
        }
        $kS_evidLineas[] = json_encode([
            'fecha'         => date('Y-m-d H:i:s'),
            'spid'          => $kS_c['spid'],
            'dormido_sec'   => $kS_c['dormido_sec'],
            'motivo'        => $kS_c['motivo'] ?? 'DESCONOCIDO',
            'host'          => $kS_c['host'] ?? '',
            'ip'            => $kS_c['ip'] ?? '',
            'login'         => $kS_c['login'] ?? '',
            'programa'      => $kS_c['programa'] ?? '',
            'sql_text'      => $kS_c['sql_text'],
            'input_buffer'  => $kS_c['input_buffer'] ?? '',
        ], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    }
    if ($kS_evidLineas !== []) {
        @file_put_contents($kS_evidFile, implode(PHP_EOL, $kS_evidLineas) . PHP_EOL, FILE_APPEND | LOCK_EX);
    }
}

// ── 5) Alerta si se mató más de 10 en una pasada ─────────────────────────
if (!$kS_dryRun && count($kS_matados) > 10) {
    error_log(sprintf('KILL_SWITCH_ALERT: Se mataron %d conexiones fantasmas de KICKSERVER', count($kS_matados)));
}

kS_emitir([
    'success'   => true,
    'dry_run'   => $kS_dryRun,
    'scanned'   => count($kS_candidatos),
    'killed'    => count($kS_matados),
    'spids'     => array_column($kS_matados, 'spid'),
    'fallidos'  => $kS_fallidos,
    'log'       => $kS_logFile,
    'elapsed_ms'=> (int) ((microtime(true) - $kS_inicio) * 1000),
], 0);
