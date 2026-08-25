<?php

declare(strict_types=1);

declare(ticks=1);

// ═══════════════════════════════════════════════════════════════════════
// CANDADO ANTI-ZOMBI (Fase 2.5): el script se suicida a los N segundos.
// Default 60s (no hace queries pesadas); override con env ARA_CLI_MAX_S.
// ═══════════════════════════════════════════════════════════════════════
$t_maxS = (int) (getenv('ARA_CLI_MAX_S') ?: '60');
$t_maxS = max(5, min(600, $t_maxS));
set_time_limit($t_maxS);
ini_set('max_execution_time', (string) $t_maxS);
ini_set('memory_limit', '128M');

$t_inicio = microtime(true);
register_shutdown_function(static function (): void {
    gc_collect_cycles();
});
register_tick_function(static function () use ($t_inicio, $t_maxS): void {
    if ((microtime(true) - $t_inicio) > $t_maxS) {
        if (defined('STDERR')) {
            fwrite(STDERR, '{"success":false,"error":"verify_env: timeout preventivo (' . $t_maxS . 's). Proceso abortado."}');
        }
        exit(1);
    }
});

/**
 * Verificador de entorno Fase 2.5 (T4).
 *
 * Valida que la configuración SQL del proyecto sea segura ANTES de cualquier
 * conexión:
 *   1. Extensiones PHP críticas (pdo_odbc/sqlsrv, curl, mbstring).
 *   2. La BD SQL configurada NO es CRISTM25 (prohibida desde Fase 2.4).
 *   3. Los valores de env PROFIT_SQL_x y PROFIT_DB_x (con defaults) se
 *      imprimen para auditoría visual.
 *
 * Uso:
 *   php bin/verify_env.php            (valida y reporta)
 *
 * Códigos de salida:
 *   0   entorno OK
 *   1   falló alguna validación (entorno NO seguro)
 *   2   error inesperado
 *   500 extensiones críticas faltantes
 */

use App\Services\ConnectionWrapper;

require_once __DIR__ . '/../app/Services/ConnectionWrapper.php';
require_once __DIR__ . '/../app/Services/ConnectionWrapperException.php';
require_once __DIR__ . '/../app/Services/EnvironmentException.php';

$fallos = [];

echo "== VERIFY ENV - Fase 2.5 (T4) ==" . PHP_EOL;

// ── 1) Extensiones críticas ────────────────────────────────────────────────
// pdo_odbc o pdo_sqlsrv (al menos uno); odbc solo obligatorio con pdo_odbc.
$tienePdo = extension_loaded('pdo_odbc') || extension_loaded('pdo_sqlsrv');
$faltantes = [];
if (!$tienePdo) {
    $faltantes[] = 'pdo_odbc|pdo_sqlsrv';
}
if (extension_loaded('pdo_odbc') && !extension_loaded('odbc')) {
    $faltantes[] = 'odbc';
}
foreach (['curl', 'mbstring', 'json'] as $ext) {
    if (!extension_loaded($ext)) {
        $faltantes[] = $ext;
    }
}
if ($faltantes !== []) {
    fwrite(STDERR, '{"status":"ERROR_DRIVER_MISSING","extensiones":'
        . json_encode($faltantes) . '}' . PHP_EOL);
    exit(500);
}
echo 'Driver SQL: ' . (extension_loaded('pdo_sqlsrv') ? 'sqlsrv' : 'pdo_odbc') . PHP_EOL;
echo "Extensiones críticas: OK" . PHP_EOL;

// ── 2) BD configurada NO CRISTM25 ──────────────────────────────────────────
$wrapper = new ConnectionWrapper();
try {
    $wrapper->validarEntorno();
    echo "Validación de BD (no CRISTM25): OK" . PHP_EOL;
} catch (\App\Services\EnvironmentException $e) {
    $fallos[] = $e->getMessage();
    echo 'Validación de BD: FALLO — ' . $e->getMessage() . PHP_EOL;
}

// ── 3) Auditoría visual de config ──────────────────────────────────────────
$sqlName = getenv('PROFIT_SQL_NAME') ?: getenv('PROFIT_DB_NAME') ?: 'PRUEB25';
$host = getenv('PROFIT_SQL_HOST') ?: getenv('PROFIT_DB_HOST') ?: '192.168.4.20';
echo PHP_EOL . "Config SQL resuelta:" . PHP_EOL;
echo '  host : ' . $host . PHP_EOL;
echo '  name : ' . $sqlName . PHP_EOL;
echo '  user : ' . (getenv('PROFIT_SQL_USER') ?: getenv('PROFIT_DB_USER') ?: 'profit') . PHP_EOL;
echo '  queryTimeout: ' . $wrapper->getQueryTimeout() . 's (PROFIT_QUERY_TIMEOUT)' . PHP_EOL;

echo PHP_EOL;
if ($fallos !== []) {
    echo '== FALLO: entorno NO seguro ==' . PHP_EOL;
    foreach ($fallos as $f) {
        echo '- ' . $f . PHP_EOL;
    }
    exit(1);
}
echo '== OK: entorno SQL seguro (Fase 2.5) ==' . PHP_EOL;
exit(0);
