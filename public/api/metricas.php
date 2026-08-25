<?php
/**
 * Endpoint de lectura pura para Dashboard Ejecutivo ARA
 */

declare(strict_types=1);

declare(ticks=1);

// ═══════════════════════════════════════════════════════════════════════
// CANDADO ANTI-ZOMBI (Fase 2.5): el script se suicida a los N segundos
// pase lo que pase. Default 300s; override con env ARA_CLI_MAX_S; tope 600s.
// Watchdog por tick: si excede 240s, fuerza desconexión y aborta (FASE25).
// ═══════════════════════════════════════════════════════════════════════
$t_maxS = (int) (getenv('ARA_CLI_MAX_S') ?: '300');
$t_maxS = max(5, min(600, $t_maxS));
set_time_limit($t_maxS);
ini_set('max_execution_time', (string) $t_maxS);
ini_set('memory_limit', '128M');

$t_inicio = microtime(true);
register_shutdown_function(static function (): void {
    // HOTFIX v4.17.1: cerrar conexiones SQL Server abiertas (sin pool dormido).
    if (isset($GLOBALS['conn']) && is_resource($GLOBALS['conn']) && function_exists('sqlsrv_close')) {
        @sqlsrv_close($GLOBALS['conn']);
    }
    if (isset($GLOBALS['db_wrapper']) && is_object($GLOBALS['db_wrapper']) && method_exists($GLOBALS['db_wrapper'], 'forceDisconnect')) {
        $GLOBALS['db_wrapper']->forceDisconnect();
    }
    gc_collect_cycles();
});
register_tick_function(static function () use ($t_inicio, $t_maxS): void {
    if ((microtime(true) - $t_inicio) > 240) {
        if (defined('STDERR')) {
            fwrite(STDERR, '{"success":false,"error":"FASE25_KILL: public/api/metricas.php excedió tiempo (' . $t_maxS . 's). Proceso abortado."}');
        }
        error_log('FASE25_KILL: script public/api/metricas.php excedió tiempo (' . $t_maxS . 's)');
        exit(1);
    }
});

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');

use App\Services\NvidiaBrain\Adapters\TecnologiaAdapter;

require_once __DIR__ . '/../../app/Core/conectar_profit_read.php';
require_once __DIR__ . '/../../app/Services/NvidiaBrain/Adapters/BaseAdapter.php';
require_once __DIR__ . '/../../app/Services/NvidiaBrain/Adapters/TecnologiaAdapter.php';
require_once __DIR__ . '/../../app/Services/ConnectionWrapper.php';
require_once __DIR__ . '/../../app/Services/ConnectionWrapperException.php';
require_once __DIR__ . '/../../app/Services/NvidiaBrain/NvidiaBrainClient.php';

try {
    $pdo = conectar_profit_read();
    $adapter = new TecnologiaAdapter(pdo: $pdo);

    $data = $adapter->obtenerMetricasDirectas();

    echo json_encode([
        'status' => 'success',
        'timestamp' => date('c'),
        'data' => $data
    ], JSON_UNESCAPED_UNICODE);

} catch (\Throwable $e) {
    http_response_code(500);
    echo json_encode([
        'status' => 'error',
        'message' => 'Error de lectura de métricas',
        'details' => $e->getMessage()
    ], JSON_UNESCAPED_UNICODE);
} finally {
    // Candado anti-zombi: la conexión de conectar_profit_read() no es
    // persistente, pero se cierra explícito para seguir el mismo patrón
    // que el resto del repo.
    $pdo = null;
    gc_collect_cycles();
}
