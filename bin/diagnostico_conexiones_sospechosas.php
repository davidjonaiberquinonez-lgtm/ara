<?php
declare(strict_types=1);

/**
 * Diagnóstico manual, SOLO LECTURA, de TODAS las conexiones activas contra
 * el servidor SQL Server de Profit (192.168.4.20) — no solo PRUEB25/CRISTM25,
 * el servidor completo — para detectar hosts/logins/programas fuera de lo
 * conocido (posible acceso no autorizado o herramienta ajena al proyecto).
 *
 * Mismo candado anti-zombi que diagnostico_bloqueo_pruebas25.php: conexión
 * corta, sin pooling, cierre garantizado en finally, guard local que
 * rechaza cualquier SQL que no sea SELECT/lectura de sistema.
 *
 * Uso: php bin/diagnostico_conexiones_sospechosas.php
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

// Baseline de lo CONOCIDO/esperado para este proyecto — cualquier fila que no
// calce contra esto se marca como "REVISAR" en el reporte (no se bloquea
// nada, es solo una señal visual para el humano).
const HOSTS_CONOCIDOS = ['KICKSERVER', 'PROFITSERVER'];
const PROGRAMAS_CONOCIDOS_SUBSTR = [
    'ODBC', 'sqlsrv', 'PHP', '.Net SqlClient', 'Microsoft SQL Server Management Studio',
    'ARA', 'python', 'pyodbc', 'Report Server', 'SQLAgent',
];
const LOGINS_CONOCIDOS = ['profit', 'sa'];

function esConocido(string $valor, array $lista): bool
{
    $valor = mb_strtolower($valor);
    foreach ($lista as $c) {
        if (str_contains($valor, mb_strtolower($c))) {
            return true;
        }
    }
    return false;
}

$host = env_or('PROFIT_SQL_HOST', env_or('PROFIT_DB_HOST', '192.168.4.20'));
$port = env_or('PROFIT_SQL_PORT', env_or('PROFIT_DB_PORT', '1433'));
$user = env_or('PROFIT_SQL_USER', env_or('PROFIT_DB_USER', 'profit'));
$pass = env_or('PROFIT_SQL_PASS', env_or('PROFIT_DB_PASS', 'profit'));
$name = 'CRISTM25'; // conexión de servicio para leer DMV server-wide

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

    echo "=== 1) TODAS las sesiones de usuario activas en el servidor (todo el instance, no solo PRUEB25) ===\n";
    $sql1 = "
        SELECT s.session_id, s.login_name, s.host_name, s.program_name,
               DB_NAME(s.database_id) AS db_actual, s.status,
               c.client_net_address, c.auth_scheme, c.net_transport,
               s.login_time, s.last_request_start_time,
               DATEDIFF(MINUTE, s.login_time, GETDATE()) AS minutos_conectado
        FROM sys.dm_exec_sessions s
        LEFT JOIN sys.dm_exec_connections c ON c.session_id = s.session_id
        WHERE s.is_user_process = 1
        ORDER BY s.login_time DESC";
    solo_lectura($sql1);

    $filas = [];
    foreach ($pdo->query($sql1) as $row) {
        $filas[] = $row;
    }

    if ($filas === []) {
        echo "(no hay sesiones de usuario activas en este momento)\n";
    }

    $sospechosas = [];
    foreach ($filas as $row) {
        $host_s = (string) ($row['host_name'] ?? '');
        $prog_s = (string) ($row['program_name'] ?? '');
        $login_s = (string) ($row['login_name'] ?? '');
        $ip_s = (string) ($row['client_net_address'] ?? '');

        $hostOk = $host_s === '' || esConocido($host_s, HOSTS_CONOCIDOS);
        $progOk = $prog_s === '' || esConocido($prog_s, PROGRAMAS_CONOCIDOS_SUBSTR);
        $loginOk = $login_s === '' || esConocido($login_s, LOGINS_CONOCIDOS);
        $marca = ($hostOk && $progOk && $loginOk) ? 'OK' : 'REVISAR';

        if ($marca === 'REVISAR') {
            $sospechosas[] = $row;
        }

        echo json_encode([
            'marca'    => $marca,
            'spid'     => $row['session_id'],
            'login'    => $login_s,
            'host'     => $host_s,
            'ip'       => $ip_s,
            'programa' => $prog_s,
            'db'       => $row['db_actual'],
            'status'   => $row['status'],
            'auth'     => $row['auth_scheme'] ?? null,
            'transporte' => $row['net_transport'] ?? null,
            'conectado_hace_min' => $row['minutos_conectado'],
        ], JSON_UNESCAPED_UNICODE) . "\n";
    }

    echo "\n=== 2) RESUMEN ===\n";
    echo "Total sesiones de usuario: " . count($filas) . "\n";
    echo "Marcadas REVISAR (host/programa/login fuera del baseline conocido): " . count($sospechosas) . "\n";
    if ($sospechosas !== []) {
        echo "\n--- Detalle de las marcadas REVISAR ---\n";
        foreach ($sospechosas as $row) {
            echo json_encode($row, JSON_UNESCAPED_UNICODE) . "\n";
        }
    }

    echo "\n=== 3) Conexiones agrupadas por IP de origen (para ver concentración/orígenes raros) ===\n";
    $sql3 = "
        SELECT c.client_net_address, s.host_name, s.program_name, s.login_name,
               COUNT(*) AS conexiones
        FROM sys.dm_exec_sessions s
        JOIN sys.dm_exec_connections c ON c.session_id = s.session_id
        WHERE s.is_user_process = 1
        GROUP BY c.client_net_address, s.host_name, s.program_name, s.login_name
        ORDER BY COUNT(*) DESC";
    solo_lectura($sql3);
    foreach ($pdo->query($sql3) as $row) {
        echo json_encode($row, JSON_UNESCAPED_UNICODE) . "\n";
    }

    echo "\n=== 4) Intentos de login fallidos recientes (si el servidor audita, sys.dm_os_ring_buffers no siempre disponible sin permisos) ===\n";
    try {
        $sql4 = "SELECT TOP 20 login_name, host_name, program_name, login_time
                 FROM sys.dm_exec_sessions
                 WHERE is_user_process = 1
                 ORDER BY login_time DESC";
        solo_lectura($sql4);
        foreach ($pdo->query($sql4) as $row) {
            echo json_encode($row, JSON_UNESCAPED_UNICODE) . "\n";
        }
    } catch (\Throwable $e) {
        echo "(no disponible con los permisos de este login: " . $e->getMessage() . ")\n";
    }
} catch (\Throwable $e) {
    echo "ERROR: " . $e->getMessage() . "\n";
} finally {
    $pdo = null;
    gc_collect_cycles();
}
