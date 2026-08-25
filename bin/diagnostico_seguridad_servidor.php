<?php
declare(strict_types=1);

/**
 * Diagnóstico manual, SOLO LECTURA, de superficie de seguridad del servidor
 * SQL Server de Profit (192.168.4.20) — segunda pasada, más allá de
 * conexiones activas: logins y sus roles/privilegios, xp_cmdshell,
 * servidores enlazados, SQL Agent jobs, y el error log reciente por
 * intentos de login fallidos.
 *
 * 192.168.4.23 ("Developer" / contenedor Docker con node-mssql/go-mssqldb)
 * confirmado como equipo de control central legítimo — no se marca aquí.
 *
 * Mismo candado anti-zombi: conexión corta, sin pooling, cierre garantizado,
 * guard local que rechaza SQL de escritura/administración.
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

    echo "=== 1) Logins del servidor: creación, último login, roles server-level ===\n";
    try {
        $sql1 = "
            SELECT sp.name, sp.type_desc, sp.is_disabled, sp.create_date, sp.modify_date,
                   l.name AS server_role
            FROM sys.server_principals sp
            LEFT JOIN sys.server_role_members rm ON rm.member_principal_id = sp.principal_id
            LEFT JOIN sys.server_principals l ON l.principal_id = rm.role_principal_id
            WHERE sp.type IN ('S','U','G') AND sp.name NOT LIKE '##%'
            ORDER BY sp.create_date DESC";
        solo_lectura($sql1);
        foreach ($pdo->query($sql1) as $row) {
            echo json_encode($row, JSON_UNESCAPED_UNICODE) . "\n";
        }
    } catch (\Throwable $e) {
        echo "(sin permisos para sys.server_principals: " . $e->getMessage() . ")\n";
    }

    echo "\n=== 2) xp_cmdshell (ejecución de comandos del SO desde SQL) — debe estar en 0 ===\n";
    try {
        $sql2 = "SELECT name, CAST(value AS INT) AS value_configurado, CAST(value_in_use AS INT) AS value_en_uso
                 FROM sys.configurations WHERE name = 'xp_cmdshell'";
        solo_lectura($sql2);
        foreach ($pdo->query($sql2) as $row) {
            echo json_encode($row, JSON_UNESCAPED_UNICODE) . "\n";
        }
    } catch (\Throwable $e) {
        echo "(no se pudo leer sys.configurations: " . $e->getMessage() . ")\n";
    }

    echo "\n=== 3) Servidores enlazados (linked servers) — posible ruta de exfiltración/pivote ===\n";
    try {
        $sql3 = "SELECT name, product, provider, data_source, is_linked
                 FROM sys.servers WHERE is_linked = 1";
        solo_lectura($sql3);
        $hubo = false;
        foreach ($pdo->query($sql3) as $row) {
            $hubo = true;
            echo json_encode($row, JSON_UNESCAPED_UNICODE) . "\n";
        }
        if (!$hubo) echo "(ninguno)\n";
    } catch (\Throwable $e) {
        echo "(no se pudo leer sys.servers: " . $e->getMessage() . ")\n";
    }

    echo "\n=== 4) SQL Agent Jobs (tareas programadas — posible persistencia) ===\n";
    try {
        $sql4 = "SELECT j.name, j.enabled, j.date_created, j.date_modified,
                        SUSER_SNAME(j.owner_sid) AS owner
                 FROM msdb.dbo.sysjobs j
                 ORDER BY j.date_modified DESC";
        solo_lectura($sql4);
        $hubo = false;
        foreach ($pdo->query($sql4) as $row) {
            $hubo = true;
            echo json_encode($row, JSON_UNESCAPED_UNICODE) . "\n";
        }
        if (!$hubo) echo "(ningún job configurado)\n";
    } catch (\Throwable $e) {
        echo "(no se pudo leer msdb.dbo.sysjobs: " . $e->getMessage() . ")\n";
    }

    echo "\n=== 5) Error log reciente: intentos de login FALLIDOS (quién golpeó la puerta y no entró) ===\n";
    try {
        $sql5 = "EXEC sp_readerrorlog 0, 1, 'Login failed'";
        solo_lectura($sql5);
        $hubo = false;
        foreach ($pdo->query($sql5) as $row) {
            $hubo = true;
            echo json_encode($row, JSON_UNESCAPED_UNICODE) . "\n";
        }
        if (!$hubo) echo "(sin intentos fallidos en el log actual)\n";
    } catch (\Throwable $e) {
        echo "(no se pudo leer el error log: " . $e->getMessage() . ")\n";
    }

    echo "\n=== 6) Triggers a nivel de servidor (posible mecanismo oculto de persistencia) ===\n";
    try {
        $sql6 = "SELECT name, is_disabled, create_date, modify_date FROM sys.server_triggers";
        solo_lectura($sql6);
        $hubo = false;
        foreach ($pdo->query($sql6) as $row) {
            $hubo = true;
            echo json_encode($row, JSON_UNESCAPED_UNICODE) . "\n";
        }
        if (!$hubo) echo "(ninguno)\n";
    } catch (\Throwable $e) {
        echo "(no se pudo leer sys.server_triggers: " . $e->getMessage() . ")\n";
    }
} catch (\Throwable $e) {
    echo "ERROR: " . $e->getMessage() . "\n";
} finally {
    $pdo = null;
    gc_collect_cycles();
}
