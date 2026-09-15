<?php

declare(strict_types=1);

declare(ticks=1);

// ═══════════════════════════════════════════════════════════════════════
// CANDADO ANTI-ZOMBI (Orden de Emergencia v4.14): este script se suicida
// a los N segundos pase lo que pase. Si la skill cuelga consultando a
// Profit, el proceso muere solo. El invocador Python añade además un
// fusible (kill) de respaldo.
//   - default 30s para skills directas de datos (consultas Profit ≤5s).
//   - hereda ARA_CLI_MAX_S del invocador (35s fusible / 600s delegadas).
//   - tools delegadas (python_* skills, hermes_chat) corren en subproceso
//     propio con timeout interno; reciben margen mayor (hasta 1800s) para
//     no romper orquestación en tareas de análisis de proyecto completo.
// ═══════════════════════════════════════════════════════════════════════
$r_modo = $_SERVER['argv'][1] ?? '';
$r_delegadoLargo = preg_match('/^(?:python_|hermes_chat)/', $r_modo) === 1;
$r_maxS = (int) (getenv('ARA_CLI_MAX_S') ?: ($r_delegadoLargo ? '600' : '30'));
$r_maxS = max(5, min(1800, $r_maxS));
set_time_limit($r_maxS);
ini_set('max_execution_time', (string) $r_maxS);
ini_set('memory_limit', '128M');

$r_inicio = microtime(true);
register_shutdown_function(static function (): void {
    // Liberación final: cierra conexiones residuales y colecta ciclos.
    gc_collect_cycles();
});
// Vigilante por ticks: aborto graceful (JSON en stderr) ANTES del hard kill
// de max_execution_time, para no dejar salida a medias ni procesos vivos.
register_tick_function(static function () use ($r_inicio, $r_maxS): void {
    if ((microtime(true) - $r_inicio) > $r_maxS) {
        if (defined('STDERR')) {
            fwrite(STDERR, '{"success":false,"error":"ejecutar_tool_cli: timeout preventivo (' . $r_maxS . 's). Proceso abortado."}');
        }
        exit(1);
    }
});

/**
 * Runner CLI universal de Tools NVIDIA BRAIN (directiva v4.4).
 *
 * Puente único PHP → AgentToolInterface::executeTool() para el frontend web
 * (menú de comandos '/') y para cualquier cliente que necesite ejecutar las
 * 25 tools departamentales por nombre sin pasar por el motor LLM.
 *
 * Uso:
 *   php bin/ejecutar_tool_cli.php __catalogo__
 *       → {"success": true, "catalogo": [{departamento, nombre, descripcion,
 *          parametros, definicion}...]} agrupado por las 5 áreas.
 *
 *   php bin/ejecutar_tool_cli.php <tool> '<argumentsJSON>' ['<contextoJSON>']
 *       → {"success": true, "tool": "<tool>", "resultado": {respuesta de la
 *          tool}} | {"success": false, "error": "...", "tipo": "..."}
 *
 * Contrato: SIEMPRE una línea JSON en stdout (UTF-8), nunca excepciones hacia
 * el llamador; exit 0 en éxito, 1 en fallo controlado de invocación.
 */

use App\Services\NvidiaBrain\ToolRegistry;

const HERRAMIENTAS_DIR = __DIR__ . '/../app/Services/NvidiaBrain/Tools';

/** @var list<string> Extensiones críticas. */
$extFaltantes = [];
foreach (['mbstring'] as $ext) {
    if (!extension_loaded($ext)) {
        $extFaltantes[] = $ext;
    }
}
if ($extFaltantes !== []) {
    fwrite(STDERR, '{"success":false,"error":"Extensión PHP requerida no instalada: ' . implode(', ', $extFaltantes) . '"}');
    exit(1);
}

require __DIR__ . '/../app/Services/NvidiaBrain/NvidiaBrainException.php';
require __DIR__ . '/../app/Services/NvidiaBrain/NvidiaBrainClient.php';
require __DIR__ . '/../app/Services/NvidiaBrain/Contracts/AgentToolInterface.php';
require __DIR__ . '/../app/Services/NvidiaBrain/ToolRegistry.php';
require __DIR__ . '/../app/Services/ConnectionWrapper.php';
require HERRAMIENTAS_DIR . '/Almacen/AlmacenDbTrait.php';

/**
 * Normaliza cualquier string a UTF-8 válido (CP1252 de Profit no debe romper JSON).
 */
function r_normalizar_utf8(mixed $v): mixed
{
    if (is_string($v)) {
        return mb_check_encoding($v, 'UTF-8')
            ? $v
            : mb_convert_encoding($v, 'UTF-8', 'Windows-1252');
    }
    if (is_array($v)) {
        foreach ($v as $k => $item) {
            $v[$k] = r_normalizar_utf8($item);
        }
    }
    return $v;
}

/** Imprime el JSON de salida (una sola línea) y termina. */
function r_emitir(array $payload, int $exitCode = 0): never
{
    $json = json_encode(
        r_normalizar_utf8($payload),
        JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES
    );
    echo ($json === false ? '{"success":false,"error":"Salida no serializable"}' : $json) . PHP_EOL;
    exit($exitCode);
}

/** Lee argv seguro: valores vacíos → '' sin warnings. */
function r_argv(int $i): string
{
    $valor = $_SERVER['argv'][$i] ?? '';
    return is_string($valor) ? trim($valor) : '';
}

try {
    $registry = new ToolRegistry();
    $registry->loadFromDirectory(HERRAMIENTAS_DIR);

    $modo = r_argv(1);

    // ── Modo catálogo: definiciones agrupadas por departamento ─────────────
    if ($modo === '__catalogo__') {
        $catalogo = [];
        foreach ($registry->all() as $tool) {
            $nombre = $tool->getName();
            $clase = get_class($tool);
            $departamento = 'General';
            if (preg_match('/\\\\Tools\\\\([A-Za-z]+)\\\\/', $clase, $m)) {
                $departamento = $m[1];
            }
            // Construido SIEMPRE desde getParameters() (parte obligatoria de
            // AgentToolInterface) en vez de depender de getDefinition() —
            // BUG REAL detectado en vivo: getDefinition() NO es parte de la
            // interfaz oficial, varias tools (las 6 nuevas de supervisión/
            // quiebre/auditoría/ubicación/inactividad/ruta) nunca la
            // implementaron, así que la llamada tiraba "Call to undefined
            // method", el catch silencioso caía a 'parameters' => [], y el
            // frontend nunca sabía cuál era el parámetro requerido de esas
            // tools (rompía la captura automática de texto plano en /skill).
            try {
                $definicion = [
                    'name'        => $nombre,
                    'description' => $tool->getDescription(),
                    'parameters'  => $tool->getParameters(),
                ];
            } catch (Throwable $e) {
                $definicion = ['name' => $nombre, 'description' => $tool->getDescription(), 'parameters' => []];
            }
            $catalogo[] = [
                'departamento' => $departamento,
                'nombre'       => $nombre,
                'descripcion'  => $tool->getDescription(),
                'parametros'   => is_array($definicion['parameters'] ?? null)
                    ? ($definicion['parameters']['properties'] ?? [])
                    : [],
                'definicion'   => $definicion,
            ];
        }
        usort($catalogo, static fn (array $a, array $b): int => [$a['departamento'], $a['nombre']] <=> [$b['departamento'], $b['nombre']]);
        r_emitir(['success' => true, 'total' => count($catalogo), 'catalogo' => $catalogo]);
    }

    // ── Modo ejecución: <tool> <argumentsJSON> [contextoJSON] ───────────────
    if ($modo === '') {
        r_emitir(['success' => false, 'error' => 'Uso: __catalogo__ | <tool> <argumentsJSON> [contextoJSON]', 'tipo' => 'uso_invalido'], 1);
    }

    $toolNombre = $modo;
    if (!$registry->has($toolNombre)) {
        r_emitir([
            'success' => false,
            'error'   => 'Tool no registrada: "' . $toolNombre . '". Disponibles: ' . implode(', ', $registry->names()),
            'tipo'    => 'tool_inexistente',
        ], 1);
    }

    $argumentsRaw = r_argv(2);
    $arguments = $argumentsRaw !== '' ? json_decode($argumentsRaw, true) : [];
    if (!is_array($arguments)) {
        r_emitir(['success' => false, 'error' => 'arguments JSON inválido: ' . $argumentsRaw, 'tipo' => 'json_invalido'], 1);
    }

    $contextoRaw = r_argv(3);
    $contexto = $contextoRaw !== '' ? json_decode($contextoRaw, true) : [];
    if (!is_array($contexto)) {
        $contexto = [];
    }

    $resultado = $registry->executeTool($toolNombre, $arguments, $contexto);
    r_emitir(['success' => true, 'tool' => $toolNombre, 'resultado' => $resultado]);
} catch (Throwable $e) {
    r_emitir([
        'success' => false,
        'error'   => 'ejecutar_tool_cli: ' . $e->getMessage(),
        'tipo'    => get_class($e),
    ], 1);
}

// ═══════════════════════════════════════════════════════════════════════
// MUERTE GARANTIZADA: el proceso JAMÁS queda vivo en background.
// ═══════════════════════════════════════════════════════════════════════
gc_collect_cycles();
exit(0); // ← SAGRADO. Nunca quitar.
