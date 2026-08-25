<?php

declare(strict_types=1);

declare(ticks=1);

// ═══════════════════════════════════════════════════════════════════════
// CANDADO ANTI-ZOMBI (Orden v4.14): el script se suicida a los N segundos
// pase lo que pase. Default 300s; override con env ARA_CLI_MAX_S; tope 600s.
// Evita procesos muertos en background.
// ═══════════════════════════════════════════════════════════════════════
$t_maxS = (int) (getenv('ARA_CLI_MAX_S') ?: '300');
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
            fwrite(STDERR, '{"success":false,"error":"test: timeout preventivo (' . $t_maxS . 's). Proceso abortado."}');
        }
        exit(1);
    }
});

/**
 * Prueba CLI del adaptador de Tecnología (NVIDIA BRAIN) contra SQL Server
 * real PRUEB25 (192.168.4.20:1433).
 *
 * Verifica el criterio de aceptación del procedimiento sp_IA_Obtener_Metricas:
 *   - procesar() retorna success=true y data.metricas como array de conjuntos
 *     de resultados (sin excepciones ODBC).
 *   - El primer conjunto contiene estatus_servicio = 'OK'.
 *   - El segundo conjunto contiene la métrica notas_del_dia.
 *
 * Configuración (variables de entorno, con defaults):
 *   PROFIT_SQL_HOST / PROFIT_SQL_NAME / PROFIT_SQL_USER / PROFIT_SQL_PASS
 *   (defaults: 192.168.4.20 / PRUEB25 / profit / profit)
 *
 * Uso:
 *   php bin/test_tecnologia_adapter.php
 *
 * Códigos de salida:
 *   0   éxito (estatus_servicio = OK + métrica de notas del día)
 *   1   el SP no cumple el criterio de aceptación (asserción fallida)
 *   2   error inesperado (Throwable no controlado)
 *   500 driver/extensiones requeridas faltantes
 */

use App\Services\NvidiaBrain\Adapters\TecnologiaAdapter;

require_once __DIR__ . '/../app/Services/NvidiaBrain/Adapters/BaseAdapter.php';
require_once __DIR__ . '/../app/Services/NvidiaBrain/Adapters/TecnologiaAdapter.php';
require_once __DIR__ . '/../app/Services/NvidiaBrain/NvidiaBrainClient.php';
require_once __DIR__ . '/../app/Services/ConnectionWrapper.php';
require_once __DIR__ . '/../app/Services/ConnectionWrapperException.php';

// ── Pre-Flight: extensiones críticas ───────────────────────────────────────
$faltantes = [];
foreach (['pdo_odbc', 'curl', 'mbstring'] as $ext) {
    if (!extension_loaded($ext)) {
        $faltantes[] = $ext;
    }
}
if ($faltantes !== []) {
    fwrite(STDERR, '{"status":"ERROR_DRIVER_MISSING","extensiones":'
        . json_encode($faltantes) . '}' . PHP_EOL);
    exit(500);
}

function sanitizar_utf8_recursivo(mixed $v): mixed
{
    if (is_string($v)) {
        return mb_convert_encoding($v, 'UTF-8', 'UTF-8') === $v
            ? $v
            : mb_convert_encoding($v, 'UTF-8', 'Windows-1252');
    }
    if (is_array($v)) {
        foreach ($v as $k => $item) {
            $v[$k] = sanitizar_utf8_recursivo($item);
        }
    }
    return $v;
}

try {
    // ── Configuración ──────────────────────────────────────────────────────
    $host = getenv('PROFIT_SQL_HOST') ?: '192.168.4.20';
    $db   = getenv('PROFIT_SQL_NAME') ?: 'PRUEB25';
    $user = getenv('PROFIT_SQL_USER') ?: 'profit';
    $pass = getenv('PROFIT_SQL_PASS') ?: 'profit';

    $drivers = PDO::getAvailableDrivers();
    $dsn = in_array('sqlsrv', $drivers, true)
        ? "sqlsrv:Server=$host,1433;Database=$db"
        : "odbc:Driver={SQL Server};Server=$host,1433;Database=$db";

    echo "== TECNOLOGIA ADAPTER - Prueba sp_IA_Obtener_Metricas ==" . PHP_EOL;
    echo "Servidor: $host | Base: $db | DSN: $dsn" . PHP_EOL;

    $pdo = new PDO($dsn, $user, $pass);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    echo "Conexion a PRUEB25: OK" . PHP_EOL . PHP_EOL;

    // ── Adaptador con PDO inyectado (no re-abre conexión) ──────────────────
    $adapter = new TecnologiaAdapter(pdo: $pdo);

    $resultado = $adapter->procesar('métricas del día');
    $resultado = sanitizar_utf8_recursivo($resultado);

    $success = (bool) ($resultado['success'] ?? false);
    $data = $resultado['data'] ?? [];
    $metricas = $data['metricas'] ?? [];
    $conjuntos = $data['conjuntos'] ?? 0;
    $totalFilas = $data['total_filas'] ?? 0;

    echo '== RESULTADO ==' . PHP_EOL;
    echo 'success:   ' . ($success ? 'true' : 'FALSE') . PHP_EOL;
    echo 'respuesta: ' . ($resultado['respuesta'] ?? '(sin respuesta)') . PHP_EOL;
    if (($resultado['error'] ?? '') !== '') {
        echo 'error:     ' . $resultado['error'] . PHP_EOL;
    }
    echo 'conjuntos: ' . $conjuntos . ' | filas: ' . $totalFilas . PHP_EOL;

    $estatus = $metricas[0][0]['estatus_servicio'] ?? null;
    $notasDia = $metricas[1][0]['notas_del_dia'] ?? null;
    echo 'estatus_servicio: ' . var_export($estatus, true) . PHP_EOL;
    echo 'notas_del_dia:    ' . var_export($notasDia, true) . PHP_EOL;

    // ── Criterio de aceptación ─────────────────────────────────────────────
    $fallos = [];
    if (!$success) {
        $fallos[] = 'success no es true';
    }
    if ($estatus !== 'OK') {
        $fallos[] = "estatus_servicio esperado 'OK', obtenido "
            . var_export($estatus, true);
    }
    if ($notasDia === null) {
        $fallos[] = 'métrica notas_del_dia ausente del 2º conjunto';
    }

    echo PHP_EOL . '== DETALLE DE METRICAS ==' . PHP_EOL;
    foreach ($metricas as $i => $conjunto) {
        echo '-- conjunto ' . ($i + 1) . ' (' . count($conjunto) . ' fila(s))' . PHP_EOL;
        foreach ($conjunto as $fila) {
            echo '   ' . json_encode($fila, JSON_UNESCAPED_UNICODE) . PHP_EOL;
        }
    }

    if ($fallos !== []) {
        echo PHP_EOL . '== FALLO DE ACEPTACION ==' . PHP_EOL;
        foreach ($fallos as $f) {
            echo '- ' . $f . PHP_EOL;
        }
        exit(1);
    }

    echo PHP_EOL . '== ACEPTADO: estatus_servicio=OK con métrica de notas del día (' . $notasDia . ') ==' . PHP_EOL;
    exit(0);
} catch (\Throwable $e) {
    fwrite(STDERR, PHP_EOL . '== ERROR INESPERADO ==' . PHP_EOL);
    fwrite(STDERR, get_class($e) . ': ' . $e->getMessage() . PHP_EOL);
    exit(2);
}
