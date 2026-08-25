<?php
declare(strict_types=1);

/**
 * Diagnóstico manual, SOLO LECTURA, de cadenas de BLOQUEO reales en TODO el
 * servidor (no solo PRUEB25/CRISTM25) — para "profit se pegó": encuentra
 * quién está bloqueando a quién ahora mismo, con texto de consulta y tiempo
 * de espera, para identificar el SPID raíz responsable.
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

$host = env_or('PROFIT_SQL_HOST', env_or('PROFIT_DB_HOST', '192.168.4.20'));
$port = env_or('PROFIT_SQL_PORT', env_or('PROFIT_DB_PORT', '1433'));
$user = env_or('PROFIT_SQL_USER', env_or('PROFIT_DB_USER', 'profit'));
$pass = env_or('PROFIT_SQL_PASS', env_or('PROFIT_DB_PASS', 'profit'));
$name = 'CRISTM25';

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

    echo "=== 1) Hora actual del servidor (para comparar contra login_time raros) ===\n";
    $sqlNow = "SELECT GETDATE() AS ahora_servidor";
    solo_lectura($sqlNow);
    foreach ($pdo->query($sqlNow) as $row) {
        echo json_encode($row, JSON_UNESCAPED_UNICODE) . "\n";
    }

    echo "\n=== 2) Cadenas de BLOQUEO reales ahora mismo (todo el servidor) ===\n";
    $sql2 = "
        SELECT r.session_id AS spid_bloqueado, r.blocking_session_id AS spid_bloqueador,
               r.wait_type, r.wait_time AS espera_ms, r.status, r.command,
               DB_NAME(r.database_id) AS db,
               s.login_name, s.host_name, s.program_name,
               SUBSTRING(t.text, 1, 400) AS sql_text
        FROM sys.dm_exec_requests r
        JOIN sys.dm_exec_sessions s ON s.session_id = r.session_id
        CROSS APPLY sys.dm_exec_sql_text(r.sql_handle) t
        WHERE r.blocking_session_id <> 0
        ORDER BY r.wait_time DESC";
    solo_lectura($sql2);
    $hubo = false;
    foreach ($pdo->query($sql2) as $row) {
        $hubo = true;
        echo json_encode($row, JSON_UNESCAPED_UNICODE) . "\n";
    }
    if (!$hubo) echo "(nadie está bloqueado por nadie en este momento)\n";

    echo "\n=== 3) Quiénes son los SPIDs bloqueadores raíz (cabeza de la cadena) + su consulta ===\n";
    $sql3 = "
        SELECT s.session_id, s.login_name, s.host_name, s.program_name, s.status,
               DB_NAME(s.database_id) AS db,
               DATEDIFF(SECOND, s.last_request_start_time, GETDATE()) AS segundos_desde_actividad,
               c.client_net_address, c.connect_time,
               SUBSTRING(t.text, 1, 400) AS ultima_query
        FROM sys.dm_exec_sessions s
        LEFT JOIN sys.dm_exec_connections c ON c.session_id = s.session_id
        OUTER APPLY sys.dm_exec_sql_text(c.most_recent_sql_handle) t
        WHERE s.session_id IN (
            SELECT DISTINCT blocking_session_id FROM sys.dm_exec_requests WHERE blocking_session_id <> 0
        )";
    solo_lectura($sql3);
    $hubo = false;
    foreach ($pdo->query($sql3) as $row) {
        $hubo = true;
        echo json_encode($row, JSON_UNESCAPED_UNICODE) . "\n";
    }
    if (!$hubo) echo "(sin bloqueadores raíz activos)\n";

    echo "\n=== 4) Requests con más de 30s corriendo AHORA (todo el servidor, no solo bloqueados) ===\n";
    $sql4 = "
        SELECT r.session_id, s.login_name, s.host_name, s.program_name,
               DB_NAME(r.database_id) AS db, r.status, r.command,
               r.total_elapsed_time / 1000.0 AS segundos_corriendo,
               SUBSTRING(t.text, 1, 400) AS sql_text
        FROM sys.dm_exec_requests r
        JOIN sys.dm_exec_sessions s ON s.session_id = r.session_id
        CROSS APPLY sys.dm_exec_sql_text(r.sql_handle) t
        WHERE r.total_elapsed_time > 30000 AND s.is_user_process = 1
        ORDER BY r.total_elapsed_time DESC";
    solo_lectura($sql4);
    $hubo = false;
    foreach ($pdo->query($sql4) as $row) {
        $hubo = true;
        echo json_encode($row, JSON_UNESCAPED_UNICODE) . "\n";
    }
    if (!$hubo) echo "(ninguna consulta lleva más de 30s corriendo)\n";

    echo "\n=== 5) Conteo total de sesiones activas por login (para ver si 'profit' está saturando el límite de conexiones) ===\n";
    $sql5 = "SELECT login_name, COUNT(*) AS total_sesiones,
                    SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) AS corriendo,
                    SUM(CASE WHEN status='sleeping' THEN 1 ELSE 0 END) AS dormidas
             FROM sys.dm_exec_sessions WHERE is_user_process = 1
             GROUP BY login_name ORDER BY COUNT(*) DESC";
    solo_lectura($sql5);
    foreach ($pdo->query($sql5) as $row) {
        echo json_encode($row, JSON_UNESCAPED_UNICODE) . "\n";
    }
} catch (\Throwable $e) {
    echo "ERROR: " . $e->getMessage() . "\n";
} finally {
    $pdo = null;
    gc_collect_cycles();
}
