<?php

declare(strict_types=1);

/**
 * ARNÉS DE PRUEBA Y AUDITORÍA EN CONSOLA — 5 SKILLS PYTHON vía Adaptadores PHP
 * (directiva v4.2). Invoca secuencialmente los 5 superconectores de
 * departamento registrados en el ToolRegistry y audita el ciclo de vida
 * completo de cada uno en STDOUT con banners estructurados.
 *
 *   [START]    Departamento / skill invocada
 *   [INPUT]    Argumentos + contexto enviados al adaptador PHP
 *   [LATENCY]  Tiempo de ejecución en milisegundos
 *   [RESPONSE] JSON formateado (JSON_PRETTY_PRINT) del resultado
 *   [STATUS]   PASS (success===true) | DEGRADED (fallo controlado) |
 *              FAIL (excepción, aislada por try/catch)
 *
 * Skills cubiertas (contrato AgentToolInterface, ejecución por
 * PythonSkillExecutor → `py -3.14` + python_skill_bridge.py):
 *   1. python_stock_bulto_cerrado_skill   (Almacén, cola de surtido real)
 *   2. python_auditoria_skill             (Auditoría, análisis LOG_REPORTE)
 *   3. python_compras_skill               (Compras, quiebres reales)
 *   4. python_despacho_skill              (Despacho, gate 1:1 bultos, puro)
 *   5. python_recepcion_skill             (Recepción, OCR/visión de factura)
 *
 * Aislamiento: cada skill se ejecuta en su propio bloque try/catch; ningún
 * fallo interrumpe la secuencia global (5/5 completados siempre).
 *
 * Uso:
 *   php bin/test_skills_console_runner.php
 *
 * Códigos de salida:
 *   0   5/5 skills completaron su ciclo (PASS o DEGRADED honesto)
 *   1   al menos una skill lanzó excepción (FAIL) o el registry no la registró
 *   500 extensiones/dependencias críticas faltantes
 */

use App\Services\NvidiaBrain\ToolRegistry;

const HERRAMIENTAS_DIR = __DIR__ . '/../app/Services/NvidiaBrain/Tools';

// ── Pre-Flight: extensiones críticas ───────────────────────────────────────
$faltantes = [];
foreach (['pdo_odbc', 'mbstring'] as $ext) {
    if (!extension_loaded($ext)) {
        $faltantes[] = $ext;
    }
}
if ($faltantes !== []) {
    fwrite(STDERR, '{"status":"ERROR_DRIVER_MISSING","extensiones":'
        . json_encode($faltantes) . '}' . PHP_EOL);
    exit(500);
}

require __DIR__ . '/../app/Services/NvidiaBrain/NvidiaBrainException.php';
require __DIR__ . '/../app/Services/NvidiaBrain/NvidiaBrainClient.php';
require __DIR__ . '/../app/Services/NvidiaBrain/Contracts/AgentToolInterface.php';
require __DIR__ . '/../app/Services/NvidiaBrain/ToolRegistry.php';
require HERRAMIENTAS_DIR . '/Almacen/AlmacenDbTrait.php';

/** Normaliza cualquier string/array a UTF-8 válido para STDOUT. */
function normalizar_utf8(mixed $v): mixed
{
    if (is_string($v)) {
        return mb_check_encoding($v, 'UTF-8')
            ? $v
            : mb_convert_encoding($v, 'UTF-8', 'Windows-1252');
    }
    if (is_array($v)) {
        foreach ($v as $k => $item) {
            $v[$k] = normalizar_utf8($item);
        }
    }
    return $v;
}

/** Imprime JSON formateado seguro (nunca lanza por caracteres raros). */
function imprimir_json(mixed $valor): void
{
    $json = json_encode(
        normalizar_utf8($valor),
        JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES
    );
    echo $json === false ? '(no serializable)' : $json;
    echo PHP_EOL;
}

/**
 * Desnormaliza la respuesta de executeTool(): el ToolRegistry envuelve la
 * salida de la tool en el formato de tool_call del motor IA
 * {role: 'tool', tool_call_id, content: '<JSON string>'}. Devuelve
 * [estado, detalle, respuestaNormalizada]:
 *   - estado: 'PASS' (success true sin status error) | 'DEGRADED'
 *     (fallo controlado de la skill con diagnóstico) | 'FORMATO_INVALIDO'
 *   - detalle: texto diagnóstico legible para [STATUS]
 */
function desnormalizar_resultado(mixed $resultado): array
{
    if (is_array($resultado) && ($resultado['role'] ?? null) === 'tool') {
        $content = is_string($resultado['content'] ?? null) ? $resultado['content'] : '';
        $interno = json_decode($content, true);
        if (is_array($interno)) {
            $resultado = $interno;
        }
    }
    if (!is_array($resultado)) {
        return ['FORMATO_INVALIDO', 'Respuesta no serializable: ' . gettype($resultado), $resultado];
    }

    $success = ($resultado['success'] ?? false) === true;
    if (!$success) {
        $error = (string) ($resultado['error'] ?? 'fallo controlado sin detalle');
        if (isset($resultado['tipo'])) {
            $error .= ' [tipo: ' . $resultado['tipo'] . ']';
        }
        return ['DEGRADED', $error, $resultado];
    }

    $data = $resultado['data'] ?? null;
    if (is_array($data) && isset($data['status']) && $data['status'] === 'error') {
        $detalle = (string) ($data['aviso'] ?? $data['requisitos'] ?? 'skill con diagnóstico de esquema');
        if (!empty($data['aviso']) && !empty($data['requisitos'])) {
            $detalle .= ' | ' . $data['requisitos'];
        }
        return ['DEGRADED', $detalle, $resultado];
    }

    return ['PASS', '', $resultado];
}

/** Banner horizontal del arnés. */
function linea(string $caracter = '='): void
{
    echo str_repeat($caracter, 78) . PHP_EOL;
}

// ── Registro de herramientas (patrón de producción del agente) ─────────────
linea();
echo 'ARNES SKILLS PYTHON v4.2 — Adaptadores PHP por departamento' . PHP_EOL;
echo 'Fecha: ' . date('Y-m-d H:i:s') . PHP_EOL;
$registry = new ToolRegistry();
$registry->loadFromDirectory(HERRAMIENTAS_DIR);
$nombres = $registry->names();
echo 'ToolRegistry: ' . count($nombres) . ' herramienta(s) registrada(s).' . PHP_EOL;
linea();

// ── Suite secuencial (aislamiento por skill) ───────────────────────────────
$SUITE = [
    [
        'departamento' => 'ALMACEN',
        'nombre'       => 'python_stock_bulto_cerrado_skill',
        'arguments'    => [
            'script'         => 'surtido_prioritario',
            'accion'         => 'obtener_cola_surtido',
            'sede'           => 'BQTO',
            'dias_rotacion'  => 30,
        ],
    ],
    [
        'departamento' => 'AUDITORIA',
        'nombre'       => 'python_auditoria_skill',
        'arguments'    => [
            'script'   => 'detector_malsurtido',
            'accion'   => 'analizar_log_surtido',
            'ruta_log' => dirname(__DIR__) . '/ara/ARA_Brain/data/log_reporte_arnes_v42.log',
        ],
    ],
    [
        'departamento' => 'COMPRAS',
        'nombre'       => 'python_compras_skill',
        'arguments'    => [
            'dias_rotacion' => 30,
        ],
    ],
    [
        'departamento' => 'DESPACHO',
        'nombre'       => 'python_despacho_skill',
        'arguments'    => [
            'accion' => 'validar_estructura_bultos',
            'bultos' => [
                ['codigo' => 'BC-BQTO-001', 'cod_art' => 'MD01006'],
                ['codigo' => 'BC-BQTO-002', 'cod_art' => 'MD01007'],
                ['codigo' => 'BC-BQTO-003', 'cod_art' => 'MD01008'],
            ],
            'items'  => ['MD01006', 'MD01007', 'MD01008'],
        ],
    ],
    [
        'departamento' => 'RECEPCION',
        'nombre'       => 'python_recepcion_skill',
        'arguments'    => [
            'ruta_imagen' => 'C:/ARA_PROYECT/ara/ARA_Brain/data/WhatsApp Image 2026-06-14 at 10.56.29 PM.jpeg',
        ],
    ],
];

// Contexto de sesión simulado del agente (Ollama/NIM al pedir tool calls):
// el mismo objeto $contexto que inyecta el motor en producción.
$contexto = [
    'usuario'  => 'ARNES_V42',
    'rol'      => 'sistema',
    'modulo'   => 'prueba_consola',
    'sesion'   => 'suite-skills-v4.2',
];

$estadisticas = ['PASS' => 0, 'DEGRADED' => 0, 'FAIL' => 0, 'NO_REGISTRADA' => 0];
$fallos_fatales = 0;

foreach ($SUITE as $item) {
    $departamento = $item['departamento'];
    $skill        = $item['nombre'];
    $arguments    = $item['arguments'];

    linea('-');
    echo "[START] {$departamento} | Skill: {$skill}" . PHP_EOL;
    linea('-');

    if (!in_array($skill, $nombres, true)) {
        $estadisticas['NO_REGISTRADA']++;
        $fallos_fatales++;
        echo "[STATUS] FAIL — la skill '{$skill}' NO está registrada en el ToolRegistry." . PHP_EOL;
        echo '         Registradas: ' . implode(', ', $nombres) . PHP_EOL;
        continue;
    }

    echo '[INPUT] arguments:' . PHP_EOL;
    imprimir_json($arguments);
    echo '[INPUT] contexto:' . PHP_EOL;
    imprimir_json($contexto);

    $t0 = microtime(true);
    try {
        $resultado = $registry->executeTool($skill, $arguments, $contexto);
        $latencia_ms = (int) ((microtime(true) - $t0) * 1000);

        [$estado, $detalle, $respuesta] = desnormalizar_resultado($resultado);
        echo "[LATENCY] {$latencia_ms} ms" . PHP_EOL;
        echo '[RESPONSE]' . PHP_EOL;
        imprimir_json($respuesta);

        if ($estado === 'PASS') {
            $estadisticas['PASS']++;
            echo '[STATUS] PASS' . PHP_EOL;
        } elseif ($estado === 'DEGRADED') {
            $estadisticas['DEGRADED']++;
            echo '[STATUS] DEGRADED — fallo controlado de la skill con diagnóstico:' . PHP_EOL;
            echo '         ' . str_replace(["\r", "\n"], ' ', (string) normalizar_utf8($detalle)) . PHP_EOL;
            if (is_array($respuesta) && !empty($respuesta['stdout']) && is_string($respuesta['stdout'])) {
                echo '         stdout crudo: ' . str_replace(["\r", "\n"], ' ', $respuesta['stdout']) . PHP_EOL;
            }
            if (is_array($respuesta) && !empty($respuesta['stderr']) && is_string($respuesta['stderr'])) {
                echo '         stderr crudo: ' . str_replace(["\r", "\n"], ' ', $respuesta['stderr']) . PHP_EOL;
            }
        } else {
            $estadisticas['DEGRADED']++;
            echo '[STATUS] DEGRADED — respuesta inesperada: ' . $detalle . PHP_EOL;
        }
    } catch (Throwable $e) {
        // Excepción NO controlada: se aísla y la secuencia continúa.
        $latencia_ms = (int) ((microtime(true) - $t0) * 1000);
        $estadisticas['FAIL']++;
        $fallos_fatales++;
        echo "[LATENCY] {$latencia_ms} ms" . PHP_EOL;
        echo '[STATUS] FAIL — excepción aislada: ' . get_class($e) . ': ' . $e->getMessage() . PHP_EOL;
    }
    echo PHP_EOL;
}

// ── Resumen final ───────────────────────────────────────────────────────────
linea();
echo 'RESUMEN SUITE SKILLS PYTHON v4.2' . PHP_EOL;
foreach ($estadisticas as $k => $v) {
    echo sprintf("  %-13s %d", $k, $v) . PHP_EOL;
}
$completadas = count($SUITE);
echo sprintf('  Completadas   %d/%d (PASS + DEGRADED = ciclo completo)', $completadas - $estadisticas['FAIL'] - $estadisticas['NO_REGISTRADA'], $completadas) . PHP_EOL;
linea();

exit($fallos_fatales > 0 ? 1 : 0);
