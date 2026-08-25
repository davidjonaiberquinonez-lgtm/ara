<?php
declare(strict_types=1);

/**
 * Diagnóstico manual, SOLO LECTURA, de bloqueos/locks sobre PRUEB25.
 *
 * PRUEB25 rechaza el login directamente ("Cannot open database"), así que no
 * se puede conectar a PRUEB25 para preguntarle quién la tiene tomada. Las DMV
 * de SQL Server (sys.dm_tran_locks, sys.dm_exec_sessions, sys.dm_exec_requests)
 * son de ALCANCE SERVIDOR, no de base — se consultan conectado a CRISTM25
 * (que sí responde, mismo servidor .20) filtrando por DB_ID('PRUEB25').
 *
 * NO pasa por ConnectionWrapper (bloquea CRISTM25 a propósito). Candado
 * anti-zombi manual: timeout corto, sin conexión persistente, cierre
 * garantizado en finally, guard local que rechaza cualquier SQL que no sea
 * SELECT/consulta de sistema de solo lectura.
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
$name = 'CRISTM25'; // conexión de servicio para leer DMV server-wide, no para tocar datos de CRISTM25

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

    echo "=== 1) Sesiones abiertas contra PRUEB25 (login/host/programa/estado) ===\n";
    $sql1 = "
        SELECT s.session_id, s.login_name, s.host_name, s.program_name,
               s.status, s.last_request_start_time, s.last_request_end_time,
               c.client_net_address
        FROM sys.dm_exec_sessions s
        LEFT JOIN sys.dm_exec_connections c ON c.session_id = s.session_id
        WHERE s.database_id = DB_ID('PRUEB25')
        ORDER BY s.last_request_start_time DESC";
    solo_lectura($sql1);
    foreach ($pdo->query($sql1) as $row) {
        echo json_encode($row, JSON_UNESCAPED_UNICODE) . "\n";
    }

    echo "\n=== 2) Locks activos sobre PRUEB25 (quién tiene qué tomado) ===\n";
    $sql2 = "
        SELECT l.request_session_id, l.resource_type, l.resource_subtype,
               l.request_mode, l.request_status,
               s.login_name, s.host_name, s.program_name
        FROM sys.dm_tran_locks l
        JOIN sys.dm_exec_sessions s ON s.session_id = l.request_session_id
        WHERE l.resource_database_id = DB_ID('PRUEB25')
        ORDER BY l.request_session_id";
    solo_lectura($sql2);
    $huboLocks = false;
    foreach ($pdo->query($sql2) as $row) {
        $huboLocks = true;
        echo json_encode($row, JSON_UNESCAPED_UNICODE) . "\n";
    }
    if (!$huboLocks) {
        echo "(sin locks activos reportados sobre PRUEB25 en este momento)\n";
    }

    echo "\n=== 3) Requests en ejecución/bloqueados contra PRUEB25 + texto SQL ===\n";
    $sql3 = "
        SELECT r.session_id, r.blocking_session_id, r.status, r.command,
               r.wait_type, r.wait_time, r.cpu_time, r.total_elapsed_time,
               s.login_name, s.host_name, s.program_name,
               SUBSTRING(t.text, 1, 500) AS sql_text
        FROM sys.dm_exec_requests r
        JOIN sys.dm_exec_sessions s ON s.session_id = r.session_id
        CROSS APPLY sys.dm_exec_sql_text(r.sql_handle) t
        WHERE r.database_id = DB_ID('PRUEB25')
        ORDER BY r.total_elapsed_time DESC";
    solo_lectura($sql3);
    $huboRequests = false;
    foreach ($pdo->query($sql3) as $row) {
        $huboRequests = true;
        echo json_encode($row, JSON_UNESCAPED_UNICODE) . "\n";
    }
    if (!$huboRequests) {
        echo "(sin requests activos contra PRUEB25 en este momento)\n";
    }

    echo "\n=== 4) Estado de la base PRUEB25 (online/restoring/single_user/etc.) ===\n";
    $sql4 = "SELECT name, state_desc, user_access_desc, is_read_only FROM sys.databases WHERE name = 'PRUEB25'";
    solo_lectura($sql4);
    $encontrada = false;
    foreach ($pdo->query($sql4) as $row) {
        $encontrada = true;
        echo json_encode($row, JSON_UNESCAPED_UNICODE) . "\n";
    }
    if (!$encontrada) {
        echo "(PRUEB25 NO aparece en sys.databases)\n";
    }

    echo "\n=== 5) TODAS las bases visibles en este servidor desde este login ===\n";
    $sql5 = "SELECT name, state_desc, create_date FROM sys.databases ORDER BY name";
    solo_lectura($sql5);
    foreach ($pdo->query($sql5) as $row) {
        echo json_encode($row, JSON_UNESCAPED_UNICODE) . "\n";
    }

    echo "\n=== 6) Permisos del login actual (server-level) ===\n";
    $sql6 = "SELECT permission_name, state_desc FROM sys.server_permissions p
             JOIN sys.server_principals sp ON sp.principal_id = p.grantee_principal_id
             WHERE sp.name = SUSER_SNAME()";
    solo_lectura($sql6);
    foreach ($pdo->query($sql6) as $row) {
        echo json_encode($row, JSON_UNESCAPED_UNICODE) . "\n";
    }
    echo "\n=== 7) ¿CRISTM25 todavía tiene datos reales del ERP? (creada hoy 00:45) ===\n";
    $sql7 = "SELECT
        (SELECT COUNT(*) FROM dbo.clientes) AS clientes,
        (SELECT COUNT(*) FROM dbo.factura) AS facturas,
        (SELECT COUNT(*) FROM dbo.art) AS articulos,
        (SELECT COUNT(*) FROM dbo.not_ent) AS notas_entrega";
    solo_lectura($sql7);
    foreach ($pdo->query($sql7) as $row) {
        echo json_encode($row, JSON_UNESCAPED_UNICODE) . "\n";
    }

    echo "\n=== 8) Server info (¿es el mismo host de siempre?) ===\n";
    $sql8 = "SELECT @@SERVERNAME AS servername, SERVERPROPERTY('MachineName') AS machine, SERVERPROPERTY('ProductVersion') AS version, SERVERPROPERTY('Edition') AS edition";
    solo_lectura($sql8);
    foreach ($pdo->query($sql8) as $row) {
        echo json_encode($row, JSON_UNESCAPED_UNICODE) . "\n";
    }
} catch (\Throwable $e) {
    echo "ERROR: " . $e->getMessage() . "\n";
} finally {
    $pdo = null;
    gc_collect_cycles();
}
