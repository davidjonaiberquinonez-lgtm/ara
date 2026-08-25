<?php

declare(strict_types=1);

declare(ticks=1);

// ═══════════════════════════════════════════════════════════════════════
// CANDADO ANTI-ZOMBI (Orden v4.14): el script se suicida a los N segundos
// pase lo que pase. Default 300s (usa LLM local/cloud); override con env
// ARA_CLI_MAX_S; tope 600s. Evita procesos muertos en background.
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
 * Prueba CLI de integración del agente ARA BRAIN (cliente unificado de IA).
 *
 * Arma el stack completo (cliente HTTP + registro de tools + motor de
 * Tool Calling) con las herramientas reales del módulo (ConsultarNotaTool,
 * BuscarInventarioTool, ConsultarSaldoClienteTool, LeerVoucherOcrTool y
 * ConciliarFacturaTool) y ejecuta una consulta en lenguaje natural.
 *
 * Pool de proveedores (POOL ROUTING):
 *   PROVEEDOR 1 (PRIMARIO OBLIGATORIO): Ollama Local
 *     http://localhost:11434/v1 — qwen2.5-coder:3b (3s conexión / 25s respuesta)
 *   PROVEEDOR 2 (FALLBACK): NVIDIA NIM
 *     https://integrate.api.nvidia.com/v1 — deepseek-ai/deepseek-v4-flash
 *
 * BLINDAJE DE ARNÉS (reglas del proyecto):
 *   - Pre-Flight Checks: extensiones críticas (pdo_odbc, curl, mbstring)
 *     antes de instanciar cURL/ODBC; si falta una → JSON
 *     {"status":"ERROR_DRIVER_MISSING", ...} y salida con código > 0.
 *   - Sondeo Ollama Fast-Fail: GET /api/tags con timeout de conexión 1s; si
 *     no responde → advertencia [PreFlight] + degradado seguro a Cloud-only
 *     (blacklist de sesión del puerto local, sin bucles locales).
 *   - Manejo global de excepciones: todo el flujo en try-catch (\Throwable);
 *     excepción no controlada → JSON FATAL_ERROR en STDERR y salida != 0.
 *   - Sanitización UTF-8 recursiva: los datos de Profit (Windows-1252) nunca
 *     rompen json_encode() ni la consola.
 *
 * Configuración (variables de entorno, con defaults):
 *   OLLAMA_BASE_URL            Ollama local (default: http://localhost:11434)
 *   OLLAMA_MODEL               Modelo local (default: qwen2.5-coder:3b)
 *   NVIDIA_BRAIN_BASE_URL      NVIDIA NIM (default: https://integrate.api.nvidia.com)
 *   NVIDIA_BRAIN_API_KEY       API key (default: NVIDIA_API_KEY_1 del .env)
 *   NVIDIA_BRAIN_MODEL         Modelo cloud (default: deepseek-ai/deepseek-v4-flash)
 *
 * Uso:
 *   php bin/test_nvidia_brain.php [numero_nota]
 *
 * Códigos de salida:
 *   0   éxito
 *   1   NvidiaBrainException (error de comunicación manejado)
 *   2   error inesperado (Throwable no controlado)
 *   500 driver/extensiones requeridas faltantes
 */

use App\Services\NvidiaBrain\NvidiaBrain;
use App\Services\NvidiaBrain\NvidiaBrainClient;
use App\Services\NvidiaBrain\NvidiaBrainException;
use App\Services\NvidiaBrain\NvidiaBrainResponse;
use App\Services\NvidiaBrain\OpenCodeAgentEngine;
use App\Services\NvidiaBrain\ToolRegistry;
use App\Services\NvidiaBrain\Security\CustomerAuthenticator;

// ── PRE-FLIGHT CHECK 1: extensiones críticas de PHP ────────────────────────
// Se valida ANTES de instanciar clientes cURL o conexiones ODBC. Si falta
// alguna extensión, se detiene la ejecución con un JSON estructurado y un
// código de salida > 0 (nunca un "Unhandled Fatal Error" de PHP).
$extensionesRequeridas = ['pdo_odbc', 'curl', 'mbstring'];
foreach ($extensionesRequeridas as $ext) {
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

require __DIR__ . '/../app/Services/NvidiaBrain/NvidiaBrainException.php';
require __DIR__ . '/../app/Services/NvidiaBrain/NvidiaBrainClient.php';
require __DIR__ . '/../app/Services/NvidiaBrain/NvidiaBrain.php';
require __DIR__ . '/../app/Services/NvidiaBrain/Contracts/AgentToolInterface.php';
require __DIR__ . '/../app/Services/NvidiaBrain/Contracts/IaLoggerInterface.php';
require __DIR__ . '/../app/Services/NvidiaBrain/ToolRegistry.php';
require __DIR__ . '/../app/Services/NvidiaBrain/OpenCodeRules.php';
require __DIR__ . '/../app/Services/NvidiaBrain/NvidiaBrainResponse.php';
require __DIR__ . '/../app/Services/NvidiaBrain/OpenCodeAgentEngine.php';
require __DIR__ . '/../app/Services/NvidiaBrain/Security/CustomerAuthenticator.php';

/**
 * Capa de sanitización UTF-8 recursiva.
 *
 * Los datos recuperados de Profit Plus llegan en Windows-1252/CP1252; esta
 * función normaliza strings y claves a UTF-8 para que json_encode() nunca
 * falle y la consola de Windows no muestre caracteres corruptos.
 *
 * @param mixed $data
 *
 * @return mixed
 */
function sanitizar_utf8_recursivo($data)
{
    if (is_string($data)) {
        return mb_convert_encoding(trim($data), 'UTF-8', 'UTF-8, Windows-1252, ISO-8859-1');
    }
    if (is_array($data)) {
        $clean = [];
        foreach ($data as $key => $value) {
            $cleanKey = is_string($key)
                ? mb_convert_encoding(trim($key), 'UTF-8', 'UTF-8, Windows-1252, ISO-8859-1')
                : $key;
            $clean[$cleanKey] = sanitizar_utf8_recursivo($value);
        }
        return $clean;
    }
    return $data;
}

/**
 * PRE-FLIGHT CHECK 2: sondeo de disponibilidad de Ollama Local (Fast-Fail).
 *
 * GET /api/tags con timeout de conexión estricto de 1 segundo. Si no
 * responde, se degrada el arnés a Cloud-only (se blacklistea el puerto local
 * para que el pool NO intente bucles locales) y se retorna false.
 *
 * @param string $baseUrl URL base de Ollama (sin /v1).
 */
function preflight_ollama_online(string $baseUrl): bool
{
    $url = rtrim($baseUrl, '/') . '/api/tags';
    $ch  = curl_init($url);
    if ($ch === false) {
        return false;
    }
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_CONNECTTIMEOUT => 1,      // Fast-Fail estricto: 1s de conexión
        CURLOPT_TIMEOUT        => 2,      // tope absoluto de la sonda
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_SSL_VERIFYPEER => false,
        CURLOPT_SSL_VERIFYHOST => 0,
        CURLOPT_HTTPHEADER     => ['Accept: application/json'],
    ]);
    $respuesta = curl_exec($ch);
    $errno     = curl_errno($ch);
    $status    = (int) curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    curl_close($ch);
    return $respuesta !== false && $errno === 0 && $status === 200;
}

/**
 * Emite el JSON FATAL_ERROR (STDERR + STDOUT) para excepciones no controladas.
 *
 * @param \Throwable $e Excepción capturada.
 *
 * @return int Código de salida (siempre != 0).
 */
function emitir_fatal_error(\Throwable $e): int
{
    $payload = [
        'status' => 'FATAL_ERROR',
        'error'  => sanitizar_utf8_recursivo($e->getMessage()),
        'file'   => $e->getFile(),
        'line'   => $e->getLine(),
    ];
    $json = json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    if ($json === false) {
        $json = '{"status":"FATAL_ERROR","error":"(mensaje no serializable a JSON)"}';
    }
    fwrite(STDERR, $json . PHP_EOL);
    echo $json . PHP_EOL;
    return 2;
}

/**
 * Mini cargador de .env: lee solo las claves pedidas, sin exponer valores.
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

// ── MODO AUTENTICACIÓN DE CLIENTE (CustomerAuthenticator vs PRUEB25) ──────
// Uso:
//   php bin/test_nvidia_brain.php --paso0 --co_cli="FAR01361"
//   php bin/test_nvidia_brain.php --rol=ROL_CLIENTE --co_cli="FAR01361" --auth_factor="1234" --query="..."
// Códigos de salida: 0 autenticado OK · 1 acceso denegado o argumentos inválidos.

/**
 * Lee el valor de un flag CLI --clave=valor.
 */
function nvidia_brain_flag(array $argv, string $flag): string
{
    $prefijo = '--' . $flag . '=';
    foreach ($argv as $argumento) {
        if (str_starts_with($argumento, $prefijo)) {
            return trim(substr($argumento, strlen($prefijo)), "\"'");
        }
    }
    return '';
}

/**
 * Conexión PDO de solo lectura a PRUEB25 (sqlsrv si está disponible, si no
 * ODBC "SQL Server"): credenciales profit/profit en 192.168.4.20.
 */
function conectar_profit_read(): PDO
{
    $host = getenv('PROFIT_SQL_HOST') ?: '192.168.4.20';
    $name = getenv('PROFIT_SQL_NAME') ?: 'PRUEB25';
    $user = getenv('PROFIT_SQL_USER') ?: 'profit';
    $pass = getenv('PROFIT_SQL_PASS') ?: 'profit';
    $dsn = in_array('sqlsrv', PDO::getAvailableDrivers(), true)
        ? "sqlsrv:Server=$host,1433;Database=$name"
        : "odbc:Driver={SQL Server};Server=$host,1433;Database=$name";
    return new PDO($dsn, $user, $pass, [
        PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_TIMEOUT            => 8,
    ]);
}

$modoAuth = false;
foreach ($argv as $argumento) {
    if (str_starts_with($argumento, '--rol=') || $argumento === '--paso0') {
        $modoAuth = true;
        break;
    }
}

if ($modoAuth) {
    try {
        $coCli = nvidia_brain_flag($argv, 'co_cli') !== ''
            ? nvidia_brain_flag($argv, 'co_cli')
            : 'FAR01361';
        $pdo = conectar_profit_read();

        // ── PASO 0: extracción de datos reales del cliente ────────────────
        if (in_array('--paso0', $argv, true)) {
            $stmt = $pdo->prepare(
                'SELECT [co_cli] AS co_cli, [cli_des] AS cli_des, RTRIM([rif]) AS rif, '
                . 'RTRIM([telefonos]) AS telefonos FROM [clientes] '
                . 'WHERE LTRIM(RTRIM([co_cli])) = ?'
            );
            $stmt->execute([$coCli]);
            $fila = $stmt->fetch(PDO::FETCH_ASSOC);
            if (!is_array($fila) || $fila === []) {
                echo json_encode([
                    'status'  => 'error',
                    'message' => 'Cliente no encontrado: ' . $coCli,
                ], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . PHP_EOL;
                exit(1);
            }
            $fila = sanitizar_utf8_recursivo($fila);
            $digitos = preg_replace('/\D/', '', (string) $fila['telefonos']) ?? '';
            $fila['ultimos_4'] = strlen($digitos) >= 4 ? substr($digitos, -4) : '';
            $fila['columnas_telef_disponibles'] = $pdo->query(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                . "WHERE TABLE_NAME = 'clientes' AND COLUMN_NAME IN ('telefonos','telef','telefono')"
            )->fetchAll(PDO::FETCH_COLUMN);
            echo json_encode(
                ['status' => 'ok', 'data' => $fila],
                JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES
            ) . PHP_EOL;
            exit(0);
        }

        // ── CASOS A/B/C: validación de identidad (CustomerAuthenticator) ───
        $factor = nvidia_brain_flag($argv, 'auth_factor');
        if ($factor === '') {
            fwrite(STDERR, '{"status":"error","message":"Falta --auth_factor"}' . PHP_EOL);
            exit(1);
        }
        $auth = new CustomerAuthenticator();
        $cliente = sanitizar_utf8_recursivo($auth->validarCliente($pdo, $coCli, $factor));
        if ($cliente === null) {
            echo json_encode([
                'status'  => 'error',
                'data'    => null,
                'message' => 'Acceso denegado: Los datos de validación no coinciden con nuestros registros.',
            ], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . PHP_EOL;
            exit(1);
        }
        echo json_encode([
            'status'  => 'success',
            'data'    => [
                'authenticated' => true,
                'cliente'       => [
                    'co_cli'  => (string) ($cliente['co_cli'] ?? ''),
                    'cli_des' => (string) ($cliente['nombre'] ?? ''),
                    'rif'     => (string) ($cliente['rif'] ?? ''),
                ],
            ],
            'message' => 'Cliente autenticado exitosamente.',
        ], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . PHP_EOL;
        exit(0);
    } catch (\Throwable $e) {
        exit(emitir_fatal_error($e));
    }
}

// ── Envoltorio principal con manejo global de excepciones ─────────────────
try {
    // ── Configuración ──────────────────────────────────────────────────────
    $env = nvidia_brain_env(__DIR__ . '/../.env');

    $baseUrl = getenv('NVIDIA_BRAIN_BASE_URL') ?: ($env['NVIDIA_BRAIN_BASE_URL'] ?? '');
    $apiKey  = getenv('NVIDIA_BRAIN_API_KEY')  ?: ($env['NVIDIA_BRAIN_API_KEY'] ?? ($env['NVIDIA_API_KEY_1'] ?? ''));
    $model   = getenv('NVIDIA_BRAIN_MODEL')    ?: ($env['NVIDIA_BRAIN_MODEL'] ?? '');
    $modeloLocal = getenv('OLLAMA_MODEL') ?: ($env['OLLAMA_MODEL'] ?? '');
    $ollamaBase = getenv('OLLAMA_BASE_URL') ?: 'http://localhost:11434';

    $numNota = $argv[1] ?? 'A0467959';

    // PRE-FLIGHT: sondeo Ollama Local (Fast-Fail 1s). Degradación Cloud-only.
    $ollamaOnline = preflight_ollama_online($ollamaBase);
    if (!$ollamaOnline) {
        fwrite(STDERR, '[PreFlight] ⚠️ Ollama Local no está respondiendo en localhost:11434.' . PHP_EOL);
        // Conmutar el indicador de entorno: el arnés degrada a Cloud-only.
        // Se blacklistea el puerto local para que el pool NO intente bucles
        // locales y conmute directo a NVIDIA NIM / DeepSeek.
        NvidiaBrainClient::blacklistEndpoint(
            $ollamaBase,
            'PreFlight: Ollama Local offline (fast-fail 1s)'
        );
    }

    echo "== ARA BRAIN - Prueba de integracion (pool de proveedores) ==" . PHP_EOL;
    echo 'Proveedor 1 (primario): Ollama Local  ' . $ollamaBase
        . ' | modelo: ' . ($modeloLocal !== '' ? $modeloLocal : 'qwen2.5-coder:3b (default)')
        . ' | estado: ' . ($ollamaOnline ? 'ONLINE' : 'OFFLINE (degradado a Cloud-only)') . PHP_EOL;
    echo 'Proveedor 2 (fallback): NVIDIA NIM    ' . ($baseUrl !== '' ? $baseUrl : 'https://integrate.api.nvidia.com')
        . ' | modelo: ' . ($model !== '' ? $model : 'deepseek-ai/deepseek-v4-flash (default)') . PHP_EOL;
    echo 'API key NVIDIA: ' . ($apiKey !== '' ? 'configurada' : 'NO configurada (solo se usará si Ollama falla)') . PHP_EOL;
    echo 'Nota:     ' . $numNota . PHP_EOL . PHP_EOL;

    // ── Stack del agente ───────────────────────────────────────────────────
    // Cliente unificado ARA Brain con POOL DE PROVEEDORES + FAILOVER:
    //  - Ollama local (qwen2.5-coder:3b) como proveedor PRIMARIO OBLIGATORIO
    //    (conexión 3s / respuesta 25s, Fast-Fail).
    //  - Si Ollama no responde (conexión rechazada, timeout o error de
    //    servidor), conmuta automáticamente a NVIDIA NIM
    //    (deepseek-ai/deepseek-v4-flash). Si el PreFlight lo marcó OFFLINE,
    //    el endpoint local ya está blacklisted y el pool va directo al Cloud.
    //  - Circuit Breaker de sesión: un endpoint que falle se blacklista y no
    //    se vuelve a intentar en la misma ejecución.
    $brain = new NvidiaBrain(
        baseUrl: $baseUrl !== '' ? $baseUrl : null,   // null → pool Ollama → NVIDIA
        apiKey: $apiKey !== '' ? $apiKey : null,
        model: $modeloLocal !== '' ? $modeloLocal : null,
        debug: true
    );

    $registry = new ToolRegistry();

    // ── Carga automática de Tools departamentales (recursiva) ───────────────
    // Almacen/, Auditoria/, Compras/, Despacho/ y Recepcion/ se registran
    // solos vía ToolRegistry::loadFromDirectory() (incluye los activadores
    // PHP de las Skills de Python). Ninguna referencia directa a la raíz
    // antigua de Tools/ (v4.1: centralización departamental completa).
    $registry->loadFromDirectory(__DIR__ . '/../app/Services/NvidiaBrain/Tools');

    // maxIteraciones del motor: fijo a 5 (OpenCodeAgentEngine::MAX_ITERACIONES).
    $engine = new OpenCodeAgentEngine(
        client: $brain->getClient(),
        registry: $registry,
        debug: true
    );

    $prompt = sprintf(
        'Hola, consulta la nota %s y dime qué cliente es y cuántos bultos/ítems tiene.',
        $numNota
    );

    // ── Ejecución con manejo de errores legible ────────────────────────────
    try {
        $resultado = $engine->run(
            prompt: $prompt,
            modulo: 'Picking & Rutas',
            contexto: [
                'usuario_id'   => 0,
                'nombre_usuario' => 'Operador de Pruebas',
                'rol'          => 'OPERADOR',
                'contexto_extra' => 'Prueba de integracion de NvidiaBrain en ARA v4.',
            ],
            overrides: ['temperature' => 0.1, 'max_tokens' => 1024]
        );

        $resultado = sanitizar_utf8_recursivo($resultado);
        $resp = NvidiaBrainResponse::fromEngine($resultado);
        echo '== RESULTADO ==' . PHP_EOL;
        echo 'Status:     ' . $resp['status'] . PHP_EOL;
        echo 'Proveedor:  ' . ($brain->getActiveProvider() ?? '?') . ' | Modelo: ' . ($resultado['model'] ?? '?') . PHP_EOL;
        echo 'Iteraciones:' . $resp['iteraciones'] . ' | Tiempo: ' . $resp['tiempo_ms'] . 'ms'
            . ($resp['max_agotado'] ? ' | LIMITE ALCANZADO' : '') . PHP_EOL . PHP_EOL;
        echo $resp['respuesta'] . PHP_EOL . PHP_EOL;

        if ($resp['tool_calls'] !== []) {
            echo '== Tools ejecutadas ==' . PHP_EOL;
            foreach ($resp['tool_calls'] as $tc) {
                echo '- ' . $tc['name'] . ' ' . json_encode($tc['arguments']) . PHP_EOL;
            }
        }
    } catch (NvidiaBrainException $e) {
        echo PHP_EOL . '== ERROR DE COMUNICACION ==' . PHP_EOL;
        echo 'Mensaje:  ' . $e->getMessage() . PHP_EOL;
        echo 'HTTP:     ' . ($e->getHttpStatus() ?? 'N/A (fallo de red/cURL)') . PHP_EOL;
        echo 'Reintentable: ' . ($e->isRetryable() ? 'si' : 'no') . PHP_EOL;
        echo 'Backoff:  ' . $e->getRetryDelayS() . 's' . PHP_EOL;
        exit(1);
    } catch (Throwable $e) {
        echo PHP_EOL . '== ERROR INESPERADO ==' . PHP_EOL;
        echo get_class($e) . ': ' . $e->getMessage() . PHP_EOL;
        exit(2);
    }
} catch (\Throwable $e) {
    // Excepción no controlada en el envoltorio principal (incluye fallos de
    // configuración, construcción del stack o drivers): JSON estructurado.
    exit(emitir_fatal_error($e));
}
