<?php
declare(strict_types=1);

/**
 * Diagnóstico manual, SOLO LECTURA: dado un SPID, muestra QUÉ está
 * consultando exactamente en SQL Server — texto completo de la query
 * (no truncado a 400 chars como en el diagnóstico de bloqueo general),
 * más contexto: DB actual, login/host/programa, estado, tiempo de espera
 * y — si el comando lo soporta — los parámetros de la última ejecución.
 *
 * Uso:
 *   php bin/consultar_spid_query.php <spid>
 */

function env_or(string $k, string $default): string
{
    $v = getenv($k);
    return (is_string($v) && trim($v) !== '') ? trim($v) : $default;
}

function solo_lectura(string $sql): void
{
    if (preg_match('/\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|MERGE|KILL|GRANT|REVOKE)\b/i', $sql)) {
        throw new \InvalidArgumentException('Guard: palabra clave de escritura/administración detectada, abortado.');
    }
}

$spid = (int) ($argv[1] ?? 0);
if ($spid <= 0) {
    fwrite(STDERR, "Uso: php bin/consultar_spid_query.php <spid>\n");
    exit(1);
}

$host = env_or('PROFIT_SQL_HOST', env_or('PROFIT_DB_HOST', '192.168.4.20'));
$port = env_or('PROFIT_SQL_PORT', env_or('PROFIT_DB_PORT', '1433'));
$user = env_or('PROFIT_SQL_USER', env_or('PROFIT_DB_USER', 'profit'));
$pass = env_or('PROFIT_SQL_PASS', env_or('PROFIT_DB_PASS', 'profit'));
$name = 'CRISTM25'; // ancla de conexión solo para llegar a las DMV server-wide (solo lectura)

$drivers = PDO::getAvailableDrivers();
$dsn = in_array('sqlsrv', $drivers, true)
    ? "sqlsrv:Server=$host,$port;Database=$name;ConnectionPooling=0"
    : "odbc:Driver={SQL Server};Server=$host,$port;Database=$name";

$pdo = null;
try {
    $pdo = new PDO($dsn, $user, $pass, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_TIMEOUT => 8,
        PDO::ATTR_PERSISTENT => false,
    ]);

    echo "=== Sesión SPID $spid: identidad y estado ===\n";
    $sqlSesion = "
        SELECT s.session_id, s.login_name, s.host_name, s.program_name, s.status,
               DB_NAME(s.database_id) AS db_actual,
               s.login_time, s.last_request_start_time, s.last_request_end_time,
               c.client_net_address, c.connect_time
        FROM sys.dm_exec_sessions s
        LEFT JOIN sys.dm_exec_connections c ON c.session_id = s.session_id
        WHERE s.session_id = $spid";
    solo_lectura($sqlSesion);
    $sesion = null;
    foreach ($pdo->query($sqlSesion) as $row) {
        $sesion = $row;
        echo json_encode($row, JSON_UNESCAPED_UNICODE) . "\n";
    }
    if ($sesion === null) {
        echo "(SPID $spid no existe o ya se desconectó)\n";
        exit(0);
    }

    echo "\n=== Request en ejecución AHORA MISMO (si status=running), texto completo ===\n";
    $sqlActivo = "
        SELECT r.session_id, r.status, r.command, r.wait_type,
               r.blocking_session_id, r.wait_time AS espera_ms,
               r.total_elapsed_time / 1000.0 AS segundos_corriendo,
               r.percent_complete, DB_NAME(r.database_id) AS db,
               t.text AS sql_text_completo
        FROM sys.dm_exec_requests r
        CROSS APPLY sys.dm_exec_sql_text(r.sql_handle) t
        WHERE r.session_id = $spid";
    solo_lectura($sqlActivo);
    $hubo = false;
    foreach ($pdo->query($sqlActivo) as $row) {
        $hubo = true;
        echo json_encode($row, JSON_UNESCAPED_UNICODE) . "\n";
    }
    if (!$hubo) {
        echo "(no hay request corriendo ahora — sesión dormida/sleeping, ver última query abajo)\n";
    }

    echo "\n=== Última consulta ejecutada por este SPID (aunque esté sleeping), texto completo ===\n";
    $sqlUltima = "
        SELECT c.session_id, c.most_recent_sql_handle, t.text AS ultima_query_completa
        FROM sys.dm_exec_connections c
        OUTER APPLY sys.dm_exec_sql_text(c.most_recent_sql_handle) t
        WHERE c.session_id = $spid";
    solo_lectura($sqlUltima);
    $hubo = false;
    foreach ($pdo->query($sqlUltima) as $row) {
        $hubo = true;
        echo json_encode($row, JSON_UNESCAPED_UNICODE) . "\n";
    }
    if (!$hubo) echo "(sin registro de última consulta — puede ser conexión interna sin query de usuario)\n";

    echo "\n=== Plan de ejecución del batch actual (si sigue corriendo) — resumen de objetos/tablas tocadas ===\n";
    $sqlPlan = "
        SELECT r.session_id, qp.query_plan
        FROM sys.dm_exec_requests r
        OUTER APPLY sys.dm_exec_query_plan(r.plan_handle) qp
        WHERE r.session_id = $spid";
    solo_lectura($sqlPlan);
    $hubo = false;
    foreach ($pdo->query($sqlPlan) as $row) {
        $hubo = true;
        $plan = (string) ($row['query_plan'] ?? '');
        echo "session_id: {$row['session_id']}\n";
        echo $plan !== '' ? "(plan XML disponible, " . strlen($plan) . " bytes — abrir en SSMS para ver gráfico)\n" : "(sin plan activo)\n";
    }
    if (!$hubo) echo "(sin request activo, no hay plan)\n";
} catch (\Throwable $e) {
    echo "ERROR: " . $e->getMessage() . "\n";
} finally {
    $pdo = null;
    gc_collect_cycles();
}
