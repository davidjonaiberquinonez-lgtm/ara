<?php
declare(ticks=1);
set_time_limit(60);

$server = "192.168.4.20";
$db = "PRUEB25"; // o master si PRUEB25 no responde
$user = "sa";    // ajusta
$pass = "";      // ajusta

$logFile = __DIR__ . "/../logs/diagnostico_420_" . date('Ymd_His') . ".txt";
if (!is_dir(dirname($logFile))) mkdir(dirname($logFile), 0777, true);

function logLine($fh, $line) {
    $ts = date('Y-m-d H:i:s');
    fwrite($fh, "[$ts] $line" . PHP_EOL);
    echo "[$ts] $line" . PHP_EOL;
}

$fh = fopen($logFile, 'w');
logLine($fh, "=== INICIO DIAGNOSTICO 192.168.4.20 ===");
logLine($fh, "Servidor: $server | DB: $db");

// Conexión
$conn = sqlsrv_connect($server, [
    "Database" => $db,
    "UID" => $user,
    "PWD" => $pass,
    "LoginTimeout" => 10,
    "ConnectTimeout" => 10,
    "ConnectionPooling" => 0   // HOTFIX v4.17.1: forzar cierre real (sin pool dormido)
]);

if (!$conn) {
    logLine($fh, "FALLO CONEXION: " . print_r(sqlsrv_errors(), true));
    exit(1);
}
logLine($fh, "Conexion OK");

// --- QUERY 1: SPIDs activos por I/O ---
logLine($fh, "--- SPIDs ACTIVOS ORDENADOS POR I/O ---");
$q1 = "SELECT 
    s.session_id,
    r.status,
    r.blocking_session_id,
    DB_NAME(r.database_id) AS db_name,
    s.login_name,
    s.host_name,
    s.program_name,
    r.cpu_time,
    r.total_elapsed_time / 1000.0 AS elapsed_sec,
    r.reads,
    r.writes,
    r.logical_reads,
    SUBSTRING(t.text, (r.statement_start_offset/2)+1,
        ((CASE r.statement_end_offset WHEN -1 THEN DATALENGTH(t.text) ELSE r.statement_end_offset END - r.statement_start_offset)/2) + 1) AS query_text,
    r.command,
    r.start_time
FROM sys.dm_exec_requests r
JOIN sys.dm_exec_sessions s ON r.session_id = s.session_id
CROSS APPLY sys.dm_exec_sql_text(r.sql_handle) t
WHERE s.is_user_process = 1 AND r.session_id <> @@SPID
ORDER BY r.reads + r.writes DESC";

$stmt = sqlsrv_query($conn, $q1);
if ($stmt) {
    $count = 0;
    while ($row = sqlsrv_fetch_array($stmt, SQLSRV_FETCH_ASSOC)) {
        $count++;
        logLine($fh, "SPID:{$row['session_id']} | DB:{$row['db_name']} | Host:{$row['host_name']} | App:{$row['program_name']} | Login:{$row['login_name']}");
        logLine($fh, "  Status:{$row['status']} | CPU:{$row['cpu_time']}ms | Reads:{$row['reads']} | Writes:{$row['writes']} | Elapsed:{$row['elapsed_sec']}s");
        logLine($fh, "  Query: " . substr($row['query_text'] ?? 'N/A', 0, 500));
        logLine($fh, str_repeat("-", 80));
    }
    if ($count === 0) logLine($fh, "No hay SPIDs de usuario activos.");
} else {
    logLine($fh, "Error Q1: " . print_r(sqlsrv_errors(), true));
}

// --- QUERY 2: Queries con mayor I/O histórico ---
logLine($fh, "");
logLine($fh, "--- TOP 20 QUERIES POR I/O HISTORICO ---");
$q2 = "SELECT TOP 20
    qs.execution_count,
    qs.total_worker_time / 1000 AS total_cpu_ms,
    qs.total_physical_reads,
    qs.total_logical_reads,
    qs.total_logical_writes,
    (qs.total_physical_reads + qs.total_logical_writes) AS total_io,
    SUBSTRING(qt.text, (qs.statement_start_offset/2)+1,
        ((CASE qs.statement_end_offset WHEN -1 THEN DATALENGTH(qt.text) ELSE qs.statement_end_offset END - qs.statement_start_offset)/2) + 1) AS query_text,
    DB_NAME(qt.dbid) AS db_name
FROM sys.dm_exec_query_stats qs
CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) qt
WHERE DB_NAME(qt.dbid) IN ('PRUEB25','CRISTM25','master')
ORDER BY (qs.total_physical_reads + qs.total_logical_writes) DESC";

$stmt = sqlsrv_query($conn, $q2);
if ($stmt) {
    while ($row = sqlsrv_fetch_array($stmt, SQLSRV_FETCH_ASSOC)) {
        logLine($fh, "DB:{$row['db_name']} | Execs:{$row['execution_count']} | CPU:{$row['total_cpu_ms']}ms | PhysReads:{$row['total_physical_reads']} | IO:{$row['total_io']}");
        logLine($fh, "Query: " . substr($row['query_text'] ?? 'N/A', 0, 500));
        logLine($fh, str_repeat("-", 80));
    }
} else {
    logLine($fh, "Error Q2: " . print_r(sqlsrv_errors(), true));
}

// --- QUERY 3: Conexiones por origen ---
logLine($fh, "");
logLine($fh, "--- CONEXIONES POR ORIGEN ---");
$q3 = "SELECT 
    s.host_name,
    c.client_net_address,
    s.program_name,
    s.login_name,
    DB_NAME(s.database_id) AS db,
    COUNT(*) AS conexiones_activas,
    MAX(s.last_request_start_time) AS ultima_actividad
FROM sys.dm_exec_sessions s
JOIN sys.dm_exec_connections c ON s.session_id = c.session_id
WHERE s.is_user_process = 1
GROUP BY s.host_name, c.client_net_address, s.program_name, s.login_name, DB_NAME(s.database_id)
ORDER BY COUNT(*) DESC";

$stmt = sqlsrv_query($conn, $q3);
if ($stmt) {
    while ($row = sqlsrv_fetch_array($stmt, SQLSRV_FETCH_ASSOC)) {
        $host = $row['host_name'] ?? 'NULL';
        $ip = $row['client_net_address'] ?? 'NULL';
        $prog = $row['program_name'] ?? 'NULL';
        $login = $row['login_name'] ?? 'NULL';
        $db = $row['db'] ?? 'NULL';
        $cnt = $row['conexiones_activas'];
        $last = ($row['ultima_actividad'] instanceof DateTime) ? $row['ultima_actividad']->format('Y-m-d H:i:s') : 'N/A';
        logLine($fh, "Host:$host | IP:$ip | Prog:$prog | Login:$login | DB:$db | Conexiones:$cnt | Ultima:$last");
    }
} else {
    logLine($fh, "Error Q3: " . print_r(sqlsrv_errors(), true));
}

// --- QUERY 4: SPIDs dormidos con I/O acumulada (sospechosos) ---
logLine($fh, "");
logLine($fh, "--- SPIDs DORMIDOS CON I/O (POSIBLES FANTASMAS) ---");
$q4 = "SELECT 
    s.session_id,
    s.host_name,
    s.program_name,
    s.login_name,
    DB_NAME(s.database_id) AS db,
    s.status,
    DATEDIFF(SECOND, s.last_request_start_time, GETDATE()) AS segundos_desde_ultima_actividad,
    c.connect_time,
    r.reads,
    r.writes
FROM sys.dm_exec_sessions s
LEFT JOIN sys.dm_exec_connections c ON s.session_id = c.session_id
LEFT JOIN sys.dm_exec_requests r ON s.session_id = r.session_id
WHERE s.is_user_process = 1
    AND s.status = 'sleeping'
    AND DATEDIFF(MINUTE, s.last_request_start_time, GETDATE()) > 5
ORDER BY (ISNULL(r.reads,0) + ISNULL(r.writes,0)) DESC";

$stmt = sqlsrv_query($conn, $q4);
if ($stmt) {
    $count = 0;
    while ($row = sqlsrv_fetch_array($stmt, SQLSRV_FETCH_ASSOC)) {
        $count++;
        logLine($fh, "SPID:{$row['session_id']} | Host:{$row['host_name']} | App:{$row['program_name']} | DB:{$row['db']}");
        logLine($fh, "  Dormido desde hace {$row['segundos_desde_ultima_actividad']}s | Reads:" . ($row['reads'] ?? 0) . " | Writes:" . ($row['writes'] ?? 0));
    }
    if ($count === 0) logLine($fh, "No hay SPIDs dormidos sospechosos.");
} else {
    logLine($fh, "Error Q4: " . print_r(sqlsrv_errors(), true));
}

sqlsrv_close($conn);
logLine($fh, "=== FIN DIAGNOSTICO ===");
logLine($fh, "Log guardado en: $logFile");
fclose($fh);

echo PHP_EOL . "Listo. Revisa: $logFile" . PHP_EOL;