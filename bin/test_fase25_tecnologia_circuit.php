<?php

declare(strict_types=1);

declare(ticks=1);

$t_maxS = (int) (getenv('ARA_CLI_MAX_S') ?: '120');
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
        fwrite(STDERR, '{"success":false,"error":"test: timeout preventivo (' . $t_maxS . 's)."}');
        exit(1);
    }
});

/**
 * Prueba Fase 2.5 T3: circuit breaker integrado en TecnologiaAdapter.
 *
 * El SP sp_IA_Obtener_Metricas NO existe en PRUEB25 (2812). Verifica:
 *   - 3 llamadas a procesar() registran el fallo 2812.
 *   - La 4ª llamada responde fail-fast (circuito abierto) con éxito=false
 *     SIN volver a golpear el servidor (respuesta instantánea).
 */

require_once __DIR__ . '/../app/Services/NvidiaBrain/Adapters/BaseAdapter.php';
require_once __DIR__ . '/../app/Services/NvidiaBrain/Adapters/TecnologiaAdapter.php';
require_once __DIR__ . '/../app/Services/NvidiaBrain/NvidiaBrainClient.php';
require_once __DIR__ . '/../app/Services/ConnectionWrapper.php';
require_once __DIR__ . '/../app/Services/ConnectionWrapperException.php';

use App\Services\NvidiaBrain\Adapters\TecnologiaAdapter;

$host = getenv('PROFIT_SQL_HOST') ?: '192.168.4.20';
$db   = getenv('PROFIT_SQL_NAME') ?: 'PRUEB25';
$user = getenv('PROFIT_SQL_USER') ?: 'profit';
$pass = getenv('PROFIT_SQL_PASS') ?: 'profit';

$drivers = PDO::getAvailableDrivers();
$dsn = in_array('sqlsrv', $drivers, true)
    ? "sqlsrv:Server=$host,1433;Database=$db"
    : "odbc:Driver={SQL Server};Server=$host,1433;Database=$db";

$pdo = new PDO($dsn, $user, $pass);
$pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);

$adapter = new TecnologiaAdapter(pdo: $pdo);

echo "== T3: Circuit breaker TecnologiaAdapter (SP 2812) ==" . PHP_EOL;

$fallos = [];
$duraciones = [];
for ($i = 1; $i <= 4; $i++) {
    $t0 = microtime(true);
    $res = $adapter->procesar('métricas');
    $dur = round((microtime(true) - $t0) * 1000, 1);
    $duraciones[] = $dur;
    $bloqueado = str_contains((string) ($res['error'] ?? ''), 'circuito abierto');
    echo "llamada $i: success=" . var_export($res['success'], true)
        . ' | dur=' . $dur . 'ms'
        . ' | circuito_abierto=' . var_export($bloqueado, true) . PHP_EOL;
    if ($i < 4 && $bloqueado) {
        $fallos[] = "llamada $i: el circuito se abrió antes de completar 3 fallos.";
    }
    if ($i === 4 && !$bloqueado) {
        $fallos[] = 'llamada 4: NO entró en fail-fast de circuito abierto.';
    }
}

echo PHP_EOL . 'duraciones: ' . implode(', ', $duraciones) . ' ms' . PHP_EOL;
if (($duraciones[3] ?? 999999) > 1000) {
    $fallos[] = 'llamada 4: el fail-fast tardó más de 1s (golpeó el servidor).';
}

echo PHP_EOL;
if ($fallos !== []) {
    echo '== FALLO ==' . PHP_EOL;
    foreach ($fallos as $f) {
        echo '- ' . $f . PHP_EOL;
    }
    exit(1);
}
echo '== OK: circuito abierto tras 3 fallos, 4ª llamada fail-fast ==' . PHP_EOL;
exit(0);
