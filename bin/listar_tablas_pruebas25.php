<?php
declare(strict_types=1);

/**
 * Lista TODAS las tablas reales de PRUEB25 (SQL Server) — solo lectura,
 * INFORMATION_SCHEMA.TABLES. Uso: php bin/listar_tablas_pruebas25.php
 */
require_once __DIR__ . '/../app/Services/ConnectionWrapper.php';
require_once __DIR__ . '/../app/Services/ConnectionWrapperException.php';
require_once __DIR__ . '/../app/Services/EnvironmentException.php';

use App\Services\ConnectionWrapper;

$wrapper = new ConnectionWrapper();
$filas = $wrapper->querySafe(
    "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME",
    [],
    0
);

foreach ($filas as $fila) {
    echo $fila['TABLE_NAME'] . "\n";
}
fwrite(STDERR, "Total: " . count($filas) . " tablas\n");
