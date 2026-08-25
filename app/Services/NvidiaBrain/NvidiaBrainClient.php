<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain;

/**
 * Cliente HTTP cURL UNIFICADO de ARA Brain con POOL DE PROVEEDORES Y FAILOVER.
 *
 * PROVEEDOR 1 (PRIMARIO OBLIGATORIO - LOCAL):
 *   Ollama local (API OpenAI-Compatible): http://localhost:11434/v1
 *   - Modelo principal (código, lógica, tools, JSON): qwen2.5-coder:3b
 *   - Visión/OCR: llava:7b | Texto rápido: phi3:latest / mini-ara:latest
 *   - Timeouts agresivos (Fast-Fail): conexión 3s, respuesta máx 25s.
 *     Si el modelo local supera 25s se aborta, se blacklista el endpoint y se
 *     conmuta INMEDIATAMENTE a NVIDIA NIM sin demoras.
 *
 * PROVEEDOR 2 (SECUNDARIO / FALLBACK CLOUD):
 *   NVIDIA NIM: https://integrate.api.nvidia.com/v1
 *   - Modelos: meta/llama-3.3-70b-instruct (primario), meta/llama-3.1-8b-instruct
 *   - Timeouts cloud: conexión 5s, respuesta 30s.
 *
 * FAILOVER: cada petición inicia contra Ollama local (/v1/chat/completions con
 * qwen2.5-coder:3b). Si responde HTTP 200 se procesa la respuesta (incluyendo
 * tool_calls) y termina la iteración. Si Ollama falla (conexión rechazada,
 * timeout > 25s o error de servidor) se registra la advertencia
 * "[AraBrain] ⚠️ Ollama Local no respondió..." y se conmuta a NVIDIA NIM.
 *
 * CIRCUIT BREAKER: $blacklistedEndpoints (estático, dura toda la sesión PHP):
 * el puerto 11434 que no responde o NVIDIA con HTTP 529/503 se marcan de
 * inmediato y no se vuelven a intentar en la misma ejecución.
 *
 * Modo legacy: si se pasa una baseUrl explícita distinta de los alias de
 * proveedor ('ollama'/'local'/'nvidia'/'nim'), el cliente opera contra UN solo
 * endpoint con la configuración dada (compatibilidad total con
 * OpenCodeAgentEngine, NvidiaBrain y herramientas como LeerVoucherOcrTool).
 *
 * Contrato de retorno (SIEMPRE, sin excepciones hacia el llamador):
 *   [
 *     'success' => bool,            // true si HTTP 2xx y JSON válido
 *     'data'    => array,           // respuesta completa del servidor (choices, etc.)
 *     'error'   => string|null,     // mensaje legible cuando success=false
 *   ]
 *   (chatCompletions añade 'provider'/'proveedor' y 'model' del que respondió).
 *
 * Uso con pool (recomendado):
 *   $client = new NvidiaBrainClient();   // Ollama primario + NVIDIA fallback
 *   $res = $client->chatCompletions($messages, $tools);
 *   if ($res['success']) { $texto = $res['data']['choices'][0]['message']['content']; }
 */
final class NvidiaBrainClient
{
    /** Versión del esquema OpenAI que consume el cliente. */
    public const OPENAI_API_VERSION = 'v1';

    /** JSON flags: UTF-8 estricto + slashes sin escape. */
    public const JSON_FLAGS = JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR;

    /** Timeout de conexión por defecto (segundos). */
    public const DEFAULT_CONNECT_TIMEOUT = 15;

    /** Timeout de respuesta por defecto (segundos). */
    public const DEFAULT_TIMEOUT = 90;

    /** Máximo de reintentos por defecto ante fallos transitorios. */
    public const DEFAULT_MAX_RETRIES = 3;

    // ── PROVEEDOR 1: OLLAMA LOCAL (PRIMARIO OBLIGATORIO) ─────────────────────
    /** URL base de Ollama local (sin '/v1'; el cliente lo añade). */
    public const OLLAMA_BASE_URL = 'http://localhost:11434';

    /**
     * Modelo principal local (código, lógica, tools, JSON).
     * qwen2.5-coder:3b: el 7b excede 25s cuando el contexto crece en la
     * iteración 2 (después de ejecutar un tool) y paraliza la aplicación.
     */
    public const OLLAMA_MODEL = 'qwen2.5-coder:3b';

    /** Modelo de visión/OCR local. */
    public const OLLAMA_VISION_MODEL = 'llava:7b';

    /** Modelo rápido de texto/resúmenes local. */
    public const OLLAMA_FAST_MODEL = 'phi3:latest';

    /** Timeout de conexión local (s): Fast-Fail a los 3s. */
    public const DEFAULT_CONNECT_TIMEOUT_LOCAL = 3;

    /**
     * Timeout de respuesta local (s): máximo ESTRICTO de 25s (Fast-Fail).
     * Si el modelo local lo supera, la conexión se aborta (cURL errno 28),
     * el endpoint se añade al Circuit Breaker en runtime y se conmuta
     * INMEDIATAMENTE a NVIDIA Cloud. Ajustable con la env OLLAMA_TIMEOUT_S.
     */
    public const DEFAULT_TIMEOUT_LOCAL = 25;

    // ── PROVEEDOR 2: NVIDIA NIM CLOUD (FALLBACK) ─────────────────────────────
    /** URL base de NVIDIA NIM (sin '/v1'). */
    public const NIM_BASE_URL = 'https://integrate.api.nvidia.com';

    /** Timeout de conexión cloud (s). */
    public const DEFAULT_CONNECT_TIMEOUT_CLOUD = 5;

    /** Timeout de respuesta cloud (s): 30s para tolerar payloads con tools
     *  (llama-3.3-70b-instruct paga cold start variable; Fast-Fail a 30s). */
    public const DEFAULT_TIMEOUT_CLOUD = 30;

    /**
     * Modelo principal de respaldo cloud.
     * Sustituye a deepseek-ai/deepseek-v4-flash (llegó a su fin de vida, HTTP
     * 410, el 2026-08-07T09:00:00Z y ya no está disponible en NVIDIA NIM).
     * meta/llama-3.1-8b-instruct es el primario verificado (HTTP 200 con la
     * API key actual del proyecto, latencia estable ~2-4s) y
     * meta/llama-3.3-70b-instruct queda como failover de capacidad en
     * DEFAULT_MODEL_POOL (validado con bin/test_nvidia_cloud_forced.php:
     * aún no responde dentro del Fast-Fail de 30s con esta key).
     */
    public const NIM_FALLBACK_MODEL = 'meta/llama-3.1-8b-instruct';

    /**
     * Pool de modelos candidatos por defecto (fallback en código cuando la
     * variable de entorno NVIDIA_BRAIN_MODELS no está definida o vacía).
     * Orden = preferencia: primario primero, failovers después.
     *   - meta/llama-3.1-8b-instruct   → HTTP 200, latencia estable (primario)
     *   - meta/llama-3.3-70b-instruct  → failover de capacidad (sustituye a
     *     deepseek-ai/deepseek-v4-flash; timeout 30s con la key actual, se
     *     usará si el primario falla).
     * Deprecados/eliminados: deepseek-ai/deepseek-v4-flash (410 EOL
     * 2026-08-07), mixtral-8x22b (baja 2026-05-21), deepseek-r1/v3 (404).
     *
     * @var list<string>
     */
    public const DEFAULT_MODEL_POOL = [
        'meta/llama-3.1-8b-instruct',
        'meta/llama-3.3-70b-instruct',
    ];

    /** @var string URL base sin '/v1' final. */
    private string $baseUrl;

    /** @var string|null API key (Bearer). */
    private ?string $apiKey;

    /** @var string|null Modelo por defecto si no se pasa en overrides. */
    private ?string $model;

    /** @var int Timeout de conexión TCP (segundos). */
    private int $connectTimeout;

    /** @var int Timeout total de lectura (segundos). */
    private int $timeoutS;

    /** @var int Máximo de reintentos ante fallos transitorios. */
    private int $maxRetries;

    /** @var array<string,string> Cabeceras HTTP adicionales. */
    private array $extraHeaders;

    /** @var bool Logs de depuración en error_log(). */
    private bool $debug;

    /**
     * Pool de proveedores resuelto en el constructor.
     * Cada entrada: name, label, baseUrl, apiKey, model, models,
     * connectTimeout, timeout, maxRetries, extraHeaders.
     *
     * @var list<array<string,mixed>>
     */
    private array $providers = [];

    /** @var string|null Proveedor que respondió en la última petición. */
    private ?string $activeProvider = null;

    /** @var bool Warm-up del modelo local ya intentado en esta instancia. */
    private bool $warmupHecho = false;

    /**
     * Circuit Breaker de sesión: URLs base blacklisted (estático, persiste
     * entre instancias dentro de la misma ejecución PHP).
     *
     * @var list<string>
     */
    private static array $blacklistedEndpoints = [];

    /**
     * @param string|null          $baseUrl        Alias de proveedor o URL base.
     *                                             null|''|'ollama'|'local' → POOL
     *                                             (Ollama local primario + NVIDIA NIM
     *                                             fallback). 'nvidia'|'nim' → solo
     *                                             NVIDIA. Cualquier URL → modo
     *                                             legacy de un solo endpoint.
     * @param string|null          $apiKey         Bearer token si se exige (legacy).
     * @param string|null          $model          Modelo primario (opcional).
     * @param int                  $timeoutS       Timeout de respuesta (s), default 90.
     * @param int                  $connectTimeout Timeout de conexión (s), default 15.
     * @param int                  $maxRetries     Reintentos ante fallos transitorios (default 3).
     * @param array<string,string> $extraHeaders   Cabeceras extra.
     * @param bool                 $debug          Logs en error_log().
     */
    public function __construct(
        ?string $baseUrl = null,
        ?string $apiKey = null,
        ?string $model = null,
        int $timeoutS = self::DEFAULT_TIMEOUT,
        int $connectTimeout = self::DEFAULT_CONNECT_TIMEOUT,
        int $maxRetries = self::DEFAULT_MAX_RETRIES,
        array $extraHeaders = [],
        bool $debug = false
    ) {
        $this->debug = $debug;
        $alias = strtolower(trim((string) $baseUrl));

        if ($baseUrl === null || $baseUrl === '' || $alias === 'ollama' || $alias === 'local') {
            // ── Modo POOL: Ollama local PRIMARIO + NVIDIA NIM fallback ──────
            $this->baseUrl = self::OLLAMA_BASE_URL;
            $this->apiKey = $apiKey;
            $modeloLocal = getenv('OLLAMA_MODEL');
            $this->model = is_string($modeloLocal) && trim($modeloLocal) !== ''
                ? trim($modeloLocal)
                : (($model !== null && $model !== '') ? $model : self::OLLAMA_MODEL);
            $this->connectTimeout = self::DEFAULT_CONNECT_TIMEOUT_LOCAL;
            $this->timeoutS = self::DEFAULT_TIMEOUT_LOCAL;
            $this->maxRetries = 0; // failover rápido: el pool rota de proveedor
            $this->extraHeaders = $extraHeaders;
            $this->providers = $this->defaultProviders($this->model, $apiKey ?? '');
            return;
        }

        // ── Modo legacy: un solo endpoint con la configuración dada ─────────
        $this->baseUrl = rtrim($baseUrl, '/');
        $this->apiKey = $apiKey;
        $this->model = ($model !== null && $model !== '') ? $model : null;
        $this->timeoutS = max(1, $timeoutS);
        $this->connectTimeout = max(1, $connectTimeout);
        $this->maxRetries = max(0, $maxRetries);
        $this->extraHeaders = $extraHeaders;
        $this->providers = [[
            'name'           => 'legacy',
            'label'          => 'Endpoint único',
            'baseUrl'        => $this->baseUrl,
            'apiKey'         => $this->apiKey,
            'model'          => $this->model,
            'models'         => $this->modelPool(),
            'connectTimeout' => $this->connectTimeout,
            'timeout'        => $this->timeoutS,
            'maxRetries'     => $this->maxRetries,
            'extraHeaders'   => $this->extraHeaders,
        ]];
    }

    /**
     * Construye el pool de proveedores por defecto (Ollama → NVIDIA).
     *
     * @param string $modeloLocal Modelo primario local (qwen2.5-coder:3b).
     * @param string $apiKeyCtor  API key del constructor (respaldo del cloud).
     *
     * @return list<array<string,mixed>>
     */
    private function defaultProviders(string $modeloLocal, string $apiKeyCtor = ''): array
    {
        $apiKeyCloud = getenv('NVIDIA_BRAIN_API_KEY') ?: (getenv('NVIDIA_API_KEY_1') ?: $apiKeyCtor);
        $urlCloud = getenv('NVIDIA_BRAIN_BASE_URL') ?: self::NIM_BASE_URL;
        $modeloCloud = getenv('NVIDIA_BRAIN_MODEL') ?: self::NIM_FALLBACK_MODEL;

        // Pool de modelos del proveedor local: SOLO modelos con soporte de
        // herramientas (tool calling). llava/phi3 NO soportan tools y devuelven
        // HTTP 400 "does not support tools"; se usan vía request() directo
        // (OCR/resúmenes). Extensible con la env OLLAMA_MODELS (CSV).
        $modelosLocal = [$modeloLocal];
        $extraLocal = getenv('OLLAMA_MODELS');
        if (is_string($extraLocal) && trim($extraLocal) !== '') {
            foreach (explode(',', $extraLocal) as $m) {
                $m = trim($m);
                if ($m !== '' && !in_array($m, $modelosLocal, true)) {
                    $modelosLocal[] = $m;
                }
            }
        }

        return [
            [
                'name'           => 'ollama',
                'label'          => 'Ollama Local',
                'baseUrl'        => rtrim((string) (getenv('OLLAMA_BASE_URL') ?: self::OLLAMA_BASE_URL), '/'),
                'apiKey'         => null,
                'model'          => $modeloLocal,
                'models'         => $modelosLocal,
                'connectTimeout' => (int) (getenv('OLLAMA_CONNECT_TIMEOUT_S') ?: self::DEFAULT_CONNECT_TIMEOUT_LOCAL),
                'timeout'        => (int) (getenv('OLLAMA_TIMEOUT_S') ?: self::DEFAULT_TIMEOUT_LOCAL),
                'maxRetries'     => 0,
                'extraHeaders'   => [],
            ],
            [
                'name'           => 'nvidia',
                'label'          => 'NVIDIA NIM Cloud',
                'baseUrl'        => rtrim($urlCloud, '/'),
                'apiKey'         => $apiKeyCloud !== '' ? $apiKeyCloud : null,
                'model'          => $modeloCloud,
                'models'         => $this->modelPool(),
                'connectTimeout' => (int) (getenv('NVIDIA_BRAIN_CONNECT_TIMEOUT_S') ?: self::DEFAULT_CONNECT_TIMEOUT_CLOUD),
                'timeout'        => (int) (getenv('NVIDIA_BRAIN_TIMEOUT_S') ?: self::DEFAULT_TIMEOUT_CLOUD),
                'maxRetries'     => 0, // fallback de emergencia: un intento por modelo, rotación inmediata
                'extraHeaders'   => [],
            ],
        ];
    }

    /**
     * Chat completion con soporte de Tool Calling (formato OpenAI v1) y
     * POOL DE PROVEEDORES CON FAILOVER:
     *
     *  - PROVEEDOR 1 (PRIMARIO OBLIGATORIO): Ollama local con
     *    qwen2.5-coder:3b (timeouts agresivos 3s/25s Fast-Fail).
 *  - PROVEEDOR 2 (FALLBACK): NVIDIA NIM con meta/llama-3.3-70b-instruct
 *    (timeouts 5s/30s).
     *  - Cada proveedor itera su propio pool de modelos; ante HTTP 529/429/408,
     *    cualquier 5xx o timeout cURL (errno 28) se rota de modelo y, si el
     *    proveedor entero falla, se marca en el Circuit Breaker de sesión y se
     *    conmuta al siguiente. $messages nunca se altera: el hilo de la
     *    conversación y el estado del Tool Calling se conservan intactos.
     *  - El proveedor/modelo que respondió queda en 'provider'/'model';
     *    la historia de fallos en 'failover_history'.
     *
     * @param array<int,array<string,mixed>> $messages Historial de mensajes.
     * @param array<int,array<string,mixed>> $tools    Definiciones de tools.
     * @param string|array|null              $toolChoice 'auto' | 'none' | array.
     * @param array<string,mixed>            $overrides Parámetros extra (model,
     *                                                 model_pool, temperature,
     *                                                 max_tokens...).
     *
     * @return array{success: bool, data: array, error: ?string}
     */
    public function chatCompletions(array $messages, array $tools = [], string|array|null $toolChoice = 'auto', array $overrides = []): array
    {
        $tInicio = microtime(true);
        $failoverHistory = [];
        $providers = $this->providers;

        $this->warmupOllama($providers);

        foreach ($providers as $provider) {
            if (self::isEndpointBlacklisted($provider['baseUrl'])) {
                $this->consoleLog(sprintf(
                    '[AraBrain] Endpoint %s en blacklist de sesión. Omitido.',
                    $provider['label']
                ));
                continue;
            }

            $this->consoleLog(sprintf(
                '[AraBrain] Ejecutando consulta vía %s (%s)...',
                $provider['label'],
                $provider['model']
            ));

            $res = $this->chatCompletionsConProveedor($provider, $messages, $tools, $toolChoice, $overrides);

            if ($res['success']) {
                $this->activeProvider = $provider['name'];
                $res['provider'] = $provider['name'];
                $res['proveedor'] = $provider['label'];
                $this->consoleLog(sprintf(
                    '[AraBrain] Respuesta recibida en %dms (%s).',
                    (int) round((microtime(true) - $tInicio) * 1000),
                    $provider['name'] === 'ollama' ? 'Local OK' : 'Cloud Fallback'
                ));
                if ($failoverHistory !== []) {
                    $res['failover_history'] = $failoverHistory;
                }
                return $res;
            }

            $failoverHistory[] = [
                'provider'    => $provider['name'],
                'model'       => $res['model'] ?? null,
                'http_status' => (int) ($res['http_status'] ?? 0),
                'error'       => $res['error'],
            ];

            // El proveedor falló como tal (conexión rechazada, timeout, 5xx,
            // o modelos locales sin soporte de tools): se blacklista y se rota
            // al siguiente proveedor del pool. Los mensajes nunca se alteran.
            self::blacklistEndpoint($provider['baseUrl'], $provider['label'] . ': ' . $res['error']);

            if ($provider['name'] === 'ollama' && isset($providers[1])) {
                // Fast-Fail estricto: el timeout local se aborta sin demoras.
                $ultimoModelo = (string) ($failoverHistory[count($failoverHistory) - 1]['model']
                    ?? $provider['model']);
                $this->consoleLog(sprintf(
                    '[NvidiaBrainClient] Fast-Fail: Timeout local alcanzado (>%ds) en %s. Conmutando a Cloud...',
                    (int) ($provider['timeout'] ?? self::DEFAULT_TIMEOUT_LOCAL),
                    $ultimoModelo
                ));
                $this->consoleLog('[AraBrain] ⚠️ Ollama Local no respondió o estuvo fuera de línea. Activando Fallback a Cloud...');
                $this->consoleLog('[AraBrain] Fallback -> Conectando con NVIDIA Cloud (DeepSeek)...');
            } else {
                $this->consoleLog(sprintf(
                    '[AraBrain] %s falló (%s). Probando siguiente proveedor...',
                    $provider['label'],
                    $res['error']
                ));
            }
        }

        // Todos los proveedores del pool fallaron con error recuperable.
        $ultimo = $failoverHistory[count($failoverHistory) - 1] ?? null;
        return [
            'success'          => false,
            'data'             => [],
            'error'            => $ultimo['error'] ?? 'Todos los proveedores del pool fallaron.',
            'http_status'      => $ultimo['http_status'] ?? 0,
            'failover_history' => $failoverHistory,
        ];
    }

    /**
     * Intenta un proveedor concreto probando su pool de modelos.
     *
     * @param array<string,mixed>             $provider   Definición del proveedor.
     * @param array<int,array<string,mixed>>  $messages   Historial de mensajes.
     * @param array<int,array<string,mixed>>  $tools      Definiciones de tools.
     * @param string|array|null               $toolChoice 'auto' | 'none' | array.
     * @param array<string,mixed>             $overrides  Parámetros extra.
     *
     * @return array<string,mixed> Contrato {success, data, error, model, ...}.
     */
    private function chatCompletionsConProveedor(array $provider, array $messages, array $tools, string|array|null $toolChoice, array $overrides): array
    {
        $pool = [];
        $primario = (string) ($provider['model'] ?? '');
        if ($primario !== '') {
            $pool[] = $primario;
        }
        foreach ((array) ($provider['models'] ?? []) as $m) {
            $m = (string) $m;
            if ($m !== '' && !in_array($m, $pool, true)) {
                $pool[] = $m;
            }
        }
        if ($pool === []) {
            $pool = ['default'];
        }

        $failover = [];
        $ctx = [
            'baseUrl'        => $provider['baseUrl'],
            'apiKey'         => $provider['apiKey'] ?? null,
            'connectTimeout' => $provider['connectTimeout'],
            'timeout'        => $provider['timeout'],
            'maxRetries'     => (int) ($provider['maxRetries'] ?? 0),
            'extraHeaders'   => $provider['extraHeaders'] ?? [],
        ];

        foreach ($pool as $modelo) {
            $payload = $this->buildPayload($modelo, $messages, $tools, $toolChoice, $overrides);
            if ($provider['name'] === 'ollama') {
                // Ollama descarga de RAM los modelos inactivos por defecto
                // (keep_alive ~5 min): la siguiente consulta paga recarga +
                // prefill. Se mantiene residente (30m por defecto) para que
                // las consultas sucesivas sean rápidas (10-25s en caliente).
                $payload['keep_alive'] = (string) (getenv('OLLAMA_KEEP_ALIVE') ?: '30m');
            }
            $res = $this->requestConCtx('POST', '/chat/completions', $payload, $ctx);

            if ($res['success']) {
                $res['data'] = $this->normalizarToolCalls($res['data']);
                $res['model'] = (string) $modelo;
                if ($failover !== []) {
                    $res['failover_history_models'] = $failover;
                }
                return $res;
            }

            $failover[] = [
                'model'       => (string) $modelo,
                'http_status' => (int) ($res['http_status'] ?? 0),
                'error'       => $res['error'],
            ];

            if (!$this->esFalloFailover($res)) {
                $res['model'] = (string) $modelo;
                $res['failover_history_models'] = $failover;
                return $res;
            }

            $this->consoleLog(sprintf(
                '[AraBrain] Modelo %s falló (HTTP %d). Rotando dentro de %s...',
                $modelo,
                (int) ($res['http_status'] ?? 0),
                $provider['label']
            ));
        }

        $ultimo = $failover[count($failover) - 1] ?? null;
        return [
            'success'     => false,
            'data'        => [],
            'error'       => $ultimo['error'] ?? 'Todos los modelos del proveedor fallaron.',
            'model'       => $ultimo['model'] ?? null,
            'http_status' => $ultimo['http_status'] ?? 0,
        ];
    }

    /**
     * Normaliza tool_calls para Ollama/qwen2.5-coder: algunas instalaciones de
     * Ollama no emiten 'message.tool_calls' nativo y el modelo devuelve el
     * JSON dentro de 'content' (finish 'stop'). Si no hay tool_calls
     * estructurados y el content comienza con ese patrón, se sintetiza el
     * tool_calls en formato OpenAI para que el motor de Tool Calling
     * (OpenCodeAgentEngine) lo ejecute.
     *
     * Formatos aceptados en content (directiva v4.12 de intent routing):
     *   A. {"name": "...", "arguments": {...}}                (legacy qwen)
     *   B. {"tool": "...", "parameters": {...}}               (matriz oficial)
     *   C. {"tool_name": "...", "arguments": {...}}           (variante)
     *
     * @param array<string,mixed> $data Respuesta completa del servidor.
     *
     * @return array<string,mixed>
     */
    private function normalizarToolCalls(array $data): array
    {
        $message = $data['choices'][0]['message'] ?? null;
        if (!is_array($message)) {
            return $data;
        }
        if (isset($message['tool_calls']) && is_array($message['tool_calls']) && $message['tool_calls'] !== []) {
            return $data;
        }
        $content = trim((string) ($message['content'] ?? ''));
        if ($content === '') {
            return $data;
        }
        $decoded = null;
        try {
            $decoded = json_decode($content, true, 512, JSON_THROW_ON_ERROR);
        } catch (\JsonException $e) {
            $decoded = null;
        }
        if (!is_array($decoded)) {
            return $data;
        }

        // Formato B/C: {"tool"|"tool_name": "...", "parameters"|"arguments": {...}}
        $nombre = (string) ($decoded['tool'] ?? $decoded['tool_name'] ?? '');
        $args = $decoded['parameters'] ?? $decoded['arguments'] ?? null;
        if ($nombre !== '' && is_array($args)) {
            try {
                $argsJson = json_encode($args, self::JSON_FLAGS);
            } catch (\JsonException $e) {
                $argsJson = '{}';
            }
            $data['choices'][0]['message']['tool_calls'] = [[
                'id'       => 'call_' . substr(md5($content), 0, 16),
                'type'     => 'function',
                'function' => [
                    'name'      => $nombre,
                    'arguments' => $argsJson, // string JSON, formato OpenAI
                ],
            ]];
            $this->consoleLog('[AraBrain] tool_calls normalizado desde content (matriz oficial tool/parameters).');
            return $data;
        }

        // Formato A (legacy): {"name": "...", "arguments": {...}}
        $patron = '/^\{\s*"name"\s*:\s*"([^"]+)"\s*,\s*"arguments"\s*:\s*(\{.*\})\s*\}$/s';
        if (preg_match($patron, $content, $m) !== 1) {
            return $data;
        }
        $data['choices'][0]['message']['tool_calls'] = [[
            'id'       => 'call_' . substr(md5($content), 0, 16),
            'type'     => 'function',
            'function' => [
                'name'      => $m[1],
                'arguments' => $m[2], // string JSON, formato OpenAI
            ],
        ]];
        $this->consoleLog('[AraBrain] tool_calls normalizado desde content (qwen/Ollama).');
        return $data;
    }

    /**
     * Compone el payload OpenAI v1 para un modelo concreto.
     *
     * @return array<string,mixed>
     */
    private function buildPayload(string $modelo, array $messages, array $tools, string|array|null $toolChoice, array $overrides): array
    {
        $payload = array_merge([
            'model'    => $modelo,
            'messages' => $messages,
            'stream'   => false,
        ], $overrides);
        $payload['model'] = $modelo;

        if ($tools !== []) {
            $payload['tools'] = $tools;
            $payload['tool_choice'] = $toolChoice ?? 'auto';
        }

        return $payload;
    }

    // ── CIRCUIT BREAKER DE SESIÓN ─────────────────────────────────────────────

    /**
     * Precarga el modelo local en RAM antes de la primera consulta (una sola
     * vez por instancia). Evita que la consulta real pague la recarga del
     * modelo tras el keep_alive y dispare el Fast-Fail de 25s:
     *  1. GET /api/ps: si el modelo ya está cargado (size > 0), no hace nada.
     *  2. POST /api/generate con prompt mínimo ('ping'): carga el modelo con
     *     un timeout amplio propio (OLLAMA_WARMUP_TIMEOUT_S, default 120s),
     *     SIN violar el Fast-Fail de 25s de las consultas reales.
     * Si el warm-up falla, se continúa igual: la consulta lo reintentará y, si
     * excede 25s, el Circuit Breaker conmuta a NVIDIA Cloud.
     *
     * @param list<array<string,mixed>> $providers Pool de proveedores.
     */
    private function warmupOllama(array $providers): void
    {
        if ($this->warmupHecho) {
            return;
        }
        $this->warmupHecho = true;

        $provider = null;
        foreach ($providers as $p) {
            if (($p['name'] ?? '') === 'ollama') {
                $provider = $p;
                break;
            }
        }
        if ($provider === null || self::isEndpointBlacklisted($provider['baseUrl'])) {
            return;
        }

        $modelo = (string) ($provider['model'] ?? self::OLLAMA_MODEL);
        $ctx = [
            'baseUrl'        => $provider['baseUrl'],
            'apiKey'         => null,
            'connectTimeout' => $provider['connectTimeout'],
            'timeout'        => (int) (getenv('OLLAMA_WARMUP_TIMEOUT_S') ?: 120),
            'maxRetries'     => 0,
            'extraHeaders'   => [],
        ];

        $ps = $this->doRequest('GET', $provider['baseUrl'] . '/api/ps', null, $ctx);
        if ($ps['success']) {
            foreach (($ps['data']['models'] ?? []) as $m) {
                if ((string) ($m['name'] ?? '') === $modelo && (int) ($m['size'] ?? 0) > 0) {
                    $this->consoleLog('[AraBrain] Modelo local ya cargado (' . $modelo . '). Sin warm-up.');
                    return;
                }
            }
        }

        $this->consoleLog('[AraBrain] Warm-up: precargando modelo local (' . $modelo . ')...');
        $gen = $this->doRequest('POST', $provider['baseUrl'] . '/api/generate', [
            'model'      => $modelo,
            'prompt'     => 'ping',
            'stream'     => false,
            'keep_alive' => (string) (getenv('OLLAMA_KEEP_ALIVE') ?: '30m'),
        ], $ctx);
        if ($gen['success']) {
            $this->consoleLog('[AraBrain] Warm-up completado (' . $modelo . ') en caliente.');
        } else {
            $this->consoleLog('[AraBrain] Warm-up falló (' . $gen['error'] . '). Se usará el fallback si la consulta no responde.');
        }
    }

    /**
     * True si la URL base está blacklisted para el resto de la sesión PHP.
     */
    public static function isEndpointBlacklisted(string $baseUrl): bool
    {
        return in_array(rtrim($baseUrl, '/'), self::$blacklistedEndpoints, true);
    }

    /**
     * Marca un endpoint (puerto 11434 caído, NVIDIA HTTP 529/503...) para no
     * volver a intentarlo en la misma sesión/ejecución.
     */
    public static function blacklistEndpoint(string $baseUrl, string $reason): void
    {
        $url = rtrim($baseUrl, '/');
        if (!in_array($url, self::$blacklistedEndpoints, true)) {
            self::$blacklistedEndpoints[] = $url;
            error_log('[AraBrain] Circuit Breaker: endpoint blacklisted (' . $url . '): ' . $reason);
        }
    }

    /** @return list<string> Endpoints blacklisted en la sesión. */
    public static function getBlacklistedEndpoints(): array
    {
        return self::$blacklistedEndpoints;
    }

    // ── DIAGNÓSTICO DEL POOL ──────────────────────────────────────────────────

    /**
     * Pool de proveedores resuelto (sin exponer API keys).
     *
     * @return list<array<string,mixed>>
     */
    public function getProviders(): array
    {
        $out = [];
        foreach ($this->providers as $p) {
            $out[] = [
                'name'           => $p['name'],
                'label'          => $p['label'],
                'baseUrl'        => $p['baseUrl'],
                'model'          => $p['model'],
                'models'         => $p['models'],
                'connectTimeout' => $p['connectTimeout'],
                'timeout'        => $p['timeout'],
                'blacklisted'    => self::isEndpointBlacklisted($p['baseUrl']),
            ];
        }
        return $out;
    }

    /** @return string|null Proveedor ('ollama'|'nvidia'|'legacy') que respondió. */
    public function getActiveProvider(): ?string
    {
        return $this->activeProvider;
    }

    /**
     * Log de consola siempre visible (STDERR, no contamina el stdout JSON de
     * scripts CLI) + error_log() si el debug está activo.
     */
    private function consoleLog(string $message): void
    {
        if (defined('STDERR')) {
            fwrite(STDERR, $message . PHP_EOL);
        }
        if ($this->debug) {
            error_log($message);
        }
    }

    /**
     * True si el fallo justifica rotar de modelo: saturación/rate limit
     * (HTTP 408, 429, 502, 503, 529, 5xx), timeout cURL (errno 28) o modelo
     * deprecado/inexistente en el catálogo (HTTP 410/404: saltar al siguiente
     * modelo del pool en vez de abortar el proveedor completo).
     *
     * @param array{http_status?: int, transient?: bool} $res
     */
    private function esFalloFailover(array $res): bool
    {
        if (!($res['transient'] ?? false)) {
            return in_array((int) ($res['http_status'] ?? 0), [404, 410], true);
        }
        $status = (int) ($res['http_status'] ?? 0);
        return in_array($status, [408, 429, 502, 503, 529, 28], true) || ($status >= 500 && $status <= 599);
    }

    /**
     * Punto único de peticiones HTTP (modo legacy) con reintento controlado y
     * contrato {success, data, error}. NUNCA lanza excepciones hacia el llamador.
     *
     * Política de reintento:
     *  - Solo se reintenta ante errores transitorios idempotentes:
     *    HTTP 408, 429, 502, 503, 529 y cualquier 5xx, o cURL errno 28
     *    (timeout) / 7, 35, 55, 56 (red).
     *  - Retardo exponencial: 2s, 4s, 8s (intento 1, 2 y 3).
     *
     * @param string                   $method Método HTTP (GET/POST/...).
     * @param string                   $path   Ruta relativa.
     * @param array<string,mixed>|null $body   Body JSON (null si no aplica).
     *
     * @return array{success: bool, data: array, error: ?string}
     */
    public function request(string $method, string $path, ?array $body = null): array
    {
        return $this->requestConCtx($method, $path, $body, [
            'baseUrl'        => $this->baseUrl,
            'apiKey'         => $this->apiKey,
            'connectTimeout' => $this->connectTimeout,
            'timeout'        => $this->timeoutS,
            'maxRetries'     => $this->maxRetries,
            'extraHeaders'   => $this->extraHeaders,
        ]);
    }

    /**
     * Petición HTTP contra un contexto (proveedor o legacy) con reintento
     * controlado. NUNCA lanza excepciones hacia el llamador.
     *
     * @param array<string,mixed> $ctx {baseUrl, apiKey, connectTimeout, timeout,
     *                                 maxRetries, extraHeaders}.
     *
     * @return array{success: bool, data: array, error: ?string}
     */
    private function requestConCtx(string $method, string $path, ?array $body, array $ctx): array
    {
        $url = rtrim($ctx['baseUrl'], '/') . '/' . self::OPENAI_API_VERSION . '/' . ltrim($path, '/');
        $method = strtoupper($method);
        $maxRetries = (int) ($ctx['maxRetries'] ?? 0);

        $lastError = null;

        for ($attempt = 0; $attempt <= $maxRetries; ++$attempt) {
            $resultado = $this->doRequest($method, $url, $body, $ctx);

            if ($resultado['success']) {
                return $resultado;
            }

            $lastError = $resultado['error'];
            // Solo se reintenta ante errores transitorios idempotentes.
            if (!$this->isTransientError($resultado)) {
                return $resultado;
            }
            if ($attempt >= $maxRetries) {
                return $resultado;
            }

            // Backoff exponencial: Intento 1 → 2s, Intento 2 → 4s, Intento 3 → 8s.
            $retryDelayS = (int) pow(2, $attempt + 1);
            $codigo = (int) ($resultado['http_status'] ?? 0);
            error_log(sprintf(
                '[NvidiaBrainClient] Reintento %d/%d de %s (código %d). Esperando %ds...',
                $attempt + 1,
                $maxRetries,
                $url,
                $codigo,
                $retryDelayS
            ));
            sleep($retryDelayS);
        }

        return [
            'success' => false,
            'data'    => [],
            'error'   => $lastError ?? 'Petición fallida sin detalle.',
        ];
    }

    /**
     * Ejecuta una llamada cURL real y normaliza el resultado.
     *
     * @param array<string,mixed> $ctx {apiKey, connectTimeout, timeout,
     *                                 extraHeaders}.
     *
     * @return array{success: bool, data: array, error: ?string, http_status: int, transient: bool}
     */
    private function doRequest(string $method, string $url, ?array $body, array $ctx): array
    {
        $apiKey = $ctx['apiKey'] ?? null;
        $connectTimeout = (int) ($ctx['connectTimeout'] ?? $this->connectTimeout);
        $timeoutS = (int) ($ctx['timeout'] ?? $this->timeoutS);
        $extraHeaders = $ctx['extraHeaders'] ?? [];

        $fallo = function (string $msg, int $status = 0, bool $transient = false) use ($url): array {
            if ($this->debug) {
                error_log(sprintf('[NvidiaBrainClient] Error (%d): %s', $status, $msg));
            }
            return [
                'success'    => false,
                'data'       => [],
                'error'      => $msg,
                'http_status'=> $status,
                'transient'  => $transient,
            ];
        };

        if (!function_exists('curl_init')) {
            return $fallo('La extensión PHP cURL no está disponible en este servidor.', 0, false);
        }

        $ch = curl_init();
        if ($ch === false) {
            return $fallo('curl_init() falló.', CURLE_FAILED_INIT, false);
        }

        try {
            $headers = [
                'Accept: application/json',
                'Content-Type: application/json',
                // Evita la cabecera "Expect: 100-continue" que cuelga a vLLM
                // con bodies grandes.
                'Expect:',
            ];
            if ($apiKey !== null && $apiKey !== '') {
                $headers[] = 'Authorization: Bearer ' . $apiKey;
            }
            foreach ($extraHeaders as $name => $value) {
                $headers[] = $name . ': ' . $value;
            }

            $jsonBody = null;
            if ($body !== null) {
                try {
                    $jsonBody = json_encode($body, self::JSON_FLAGS);
                } catch (\JsonException $e) {
                    return $fallo('Body no serializable a JSON: ' . $e->getMessage(), 0, false);
                }
            }

            curl_setopt_array($ch, [
                CURLOPT_URL            => $url,
                CURLOPT_CUSTOMREQUEST  => $method,
                CURLOPT_HTTPHEADER     => $headers,
                CURLOPT_RETURNTRANSFER => true,
                CURLOPT_CONNECTTIMEOUT => $connectTimeout,
                CURLOPT_TIMEOUT        => $timeoutS,
                CURLOPT_FOLLOWLOCATION => true,
                CURLOPT_MAXREDIRS      => 3,
                CURLOPT_ENCODING       => '',
                CURLOPT_SSL_VERIFYPEER => true,
                CURLOPT_SSL_VERIFYHOST => 2,
            ]);

            if ($jsonBody !== null) {
                curl_setopt($ch, CURLOPT_POSTFIELDS, $jsonBody);
            }

            $raw = curl_exec($ch);
            $errno = curl_errno($ch);
            $error = curl_error($ch);
            $status = (int) curl_getinfo($ch, CURLINFO_RESPONSE_CODE);

            if ($raw === false) {
                // Errores transitorios típicos: timeout (28) y conn reset (56).
                $transient = in_array($errno, [7, 28, 35, 55, 56], true);
                return $fallo(sprintf('Error cURL (%d): %s', $errno, $error), $errno, $transient);
            }

            if ($status < 200 || $status >= 300) {
                $detail = $this->extractErrorDetail((string) $raw);
                // 410 = modelo deprecado / EOL en el catálogo NVIDIA NIM (caso
                // histórico: deepseek-ai/deepseek-v4-flash, EOL 2026-08-07);
                // 404 = modelo no habilitado o ruta inexistente.
                if (in_array($status, [410, 404], true)) {
                    error_log(sprintf(
                        '[NVIDIA_CLOUD_SYNC] ⚠️ Endpoint cloud deprecado (HTTP %d). Verifique el modelo configurado: %s',
                        $status,
                        $detail
                    ));
                }
                // 408/429 = rate limit, 529 = service temporarily overloaded
                // (NVIDIA NIM), cualquier 5xx = error transitorio del servidor.
                $transient = in_array($status, [408, 429, 529], true) || ($status >= 500 && $status <= 599);
                return $fallo(sprintf('HTTP %d al llamar a %s: %s', $status, $url, $detail), $status, $transient);
            }

            if ($raw === '') {
                // 200 sin body: válido solo si no esperábamos payload.
                return [
                    'success'    => true,
                    'data'       => [],
                    'error'      => null,
                    'http_status'=> $status,
                    'transient'  => false,
                ];
            }

            try {
                $decoded = json_decode($raw, true, 512, self::JSON_FLAGS);
            } catch (\JsonException $e) {
                return $fallo('Respuesta no-JSON del servidor IA: ' . $e->getMessage(), $status, false);
            }

            if (!is_array($decoded)) {
                return $fallo('Respuesta JSON con forma inesperada (no objeto).', $status, false);
            }

            return [
                'success'    => true,
                'data'       => $decoded,
                'error'      => null,
                'http_status'=> $status,
                'transient'  => false,
            ];
        } finally {
            curl_close($ch);
        }
    }

    /**
     * Determina si un resultado es candidato a reintento.
     *
     * @param array{http_status?: int, transient?: bool} $resultado
     */
    private function isTransientError(array $resultado): bool
    {
        return (bool) ($resultado['transient'] ?? false);
    }

    /**
     * Extrae un detalle legible del error HTTP (JSON {error:{message}} o HTML).
     */
    private function extractErrorDetail(string $raw): string
    {
        $decoded = json_decode($raw, true);
        if (is_array($decoded)) {
            if (isset($decoded['error']['message'])) {
                return (string) $decoded['error']['message'];
            }
            if (isset($decoded['message'])) {
                return (string) $decoded['message'];
            }
            if (isset($decoded['detail'])) {
                return (string) $decoded['detail'];
            }
            return substr($raw, 0, 300);
        }
        $plain = trim(strip_tags($raw));
        return $plain !== '' ? substr($plain, 0, 300) : substr($raw, 0, 300);
    }

    /**
     * Consulta la lista de modelos disponibles del servidor (GET /v1/models).
     *
     * @return array{success: bool, data: array, error: ?string}
     */
    public function listModels(): array
    {
        return $this->request('GET', '/models');
    }

    /**
     * Pool de modelos candidatos: env NVIDIA_BRAIN_MODELS (CSV) o fallback a
     * self::DEFAULT_MODEL_POOL. Los nombres se recortan y se descartan vacíos.
     *
     * @return list<string>
     */
    public function modelPool(): array
    {
        $raw = getenv('NVIDIA_BRAIN_MODELS');
        if (!is_string($raw) || trim($raw) === '') {
            return self::DEFAULT_MODEL_POOL;
        }
        $modelos = array_values(array_filter(array_map(
            static fn (string $m): string => trim($m),
            explode(',', $raw)
        ), static fn (string $m): bool => $m !== ''));
        return $modelos !== [] ? $modelos : self::DEFAULT_MODEL_POOL;
    }

    /** @return string URL base configurada (diagnóstico). */
    public function getBaseUrl(): string
    {
        return $this->baseUrl;
    }

    /** @return string|null Modelo primario configurado en el constructor. */
    public function getPrimaryModel(): ?string
    {
        return $this->model;
    }
}
