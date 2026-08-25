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
 * Arnés de verificación de la SUITE DE SKILLS DE GESTIÓN DE NOTAS EN TIEMPO
 * REAL (directiva v3.32): los 6 Tools de Almacén.
 *
 * Cobertura:
 *  1. Auto-descubrimiento: ToolRegistry::loadFromDirectory() registra los 6
 *     Tools sin registro manual (criterio de aceptación de la directiva).
 *  2. Contrato AgentToolInterface: getName/getDescription/getParameters/
 *     getDefinition válidos en los 6.
 *  3. Lógica pura (sin BD): detección de errores de renglón, ranking de
 *     preparadores, carga activa, recomendación inteligente, ubicación
 *     física derivada y validación de renglones.
 *  4. Diagnóstico real SOLO LECTURA contra PRUEB25 (ODBC SQL Server) y el
 *     MySQL legacy (si el driver existe en la instalación). Cero escritura.
 *
 * Salida: JSON {"suite":..., "summary":{total,passed,failed,total_time_ms},
 * "results":[{id,nombre,status,detalle,time_ms}]}. Exit 0 si todo PASS.
 *
 * Pre-Flight: extensiones críticas (pdo_odbc, mbstring); fallo → JSON
 * estructurado y exit 500 (patrón del proyecto).
 */

use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\ToolRegistry;
use App\Services\NvidiaBrain\Tools\Almacen\DetectorErroresNotaTool;
use App\Services\NvidiaBrain\Tools\Almacen\FlujoNotasTiempoRealTool;
use App\Services\NvidiaBrain\Tools\Almacen\GestionSuperEsteroideSearchTool;
use App\Services\NvidiaBrain\Tools\Almacen\MonitorModificacionesEliminacionesTool;
use App\Services\NvidiaBrain\Tools\Almacen\RendimientoPreparadoresSmartAssignTool;
use App\Services\NvidiaBrain\Tools\Almacen\TrazabilidadVidaUtilNotaTool;

foreach (['pdo_odbc', 'mbstring'] as $ext) {
    if (!extension_loaded($ext)) {
        $payload = [
            'status'  => 'ERROR_DRIVER_MISSING',
            'message' => 'Extensión PHP requerida no instalada: ' . $ext,
            'code'    => 500,
        ];
        echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . PHP_EOL;
        exit(500);
    }
}

$toolsDir = __DIR__ . '/../app/Services/NvidiaBrain/Tools';
require __DIR__ . '/../app/Services/NvidiaBrain/NvidiaBrainException.php';
require __DIR__ . '/../app/Services/NvidiaBrain/NvidiaBrainClient.php';
require __DIR__ . '/../app/Services/ConnectionWrapper.php';
require __DIR__ . '/../app/Services/NvidiaBrain/Contracts/AgentToolInterface.php';
require __DIR__ . '/../app/Services/NvidiaBrain/ToolRegistry.php';
require $toolsDir . '/Almacen/AlmacenDbTrait.php';
require $toolsDir . '/Almacen/TrazabilidadVidaUtilNotaTool.php';
require $toolsDir . '/Almacen/FlujoNotasTiempoRealTool.php';
require $toolsDir . '/Almacen/DetectorErroresNotaTool.php';
require $toolsDir . '/Almacen/MonitorModificacionesEliminacionesTool.php';
require $toolsDir . '/Almacen/RendimientoPreparadoresSmartAssignTool.php';
require $toolsDir . '/Almacen/GestionSuperEsteroideSearchTool.php';

const NOMBRES_TOOLS = [
    'trazabilidad_vida_util_nota',
    'flujo_notas_tiempo_real',
    'detector_errores_nota',
    'monitor_modificaciones_eliminaciones',
    'rendimiento_preparadores_smart_assign',
    'gestion_super_esteroide_search',
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
        'id'      => 'notas-' . $_ORDEN,
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

// ── 1) Auto-descubrimiento por ToolRegistry ────────────────────────────────
caso('ToolRegistry registra los 6 Tools via loadFromDirectory()', function () use ($toolsDir): array {
    $registry = new ToolRegistry();
    $registry->loadFromDirectory($toolsDir);
    $faltantes = array_diff(NOMBRES_TOOLS, $registry->names());
    if ($faltantes !== []) {
        return [false, 'Tools no registrados: ' . implode(', ', $faltantes) . ' | registrados: ' . implode(', ', $registry->names())];
    }
    return [true, '6/6 registrados: ' . implode(', ', NOMBRES_TOOLS)];
});

// ── 2) Contrato AgentToolInterface en los 6 ────────────────────────────────
caso('Contrato de los 6 Tools (getDefinition válido)', function () use ($toolsDir): array {
    $registry = new ToolRegistry();
    $registry->loadFromDirectory($toolsDir);
    $defs = $registry->getDefinitions();
    $porNombre = [];
    foreach ($defs as $d) {
        $porNombre[(string) $d['function']['name']] = $d['function'];
    }
    $problemas = [];
    foreach (NOMBRES_TOOLS as $nombre) {
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
    return $problemas === []
        ? [true, count($defs) . ' definiciones OpenAI v1 válidas']
        : [false, implode(' | ', $problemas)];
});

// ── 3) Detector de errores (lógica pura) ───────────────────────────────────
caso('detectarErroresRenglones: duplicado, cero, negativo, anulado, catálogo', function (): array {
    $renglones = [
        ['num' => 'A1', 'reng' => 1, 'art' => 'SKU_X', 'sol' => 5, 'anul' => '0'],
        ['num' => 'A1', 'reng' => 2, 'art' => 'SKU_X', 'sol' => 3, 'anul' => '0'],
        ['num' => 'A1', 'reng' => 3, 'art' => 'SKU_Y', 'sol' => 0, 'anul' => '0'],
        ['num' => 'A1', 'reng' => 4, 'art' => 'SKU_Z', 'sol' => -2, 'anul' => '0'],
        ['num' => 'A1', 'reng' => 5, 'art' => 'SKU_W', 'sol' => 1, 'anul' => '1'],
        ['num' => 'B2', 'reng' => 1, 'art' => 'DESCAT', 'sol' => 4, 'anul' => '0'],
    ];
    // Catálogo con todos excepto DESCAT (el renglón anulado nunca se chequea).
    $catalogo = ['SKU_X' => true, 'SKU_Y' => true, 'SKU_Z' => true, 'SKU_W' => true];
    $errores = DetectorErroresNotaTool::detectarErroresRenglones($renglones, $catalogo);
    $claves = [];
    foreach ($errores as $e) {
        $claves[] = $e['codigo_error'] . ':' . $e['co_art'];
    }
    $esperados = ['RENGLON_DUPLICADO:SKU_X', 'CANTIDAD_CERO:SKU_Y', 'CANTIDAD_NEGATIVA:SKU_Z', 'CODIGO_INCOMPATIBLE:DESCAT'];
    sort($claves);
    sort($esperados);
    $anuladoGenera = false;
    foreach ($errores as $e) {
        if ($e['co_art'] === 'SKU_W') {
            $anuladoGenera = true;
        }
    }
    return [(count($claves) === 4 && $claves === $esperados && !$anuladoGenera), json_encode($claves)];
});

caso('detectarErroresRenglones: catálogo null omite chequeo 3', function (): array {
    $renglones = [
        ['num' => 'C3', 'reng' => 1, 'art' => 'SIN_CATALOGO', 'sol' => 4, 'anul' => '0'],
    ];
    $errores = DetectorErroresNotaTool::detectarErroresRenglones($renglones, null);
    return [$errores === [], '0 errores (chequeo de descatalogados omitido)'];
});

// ── 4) Ranking y recomendación (lógica pura) ───────────────────────────────
caso('calcularRanking: rpm global + marcas por volumen', function (): array {
    $registros = [
        // Ana: 10 ítems en 10 min (1 rpm) + 3 ítems en 1 min (3 rpm).
        ['usuario' => 'ANA', 'tamano' => 10, 'minutos' => 10, 'completado' => true],
        ['usuario' => 'ANA', 'tamano' => 3,  'minutos' => 1,  'completado' => true],
        // Bob: 8 ítems en 2 min (4 rpm).
        ['usuario' => 'BOB', 'tamano' => 8,  'minutos' => 2,  'completado' => true],
        // Ana: nota en curso (no cuenta en rpm).
        ['usuario' => 'ANA', 'tamano' => 30, 'minutos' => 0,  'completado' => false],
    ];
    $ranking = RendimientoPreparadoresSmartAssignTool::calcularRanking($registros);
    $ana = null;
    $bob = null;
    foreach ($ranking as $p) {
        if ($p['usuario'] === 'ANA') {
            $ana = $p;
        }
        if ($p['usuario'] === 'BOB') {
            $bob = $p;
        }
    }
    if ($ana === null || $bob === null) {
        return [false, 'Faltan operarios en el ranking'];
    }
    $ok = $ana['renglones'] === 13 && abs($ana['rpm'] - (13 / 11)) < 0.01
        && abs($bob['rpm'] - 4.0) < 0.01
        && $ana['marcas_por_volumen']['1-5']['rpm'] === 3.0
        && $ana['marcas_por_volumen']['6-20']['rpm'] === 1.0
        && $ana['notas_completadas'] === 2;
    return [$ok, json_encode([
        'ANA' => ['rpm' => $ana['rpm'], 'renglones' => $ana['renglones'], 'marcas' => $ana['marcas_por_volumen']],
        'BOB' => ['rpm' => $bob['rpm']],
    ])];
});

caso('cargaActiva + recomendarPreparador (mejor rpm del rango)', function (): array {
    $registros = [
        // Ana: carga activa 30 (rpm global 1.0, sin marca 1-5).
        ['usuario' => 'ANA', 'tamano' => 10, 'minutos' => 10, 'completado' => true],
        ['usuario' => 'ANA', 'tamano' => 30, 'minutos' => 0,  'completado' => false],
        // Bob: marca 1-5 = 3.0 (3 ítems en 1 min) + 6-20 = 4.0.
        ['usuario' => 'BOB', 'tamano' => 8,  'minutos' => 2,  'completado' => true],
        ['usuario' => 'BOB', 'tamano' => 3,  'minutos' => 1,  'completado' => true],
        // Carlos: rpm global 5.0 pero sin marcas en 1-5.
        ['usuario' => 'CAR', 'tamano' => 25, 'minutos' => 5,  'completado' => true],
    ];
    $ranking = RendimientoPreparadoresSmartAssignTool::calcularRanking($registros);
    $carga = RendimientoPreparadoresSmartAssignTool::cargaActiva($registros);
    foreach ($ranking as &$p) {
        $p['carga_activa_renglones'] = $carga[$p['usuario']] ?? 0;
        $p['disponible'] = ($carga[$p['usuario']] ?? 0) < $p['rpm'] * 15;
    }
    unset($p);
    // Nota de 2 ítems → rango 1-5: Bob (marca 3.0 en 1-5, sin carga activa).
    $rec = RendimientoPreparadoresSmartAssignTool::recomendarPreparador($ranking, 2, 'N-2');
    $okCarga = ($carga['ANA'] ?? 0) === 30 && !isset($carga['BOB']);
    if ($rec === null) {
        return [false, 'Sin recomendación para nota de 2 ítems'];
    }
    $ok = $okCarga && $rec['preparador'] === 'BOB' && $rec['rango_volumen'] === '1-5'
        && (float) $rec['rpm_rango'] === 3.0;
    return [$ok, json_encode(['carga' => $carga, 'recomendacion' => $rec['preparador'], 'rpm_rango' => $rec['rpm_rango']])];
});

caso('recomendarPreparador: rango sin marcas cae a rpm global', function (): array {
    $ranking = [
        ['usuario' => 'ZZZ', 'rpm' => 0.1, 'carga_activa_renglones' => 0, 'disponible' => true,
         'marcas_por_volumen' => ['1-5' => ['rpm' => 0.0], '6-20' => ['rpm' => 0.0], '21+' => ['rpm' => 0.0]]],
        ['usuario' => 'TOP', 'rpm' => 9.9, 'carga_activa_renglones' => 0, 'disponible' => true,
         'marcas_por_volumen' => ['1-5' => ['rpm' => 0.0], '6-20' => ['rpm' => 0.0], '21+' => ['rpm' => 5.0]]],
    ];
    // Nota de 40 ítems (rango 21+): TOP tiene marca 5.0 → gana.
    $rec1 = RendimientoPreparadoresSmartAssignTool::recomendarPreparador($ranking, 40, 'N-40');
    // Nota de 15 ítems (rango 6-20 sin marcas): desempata por rpm global → TOP.
    $rec2 = RendimientoPreparadoresSmartAssignTool::recomendarPreparador($ranking, 15, 'N-15');
    return [
        ($rec1['preparador'] ?? '') === 'TOP' && ($rec2['preparador'] ?? '') === 'TOP',
        json_encode(['N-40' => $rec1['preparador'] ?? null, 'N-15' => $rec2['preparador'] ?? null]),
    ];
});

// ── 5) Ubicación física y validación (lógica pura) ─────────────────────────
caso('derivarUbicacion: estados físicos completos', function (): array {
    $hito = static fn (string $fase, int $ts): array => ['fase' => $fase, 'timestamp' => $ts, 'usuario' => ''];
    $base = [$hito('CREACION', 1000)];
    $casos = [
        [GestionSuperEsteroideSearchTool::derivarUbicacion($base, 'PENDIENTE', false), 'SIN_ASIGNACION'],
        [GestionSuperEsteroideSearchTool::derivarUbicacion([...$base, $hito('ASIGNACION_MESA', 2000)], 'PENDIENTE', false), 'MESA_DE_PICKING'],
        [GestionSuperEsteroideSearchTool::derivarUbicacion([...$base, $hito('ASIGNACION_MESA', 2000), $hito('PREPARACION', 3000)], 'EN_PREPARACION', false), 'MODULO_DE_CHEQUEO'],
        [GestionSuperEsteroideSearchTool::derivarUbicacion([...$base, $hito('ASIGNACION_MESA', 2000), $hito('PREPARACION', 3000), $hito('CHEQUEO', 4000)], 'EN_CHEQUEO', false), 'ZONA_DE_EMBALAJE'],
        [GestionSuperEsteroideSearchTool::derivarUbicacion([...$base, $hito('ASIGNACION_MESA', 2000), $hito('PREPARACION', 3000), $hito('CHEQUEO', 4000), $hito('EMBALAJE', 5000)], 'EN_EMBALAJE', false), 'ZONA_DE_BULTO_CERRADO'],
        [GestionSuperEsteroideSearchTool::derivarUbicacion([...$base, $hito('ASIGNACION_MESA', 2000), $hito('PREPARACION', 3000), $hito('CHEQUEO', 4000), $hito('EMBALAJE', 5000)], 'PROCESADA', false), 'CAMION_DE_RUTA'],
        [GestionSuperEsteroideSearchTool::derivarUbicacion([...$base, $hito('ENTREGA_DEVOLUCION', 6000)], 'PROCESADA', true), 'DEVUELTA'],
    ];
    $fallas = [];
    foreach ($casos as [$resultado, $esperado]) {
        if (!str_contains($resultado, $esperado)) {
            $fallas[] = $esperado . ' → ' . $resultado;
        }
    }
    return [$fallas === [], $fallas === [] ? '7/7 ubicaciones correctas' : implode(' | ', $fallas)];
});

caso('derivarValidacion: 100%, parcial, sin validar y bypass', function (): array {
    $v100 = GestionSuperEsteroideSearchTool::derivarValidacion(
        [['sol' => 10, 'desp' => 10], ['sol' => 5, 'desp' => 5]], 'EN_CHEQUEO'
    );
    $vParcial = GestionSuperEsteroideSearchTool::derivarValidacion(
        [['sol' => 10, 'desp' => 4]], 'EN_CHEQUEO'
    );
    $vNada = GestionSuperEsteroideSearchTool::derivarValidacion(
        [['sol' => 10, 'desp' => 0]], 'PENDIENTE'
    );
    $vBypass = GestionSuperEsteroideSearchTool::derivarValidacion(
        [['sol' => 10, 'desp' => 4]], 'PROCESADA'
    );
    $ok = $v100['nivel'] === 'VALIDADA_100' && $v100['validado'] === true
        && $vParcial['nivel'] === 'PARCIAL_EN_REVISION' && $vParcial['bypass_revision'] === false
        && $vNada['nivel'] === 'SIN_VALIDAR'
        && $vBypass['nivel'] === 'PARCIAL_EN_REVISION' && $vBypass['bypass_revision'] === true;
    return [$ok, json_encode([
        '100' => $v100['nivel'] . '/' . $v100['validado'],
        'parcial' => $vParcial['nivel'],
        'sin_validar' => $vNada['nivel'],
        'bypass' => $vBypass['nivel'] . '/bypass=' . var_export($vBypass['bypass_revision'], true),
    ])];
});

// ── 6) Diagnóstico real SOLO LECTURA (PRUEB25 + MySQL legacy) ─────────────
// Nota real confirmada en PRUEB25 (reng_nde.num_doc, 20 renglones) — se usa
// SOLO lectura; cero escritura en BD.
$notaReal = '72000051';

caso('DIAGNÓSTICO REAL: trazabilidad_vida_util_nota(' . $notaReal . ')', function () use ($notaReal): array {
    $tool = new TrazabilidadVidaUtilNotaTool();
    $r = $tool->execute(['num_nota' => $notaReal], []);
    if (($r['success'] ?? false) !== true) {
        return [false, 'success=false: ' . ($r['error'] ?? '?')];
    }
    $items = (array) ($r['data']['items'] ?? []);
    $advertencias = (array) ($r['data']['advertencias'] ?? []);
    $resumen = [
        'estado'  => $r['data']['estado_actual'] ?? '?',
        'final'   => $r['data']['estado_final'] ?? '?',
        'hitos'   => count((array) ($r['data']['timeline'] ?? [])),
        'deltas'  => count((array) ($r['data']['deltas'] ?? [])),
        'items'   => count($items),
        'metric_us' => $r['data']['metric_us'] ?? 0,
    ];
    return [
        $items !== [],  // reng_nde real de PRUEB25 debe traer ítems
        json_encode($resumen) . ' | avisos: ' . count($advertencias),
    ];
});

caso('DIAGNÓSTICO REAL: detector_errores_nota(' . $notaReal . ')', function () use ($notaReal): array {
    $tool = new DetectorErroresNotaTool();
    $r = $tool->execute(['num_nota' => $notaReal], []);
    if (($r['success'] ?? false) !== true) {
        return [false, 'success=false: ' . ($r['error'] ?? '?')];
    }
    $d = $r['data'];
    return [
        ($d['total_analizadas'] ?? 0) === 1,
        json_encode([
            'analizadas' => $d['total_analizadas'] ?? 0,
            'en_conflicto' => count((array) ($d['en_conflicto'] ?? [])),
            'alertas'      => count((array) ($d['mensajes_alerta'] ?? [])),
            'metric_us'    => $d['metric_us'] ?? 0,
        ]),
    ];
});

caso('DIAGNÓSTICO REAL: gestion_super_esteroide_search(' . $notaReal . ')', function () use ($notaReal): array {
    $tool = new GestionSuperEsteroideSearchTool();
    $r = $tool->execute(['num_nota' => $notaReal], []);
    if (($r['success'] ?? false) !== true) {
        return [false, 'success=false: ' . ($r['error'] ?? '?')];
    }
    $d = $r['data'];
    $val = (array) ($d['validacion'] ?? []);
    return [
        (($d['encontrada'] ?? false) === true) && isset($val['nivel']) && isset($d['donde_esta']),
        json_encode([
            'donde'     => $d['donde_esta'] ?? '?',
            'validacion'=> $val['nivel'] ?? '?',
            'movs'      => count((array) ($d['ultimos_movimientos'] ?? [])),
            'metric_us' => $d['metric_us'] ?? 0,
        ]),
    ];
});

caso('DIAGNÓSTICO REAL: flujo_notas_tiempo_real (cola activa)', function (): array {
    $tool = new FlujoNotasTiempoRealTool();
    $r = $tool->execute(['limite' => 50], []);
    // Acepta: éxito con datos O error estructurado honesto (driver MySQL
    // ausente en instalaciones sin pdo_mysql/ODBC MySQL).
    if (($r['success'] ?? false) === true) {
        $d = $r['data'];
        return [
            isset($d['buckets'], $d['totales']),
            json_encode([
                'notas_activas' => $d['notas_activas'] ?? 0,
                'buckets'       => count((array) ($d['buckets'] ?? [])),
                'totales'       => $d['totales'] ?? [],
                'metric_us'     => $d['metric_us'] ?? 0,
            ]),
        ];
    }
    $err = (string) ($r['error'] ?? '');
    $honesto = str_contains($err, 'MySQL') || str_contains($err, 'driver') || str_contains($err, 'no disponible');
    return [$honesto, 'diagnóstico honesto: ' . $err];
});

caso('DIAGNÓSTICO REAL: monitor_modificaciones_eliminaciones', function (): array {
    $tool = new MonitorModificacionesEliminacionesTool();
    $r = $tool->execute(['limite' => 20], []);
    if (($r['success'] ?? false) !== true) {
        $err = (string) ($r['error'] ?? '');
        $honesto = str_contains($err, 'MySQL') || str_contains($err, 'driver') || str_contains($err, 'no disponible');
        return [$honesto, 'diagnóstico honesto: ' . $err];
    }
    $d = $r['data'];
    return [
        isset($d['fuente'], $d['eventos']),
        json_encode(['fuente' => $d['fuente'] ?? '?', 'eventos' => count((array) ($d['eventos'] ?? [])), 'avisos' => count((array) ($d['advertencias'] ?? []))]),
    ];
});

caso('DIAGNÓSTICO REAL: rendimiento_preparadores_smart_assign', function (): array {
    $tool = new RendimientoPreparadoresSmartAssignTool();
    $r = $tool->execute(['fecha' => date('Y-m-d')], []);
    if (($r['success'] ?? false) !== true) {
        $err = (string) ($r['error'] ?? '');
        $honesto = str_contains($err, 'MySQL') || str_contains($err, 'driver') || str_contains($err, 'no disponible');
        return [$honesto, 'diagnóstico honesto: ' . $err];
    }
    $d = $r['data'];
    return [
        isset($d['ranking']),
        json_encode(['ranking' => count((array) ($d['ranking'] ?? [])), 'sugerencia' => $d['sugerencia'] ?? null, 'avisos' => count((array) ($d['advertencias'] ?? []))]),
    ];
});

// ── Salida JSON estandarizada ──────────────────────────────────────────────
$salida = [
    'suite'   => 'notas-tiempo-real-tools',
    'summary' => resumen($_RESULTADOS),
    'results' => $_RESULTADOS,
];
echo json_encode($salida, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . PHP_EOL;
exit($salida['summary']['failed'] > 0 ? 1 : 0);
