<?php

declare(strict_types=1);

/**
 * Conexión PDO de SOLO LECTURA a Profit Plus (PRUEB25).
 *
 * Patrón del módulo: sqlsrv si está disponible, si no ODBC "SQL Server";
 * credenciales PROFIT_SQL_* → PROFIT_DB_* → defaults (profit/profit en
 * 192.168.4.20:1433, base PRUEB25). Timeout de consulta 8s.
 *
 * Usada por endpoints de lectura pura (ej. public/api/metricas.php).
 *
 * @throws PDOException Si la conexión falla.
 */
function conectar_profit_read(): PDO
{
    $host = getenv('PROFIT_SQL_HOST') ?: (getenv('PROFIT_DB_HOST') ?: '192.168.4.20');
    $name = getenv('PROFIT_SQL_NAME') ?: (getenv('PROFIT_DB_NAME') ?: 'PRUEB25');
    $user = getenv('PROFIT_SQL_USER') ?: (getenv('PROFIT_DB_USER') ?: 'profit');
    $pass = getenv('PROFIT_SQL_PASS') ?: (getenv('PROFIT_DB_PASS') ?: 'profit');

    // ConnectionPooling=0 + ATTR_PERSISTENT=false: mismo hotfix v4.17.1 que
    // ConnectionWrapper — sin esto el driver puede dejar un SPID dormido en
    // el servidor aunque PHP ya haya destruido el objeto PDO.
    $dsn = in_array('sqlsrv', PDO::getAvailableDrivers(), true)
        ? "sqlsrv:Server=$host,1433;Database=$name;ConnectionPooling=0"
        : "odbc:Driver={SQL Server};Server=$host,1433;Database=$name";

    return new PDO($dsn, $user, $pass, [
        PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_TIMEOUT            => 8,
        PDO::ATTR_PERSISTENT         => false,
    ]);
}
