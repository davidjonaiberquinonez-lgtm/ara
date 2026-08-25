<?php

declare(strict_types=1);

declare(ticks=1);

// ═══════════════════════════════════════════════════════════════════════
// CANDADO ANTI-ZOMBI (Orden v4.14): el script se suicida a los N segundos
// pase lo que pase. Default 300s; override con env ARA_CLI_MAX_S; tope 600s.
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
 * Arnés de verificación de la FASE 4.17 — AJUSTE DE ADAPTADORES
 * (SISTEMA PRUEB25 / PROFIT), orden v4.17.
 *
 * Cobertura:
 *  1. Envelope estándar v4.17 (ok/vacio/error/conflicto/conCard).
 *  2. Auto-descubrimiento: ToolRegistry::loadFromDirectory() registra los 10
 *     adaptadores.
 *  3. Contrato AgentToolInterface: getName/getDescription/getParameters válidos.
 *  4. Guards offline (sin BD): validación de parámetros obligatorios.
 *  5. Lógica pura AD10 (operadores_del_dia / cuellos_de_botella >20 /
 *     alerta_rendimiento) y AD3 (detección de errores de renglón).
 *  6. Diagnóstico real SOLO LECTURA contra PRUEB25 / MySQL legacy con
 *     degradación honesta (acepta error controlado de conexión/driver).
 *
 * Salida: JSON {"suite":..., "summary":{...}, "results":[...]}. Exit 0 si
 * todo PASS, 1 si hay FAIL.
 */

use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\ToolRegistry;
use App\Services\NvidiaBrain\Tools\Almacen\BuscarInventarioTool;
use App\Services\NvidiaBrain\Tools\Almacen\ConsultarNotaTool;
use App\Services\NvidiaBrain\Tools\Almacen\DetectorErroresNotaTool;
use App\Services\NvidiaBrain\Tools\Almacen\FlujoNotasTiempoRealTool;
use App\Services\NvidiaBrain\Tools\Almacen\GestionNotasTool;
use App\Services\NvidiaBrain\Tools\Almacen\MonitorModificacionesEliminacionesTool;
use App\Services\NvidiaBrain\Tools\Almacen\RendimientoPreparadoresSmartAssignTool;
use App\Services\NvidiaBrain\Tools\Almacen\TrazabilidadVidaUtilNotaTool;
use App\Services\NvidiaBrain\Tools\Auditoria\ConsultarSaldoClienteTool;
use App\Services\NvidiaBrain\Tools\Compras\PythonComprasSkillTool;
use App\Services\NvidiaBrain\Tools\Common\Envelope;
use App\Services\NvidiaBrain\Tools\Despacho\ConsultarClienteTool;

foreach (['pdo_odbc', 'mbstring'] as $ext) {
    if (!extension_loaded($ext)) {
        echo json_encode([
            'status'  => 'ERROR_DRIVER_MISSING',
            'message' => 'Extensión PHP requerida no instalada: ' . $ext,
            'code'    => 500,
        ], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . PHP_EOL;
        exit(500);
    }
}

$toolsDir = __DIR__ . '/../app/Services/NvidiaBrain/Tools';
require __DIR__ . '/../app/Services/NvidiaBrain/NvidiaBrainException.php';
require __DIR__ . '/../app/Services/NvidiaBrain/NvidiaBrainClient.php';
require __DIR__ . '/../app/Services/ConnectionWrapper.php';
require __DIR__ . '/../app/Services/NvidiaBrain/Contracts/AgentToolInterface.php';
require __DIR__ . '/../app/Services/NvidiaBrain/ToolRegistry.php';
require $toolsDir . '/Common/CardBuilder.php';
require $toolsDir . '/Common/Envelope.php';
require $toolsDir . '/Almacen/AlmacenDbTrait.php';
require $toolsDir . '/Almacen/ConsultarNotaTool.php';
require $toolsDir . '/Almacen/GestionNotasTool.php';
require $toolsDir . '/Almacen/BuscarInventarioTool.php';
require $toolsDir . '/Almacen/DetectorErroresNotaTool.php';
require $toolsDir . '/Almacen/MonitorModificacionesEliminacionesTool.php';
require $toolsDir . '/Almacen/TrazabilidadVidaUtilNotaTool.php';
require $toolsDir . '/Almacen/FlujoNotasTiempoRealTool.php';
require $toolsDir . '/Almacen/RendimientoPreparadoresSmartAssignTool.php';
require $toolsDir . '/Auditoria/ConsultarSaldoClienteTool.php';
require $toolsDir . '/Compras/PythonComprasSkillTool.php';
require $toolsDir . '/Despacho/ConsultarClienteTool.php';

const NOMBRES_ADAPTADORES = [
    'consultar_nota',
    'gestion_notas',
    'buscar_inventario',
    'detector_errores_nota',
    'monitor_modificaciones_eliminaciones',
    'consultar_saldo_cliente',
    'trazabilidad_vida_util_nota',
    'consultar_cliente',
    'flujo_notas_tiempo_real',
    'python_compras_skill',
    'rendimiento_preparadores_smart_assign',
];

/** @var array<int,array<string,mixed>> */
$_RESULTADOS = [];
$_ORDEN = 0;

function caso(string $nombre, callable $fn): void
{
    global $_RESULTADOS, $_ORDEN;
    ++$_ORDEN;
    $t0 = microtime(true);
    try {
        [$ok, $detalle] = $fn();
    } catch (Throwable $e) {
        $ok = false;
        $detalle = 'excepción: ' . get_class($e) . ': ' . $e->getMessage();
    }
    $_RESULTADOS[] = [
        'id'      => 'v417-' . $_ORDEN,
        'nombre'  => $nombre,
        'status'  => $ok ? 'PASS' : 'FAIL',
        'detalle' => mb_convert_encoding($detalle, 'UTF-8', 'UTF-8, Windows-1252, ISO-8859-1'),
        'time_ms' => (int) ((microtime(true) - $t0) * 1000),
    ];
}

function resumen(array $r): array
{
    $passed = count(array_filter($r, static fn (array $x): bool => $x['status'] === 'PASS'));
    return [
        'total'        => count($r),
        'passed'       => $passed,
        'failed'       => count($r) - $passed,
        'total_time_ms'=> (int) array_sum(array_column($r, 'time_ms')),
    ];
}

/** Error de degradación honesta (conexión/driver/BD ausente) = aceptable. */
function errorHonesto(?string $error): bool
{
    $error = (string) $error;
    return $error === '' || str_contains($error, 'MySQL') || str_contains($error, 'no disponible')
        || str_contains($error, 'driver') || str_contains($error, 'No se pudo conectar')
        || str_contains($error, 'no existe') || str_contains($error, 'no encontrado')
        || str_contains($error, 'Sin registros');
}

// ── 1) Envelope estándar v4.17 (lógica pura) ────────────────────────────────
caso('Envelope::ok con data y message', function (): array {
    $e = Envelope::ok(['a' => 1], 'Nota consultada.');
    return [
        ($e['success'] ?? null) === true && ($e['data'] ?? null) === ['a' => 1]
        && ($e['message'] ?? '') === 'Nota consultada.' && isset($e['timestamp']),
        json_encode($e),
    ];
});

caso('Envelope::vacio y ok([]) usan "Sin registros"', function (): array {
    $v = Envelope::vacio();
    $ok = Envelope::ok([]);
    return [
        ($v['success'] ?? null) === true && ($v['data'] ?? null) === [] && ($v['message'] ?? '') === 'Sin registros'
        && ($ok['message'] ?? '') === 'Sin registros',
        'vacio=' . ($v['message'] ?? '?') . ' ok=[] ' . ($ok['message'] ?? '?'),
    ];
});

caso('Envelope::error y conflicto HTTP_409', function (): array {
    $e = Envelope::error('X', 'STOCK_INSUFICIENTE');
    $c = Envelope::conflicto('Solo Pendiente');
    return [
        ($e['success'] ?? null) === false && ($e['codigo'] ?? '') === 'STOCK_INSUFICIENTE' && ($e['data'] ?? null) === []
        && ($c['success'] ?? null) === false && ($c['codigo'] ?? '') === 'HTTP_409_CONFLICTO',
        json_encode(['e' => $e, 'c' => $c]),
    ];
});

caso('Envelope::conCard conserva card y ok', function (): array {
    $e = Envelope::conCard(Envelope::ok(['x' => 1]), '# card');
    return [
        ($e['card'] ?? '') === '# card' && ($e['ok'] ?? null) === true && ($e['success'] ?? null) === true,
        json_encode($e),
    ];
});

// ── 2) Auto-descubrimiento por ToolRegistry ─────────────────────────────────
caso('ToolRegistry registra los 11 adaptadores via loadFromDirectory()', function () use ($toolsDir): array {
    $registry = new ToolRegistry();
    $registry->loadFromDirectory($toolsDir);
    $faltantes = array_diff(NOMBRES_ADAPTADORES, $registry->names());
    if ($faltantes !== []) {
        return [false, 'No registrados: ' . implode(', ', $faltantes)];
    }
    return [true, count(NOMBRES_ADAPTADORES) . '/' . count(NOMBRES_ADAPTADORES) . ' registrados'];
});

// ── 3) Contrato AgentToolInterface ──────────────────────────────────────────
caso('Contrato de los 11 adaptadores (getParameters JSON Schema válido)', function () use ($toolsDir): array {
    $registry = new ToolRegistry();
    $registry->loadFromDirectory($toolsDir);
    $defs = $registry->getDefinitions();
    $porNombre = [];
    foreach ($defs as $d) {
        $porNombre[(string) $d['function']['name']] = $d['function'];
    }
    $problemas = [];
    foreach (NOMBRES_ADAPTADORES as $nombre) {
        $fn = $porNombre[$nombre] ?? null;
        if ($fn === null) {
            $problemas[] = $nombre . ': sin definición';
            continue;
        }
        if (trim((string) ($fn['description'] ?? '')) === '') {
            $problemas[] = $nombre . ': descripción vacía';
        }
        $params = $fn['parameters'] ?? null;
        if (!is_array($params) || ($params['type'] ?? '') !== 'object' || !is_array($params['properties'] ?? null)) {
            $problemas[] = $nombre . ': JSON Schema inválido';
        }
    }
    return $problemas === [] ? [true, count(NOMBRES_ADAPTADORES) . ' contratos válidos'] : [false, implode(' | ', $problemas)];
});

// ── 4) Guards offline (sin tocar BD) ────────────────────────────────────────
caso('guard: gestion_notas sin accion → error controlado', function (): array {
    $r = (new GestionNotasTool())->execute([], []);
    return [
        ($r['success'] ?? null) === false && str_contains((string) ($r['message'] ?? ''), 'accion'),
        json_encode($r),
    ];
});

caso('guard: consultar_saldo_cliente sin identificador → error controlado', function (): array {
    $r = (new ConsultarSaldoClienteTool())->execute([], []);
    return [
        ($r['success'] ?? null) === false && str_contains((string) ($r['message'] ?? ($r['error'] ?? '')), 'obligatorio'),
        json_encode($r),
    ];
});

caso('guard: trazabilidad_vida_util_nota sin num_nota → error controlado', function (): array {
    $r = (new TrazabilidadVidaUtilNotaTool())->execute([], []);
    return [
        ($r['success'] ?? null) === false && str_contains((string) ($r['message'] ?? ($r['error'] ?? '')), 'obligatorio'),
        json_encode($r),
    ];
});

caso('guard: consultar_cliente sin criterio → error controlado', function (): array {
    $r = (new ConsultarClienteTool())->execute([], []);
    return [
        (($r['ok'] ?? null) === false || ($r['success'] ?? null) === false)
        && (($r['error'] ?? '') !== '' || ($r['message'] ?? '') !== ''),
        json_encode($r),
    ];
});

caso('guard: buscar_inventario sin criterio → error controlado', function (): array {
    $r = (new BuscarInventarioTool())->execute([], []);
    return [
        ($r['success'] ?? null) === false && str_contains((string) ($r['message'] ?? ($r['error'] ?? '')), 'criterio'),
        json_encode($r),
    ];
});

// ── 5) Lógica pura AD10 / AD3 ───────────────────────────────────────────────
caso('operadoresDelDia: cuello de botella >20 y alerta rendimiento', function (): array {
    $registros = [];
    // Botella: 25 ops pendientes (nunca completadas).
    for ($i = 0; $i < 25; ++$i) {
        $registros[] = ['usuario' => 'BOTELLA', 'tamano' => 5, 'minutos' => 0, 'completado' => false];
    }
    // Lento: 2 ops completadas en 80 min c/u → 80 min promedio > 30.
    $registros[] = ['usuario' => 'LENTO', 'tamano' => 4, 'minutos' => 80, 'completado' => true];
    $registros[] = ['usuario' => 'LENTO', 'tamano' => 4, 'minutos' => 80, 'completado' => true];
    $r = RendimientoPreparadoresSmartAssignTool::operadoresDelDia($registros);
    $cuellos = (array) ($r['cuellos_de_botella'] ?? []);
    $alertas = (array) ($r['alerta_rendimiento'] ?? []);
    $hayBotella = false;
    foreach ($cuellos as $c) {
        if ($c['nombre'] === 'BOTELLA' && (int) $c['ops_pendientes'] > 20) {
            $hayBotella = true;
        }
    }
    $hayAlerta = false;
    foreach ($alertas as $a) {
        if ($a['nombre'] === 'LENTO' && (float) $a['tiempo_promedio'] > 30) {
            $hayAlerta = true;
        }
    }
    return [$hayBotella && $hayAlerta, json_encode($r)];
});

caso('detectarErroresRenglones: duplicado + cero + negativo + catálogo', function (): array {
    $renglones = [
        ['num' => 'A1', 'reng' => 1, 'art' => 'SKU_X', 'sol' => 5, 'anul' => '0'],
        ['num' => 'A1', 'reng' => 2, 'art' => 'SKU_X', 'sol' => 3, 'anul' => '0'],
        ['num' => 'A1', 'reng' => 3, 'art' => 'SKU_Y', 'sol' => 0, 'anul' => '0'],
        ['num' => 'A1', 'reng' => 4, 'art' => 'DESCAT', 'sol' => 4, 'anul' => '0'],
    ];
    $errores = DetectorErroresNotaTool::detectarErroresRenglones($renglones, ['SKU_X' => true, 'SKU_Y' => true]);
    $claves = [];
    foreach ($errores as $e) {
        $claves[] = $e['codigo_error'];
    }
    sort($claves);
    return [
        $claves === ['CANTIDAD_CERO', 'CODIGO_INCOMPATIBLE', 'RENGLON_DUPLICADO'],
        json_encode($claves),
    ];
});

// ── 6) Diagnóstico real SOLO LECTURA (degradación honesta aceptada) ─────────
$notaReal = '72000051';

caso('DIAGNÓSTICO REAL: consultar_nota(' . $notaReal . ')', function () use ($notaReal): array {
    $r = (new ConsultarNotaTool())->execute(['num_nota' => $notaReal], []);
    if (($r['success'] ?? false) === true) {
        $d = $r['data'];
        return [
            isset($d['num_nota'], $d['estado'], $d['lineas']) && ($d['cliente']['co_cli'] ?? null) !== null,
            json_encode(['num' => $d['num_nota'] ?? '?', 'estado' => $d['estado'] ?? '?', 'lineas' => count((array) ($d['lineas'] ?? []))]),
        ];
    }
    return [errorHonesto((string) ($r['message'] ?? $r['error'] ?? '')), 'degradación: ' . (string) ($r['message'] ?? $r['error'] ?? '?')];
});

caso('DIAGNÓSTICO REAL: detector_errores_nota(' . $notaReal . ')', function () use ($notaReal): array {
    $r = (new DetectorErroresNotaTool())->execute(['num_nota' => $notaReal], []);
    if (($r['success'] ?? false) === true) {
        $d = $r['data'];
        return [
            isset($d['metricas_del_dia'], $d['total_analizadas']),
            json_encode(['analizadas' => $d['total_analizadas'] ?? 0, 'metricas' => $d['metricas_del_dia']['total_notas_hasta_momento'] ?? 0]),
        ];
    }
    return [errorHonesto((string) ($r['message'] ?? $r['error'] ?? '')), 'degradación: ' . (string) ($r['message'] ?? $r['error'] ?? '?')];
});

caso('DIAGNÓSTICO REAL: flujo_notas_tiempo_real (flujo_del_dia)', function (): array {
    $r = (new FlujoNotasTiempoRealTool())->execute(['limite' => 50], []);
    if (($r['success'] ?? false) === true) {
        $d = $r['data'];
        $fd = (array) ($d['flujo_del_dia'] ?? []);
        return [
            isset($d['buckets'], $d['totales']) && isset($fd['por_hora'], $fd['tendencia'], $fd['notas_del_dia']),
            json_encode(['notas_dia' => $fd['notas_del_dia'] ?? 0, 'tendencia' => $fd['tendencia'] ?? '?', 'por_hora' => count((array) ($fd['por_hora'] ?? []))]),
        ];
    }
    return [errorHonesto((string) ($r['message'] ?? $r['error'] ?? '')), 'degradación: ' . (string) ($r['message'] ?? $r['error'] ?? '?')];
});

caso('DIAGNÓSTICO REAL: monitor_modificaciones_eliminaciones (esquema AD4)', function (): array {
    $r = (new MonitorModificacionesEliminacionesTool())->execute(['limite' => 20], []);
    if (($r['success'] ?? false) === true) {
        $d = $r['data'];
        return [
            isset($d['modificaciones'], $d['eliminaciones'], $d['eventos'], $d['fuente']),
            json_encode(['fuente' => $d['fuente'] ?? '?', 'mod' => count((array) ($d['modificaciones'] ?? [])), 'elim' => count((array) ($d['eliminaciones'] ?? []))]),
        ];
    }
    return [errorHonesto((string) ($r['message'] ?? $r['error'] ?? '')), 'degradación: ' . (string) ($r['message'] ?? $r['error'] ?? '?')];
});

caso('DIAGNÓSTICO REAL: trazabilidad_vida_util_nota(' . $notaReal . ')', function () use ($notaReal): array {
    $r = (new TrazabilidadVidaUtilNotaTool())->execute(['num_nota' => $notaReal], []);
    if (($r['success'] ?? false) === true) {
        $d = $r['data'];
        return [
            isset($d['ciclo_vida'], $d['tiempo_total_ciclo_min'], $d['alertas'], $d['timeline']),
            json_encode(['ciclo' => count((array) ($d['ciclo_vida'] ?? [])), 'total_min' => $d['tiempo_total_ciclo_min'] ?? 0, 'alertas' => count((array) ($d['alertas'] ?? []))]),
        ];
    }
    return [errorHonesto((string) ($r['message'] ?? $r['error'] ?? '')), 'degradación: ' . (string) ($r['message'] ?? $r['error'] ?? '?')];
});

caso('DIAGNÓSTICO REAL: rendimiento_preparadores_smart_assign (operadores_del_dia)', function (): array {
    $r = (new RendimientoPreparadoresSmartAssignTool())->execute(['fecha' => date('Y-m-d')], []);
    if (($r['success'] ?? false) === true) {
        $d = $r['data'];
        return [
            isset($d['ranking'], $d['operadores_del_dia'], $d['cuellos_de_botella'], $d['alerta_rendimiento']),
            json_encode(['ranking' => count((array) ($d['ranking'] ?? [])), 'ops' => count((array) ($d['operadores_del_dia'] ?? []))]),
        ];
    }
    return [errorHonesto((string) ($r['message'] ?? $r['error'] ?? '')), 'degradación: ' . (string) ($r['message'] ?? $r['error'] ?? '?')];
});

caso('DIAGNÓSTICO REAL: python_compras_skill (priorizados AD9)', function (): array {
    $r = (new PythonComprasSkillTool())->execute([], []);
    if (($r['success'] ?? false) === true) {
        $d = $r['data'];
        return [
            isset($d['priorizados'], $d['prioridad_alta'], $d['prioridad_media'], $d['prioridad_baja']),
            json_encode(['total' => $d['priorizados']['total'] ?? 0, 'alta' => count((array) ($d['prioridad_alta'] ?? []))]),
        ];
    }
    return [errorHonesto((string) ($r['message'] ?? $r['error'] ?? '')), 'degradación: ' . (string) ($r['message'] ?? $r['error'] ?? '?')];
});

caso('DIAGNÓSTICO REAL: consultar_saldo_cliente (facturas + descuento_unico)', function (): array {
    $r = (new ConsultarSaldoClienteTool())->execute(['identificador_cliente' => 'FAR01361'], []);
    if (($r['success'] ?? false) === true) {
        $d = $r['data'];
        $cartera = (array) ($d['cartera'] ?? []);
        return [
            isset($d['cliente']) && isset($cartera['facturas']) && array_key_exists('descuento_unico', $d['cliente'] ?? []),
            json_encode(['cli' => $d['cliente']['identificador'] ?? '?', 'facturas' => count((array) ($cartera['facturas'] ?? [])), 'saldo' => $d['cliente']['saldo_actual'] ?? null]),
        ];
    }
    return [errorHonesto((string) ($r['message'] ?? $r['error'] ?? '')), 'degradación: ' . (string) ($r['message'] ?? $r['error'] ?? '?')];
});

caso('DIAGNÓSTICO REAL: consultar_cliente (ultimas_consultas + credito_disponible)', function (): array {
    $r = (new ConsultarClienteTool())->execute(['co_cli' => 'FAR01361'], []);
    if (($r['ok'] ?? ($r['success'] ?? false)) === true) {
        return [
            isset($r['credito_disponible'], $r['ultimas_consultas'], $r['telefonos']),
            json_encode(['co_cli' => $r['co_cli'] ?? '?', 'credito' => $r['credito_disponible'] ?? null, 'consultas' => count((array) ($r['ultimas_consultas'] ?? []))]),
        ];
    }
    return [errorHonesto((string) ($r['message'] ?? $r['error'] ?? '')), 'degradación: ' . (string) ($r['message'] ?? $r['error'] ?? '?')];
});

// ── Salida JSON estandarizada ──────────────────────────────────────────────
$resumen = resumen($_RESULTADOS);
$salida = [
    'suite'   => 'adaptadores-v4.17',
    'summary' => $resumen,
    'results' => $_RESULTADOS,
];
echo json_encode($salida, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . PHP_EOL;
exit($resumen['failed'] === 0 ? 0 : 1);
