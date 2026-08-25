<?php

declare(strict_types=1);

/**
 * Arnés de validación forzada a NVIDIA Cloud (sin ping previo a Ollama).
 *
 * Instancia NvidiaBrainClient en modo legacy contra el endpoint real de
 * NVIDIA NIM (https://integrate.api.nvidia.com/v1/chat/completions) con el
 * modelo meta/llama-3.3-70b-instruct y valida que la API Key actual responda
 * HTTP 200 exacto con el prompt de operatividad del Proyecto ARA.
 *
 * PRE-FLIGHT:
 *   - Extensiones requeridas: curl, mbstring → si falta alguna, JSON
 *     {"status":"ERROR_DRIVER_MISSING",...} y exit(500).
 *   - API Key: NVIDIA_BRAIN_API_KEY → NVIDIA_API_KEY_1 → .env (raíz del
 *     proyecto). Sin API key → error de configuración y exit(1).
 *
 * EJECUCIÓN (Cloud directo, sin sondeo local):
 *   - Cliente forzado al proveedor Cloud (PROVIDER_NVIDIA_CLOUD): un único
 *     endpoint NIM vía baseUrl explícita (el alias 'nvidia' del constructor
 *     no está implementado; se usa la URL exacta en modo legacy).
 *   - Timeouts cURL: conexión 5s / respuesta 30s (fast-fail cloud del
 *     cliente), sin reintentos (maxRetries=0).
 *   - Prompt: "Confirma en una oración corta que estás operativo como motor
 *     de soporte para Proyecto ARA."
 *
 * VALIDACIÓN:
 *   - http_code debe ser exactamente 200 (expuesto en el contrato del
 *     cliente, incluso en éxito).
 *   - HTTP 410 (modelo EOL) o 404 (no habilitado) → JSON de error con el
 *     detalle del servidor y exit(1).
 *   - sanitizar_utf8_recursivo() aplicado al texto retornado (regla del
 *     proyecto: el cloud puede devolver secuencias que rompan json_encode).
 *
 * SALIDA JSON (STDOUT):
 *   {"status","provider","model","http_code","latency_ms","response"}
 *
 * Códigos de salida:
 *   0    HTTP 200 con texto de respuesta no vacío
 *   1    fallo de validación (HTTP != 200, 410/404, texto vacío, sin API key)
 *   2    error inesperado (Throwable no controlado)
 *   500  extensiones PHP requeridas faltantes
 *
 * Uso:
 *   php bin/test_nvidia_cloud_forced.php
 */

use App\Services\NvidiaBrain\NvidiaBrainClient;

// ═══════════════════════════════════════════════════════════════════════
// CANDADO ANTI-ZOMBI (HOTFIX v4.17.1): el script se suicida a los N
// segundos pase lo que pase. Default 300s; override con env ARA_CLI_MAX_S;
// tope 600s. Watchdog por tick: si excede 240s, aborta.
// ═══════════════════════════════════════════════════════════════════════
declare(ticks=1);
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
        fwrite(STDERR, 'HOTFIX_KILL: bin/test_nvidia_cloud_forced.php excedió tiempo (' . $t_maxS . 's). Proceso abortado.' . PHP_EOL);
        error_log('HOTFIX_KILL: bin/test_nvidia_cloud_forced.php excedió tiempo (' . $t_maxS . 's)');
        exit(1);
    }
});

// ── Pre-Flight 1: extensiones críticas ─────────────────────────────────────
$faltantes = [];
foreach (['curl', 'mbstring'] as $ext) {
    if (!extension_loaded($ext)) {
        $faltantes[] = $ext;
    }
}
if ($faltantes !== []) {
    fwrite(STDERR, json_encode([
        'status'  => 'ERROR_DRIVER_MISSING',
        'message' => 'Extensión PHP requerida no instalada: ' . implode(', ', $faltantes),
        'code'    => 500,
    ], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . PHP_EOL);
    exit(500);
}

/**
 * Mini cargador de .env (solo claves NVIDIA, sin exponer valores).
 *
 * @return array<string,string>
 */
function nvidia_brain_env(string $file): array
{
    $out = [];
    if (!is_file($file)) {
        return $out;
    }
    foreach (file($file, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) ?: [] as $line) {
        $line = trim($line);
        if ($line === '' || $line[0] === '#' || !str_contains($line, '=')) {
            continue;
        }
        [$key, $value] = array_map('trim', explode('=', $line, 2));
        if (preg_match('/^NVIDIA_BRAIN_|^NVIDIA_API_KEY_1$/', $key)) {
            $out[$key] = trim($value, "\"'");
        }
    }
    return $out;
}

/**
 * Sanitización UTF-8 recursiva (regla de proyecto, CP1252 → UTF-8).
 */
function sanitizar_utf8_recursivo(mixed $v): mixed
{
    if (is_string($v)) {
        return mb_convert_encoding(trim($v), 'UTF-8', 'UTF-8, Windows-1252, ISO-8859-1');
    }
    if (is_array($v)) {
        $limpio = [];
        foreach ($v as $k => $item) {
            $clave = is_string($k)
                ? mb_convert_encoding(trim($k), 'UTF-8', 'UTF-8, Windows-1252, ISO-8859-1')
                : $k;
            $limpio[$clave] = sanitizar_utf8_recursivo($item);
        }
        return $limpio;
    }
    return $v;
}

/**
 * Extrae el texto de la respuesta OpenAI v1 (content string o partes).
 */
function extraer_texto_respuesta(array $data): string
{
    $contenido = $data['choices'][0]['message']['content'] ?? null;
    if (is_array($contenido)) {
        $piezas = [];
        foreach ($contenido as $parte) {
            if (is_array($parte) && isset($parte['text'])) {
                $piezas[] = (string) $parte['text'];
            }
        }
        return trim(implode('', $piezas));
    }
    return trim((string) $contenido);
}

// ── Envoltorio principal con manejo global de excepciones ──────────────────
try {
    // ── Pre-Flight 2: API Key de NVIDIA Cloud ──────────────────────────────
    $env = nvidia_brain_env(__DIR__ . '/../.env');
    $apiKey = getenv('NVIDIA_BRAIN_API_KEY') ?: (getenv('NVIDIA_API_KEY_1') ?: ($env['NVIDIA_BRAIN_API_KEY'] ?? ($env['NVIDIA_API_KEY_1'] ?? '')));

    $MODELO = 'meta/llama-3.3-70b-instruct';
    $PROMPT = 'Confirma en una oración corta que estás operativo como motor de soporte para Proyecto ARA.';

    if ($apiKey === '') {
        $payload = [
            'status'   => 'ERROR',
            'provider' => 'nvidia_cloud',
            'model'    => $MODELO,
            'http_code'=> null,
            'latency_ms' => 0,
            'error'    => 'API Key de NVIDIA Cloud no configurada (NVIDIA_BRAIN_API_KEY / NVIDIA_API_KEY_1 / .env).',
        ];
        echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . PHP_EOL;
        exit(1);
    }

    require __DIR__ . '/../app/Services/NvidiaBrain/NvidiaBrainClient.php';

    // Pool de un SOLO modelo (el objetivo): la rotación interna del cliente
    // hacia failovers (NVIDIA_BRAIN_MODELS) no debe enmascarar el resultado
    // del modelo validado. Se sobreescribe la env para esta ejecución.
    putenv('NVIDIA_BRAIN_MODELS=' . $MODELO);

    // ── Ejecución directa a Cloud (PROVIDER_NVIDIA_CLOUD forzado) ───────────
    // baseUrl explícita = único endpoint NIM (modo legacy sin pool local);
    // timeouts cURL 5s conexión / 30s respuesta; sin reintentos.
    $client = new NvidiaBrainClient(
        baseUrl: NvidiaBrainClient::NIM_BASE_URL,
        apiKey: $apiKey,
        model: $MODELO,
        timeoutS: 30,
        connectTimeout: 5,
        maxRetries: 0,
        debug: true
    );

    $tInicio = microtime(true);
    $res = $client->chatCompletions(
        [['role' => 'user', 'content' => $PROMPT]],
        [],
        'none',
        ['temperature' => 0.1, 'max_tokens' => 150]
    );
    $latencyMs = (int) round((microtime(true) - $tInicio) * 1000);

    $httpCode = (int) ($res['http_status'] ?? 0);
    $texto = sanitizar_utf8_recursivo(extraer_texto_respuesta($res['data'] ?? []));
    $modelo = (string) ($res['model'] ?? $MODELO);
    $error = $res['error'] ?? null;

    // ── Captura y validación de la respuesta ────────────────────────────────
    $payload = [
        'status'     => 'OK',
        'provider'   => 'nvidia_cloud',
        'model'      => $modelo,
        'http_code'  => $httpCode,
        'latency_ms' => $latencyMs,
        'response'   => $texto !== '' ? $texto : null,
    ];

    if ($httpCode !== 200) {
        $payload['status'] = 'ERROR';
        if ($httpCode === 410 || $httpCode === 404) {
            $payload['error'] = sprintf(
                'Modelo no disponible en el catálogo NVIDIA NIM (HTTP %d): %s',
                $httpCode,
                (string) ($error ?? 'detalle no disponible')
            );
        } else {
            $payload['error'] = (string) ($error ?? 'El proveedor cloud no respondió HTTP 200.');
        }
        $payload['response'] = null;
        echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . PHP_EOL;
        exit(1);
    }

    if ($texto === '') {
        $payload['status'] = 'ERROR';
        $payload['error'] = 'HTTP 200 pero el modelo devolvió contenido vacío.';
        echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . PHP_EOL;
        exit(1);
    }

    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . PHP_EOL;
    exit(0);
} catch (Throwable $e) {
    $payload = [
        'status'     => 'ERROR',
        'provider'   => 'nvidia_cloud',
        'model'      => 'meta/llama-3.3-70b-instruct',
        'http_code'  => null,
        'latency_ms' => 0,
        'error'      => get_class($e) . ': ' . sanitizar_utf8_recursivo($e->getMessage()),
    ];
    fwrite(STDERR, json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . PHP_EOL);
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . PHP_EOL;
    exit(2);
}
