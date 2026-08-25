<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Adapters;

use App\Services\NvidiaBrain\NvidiaBrainClient;
use App\Services\NvidiaBrain\ToolRegistry;
use PDO;
use PDOException;

/**
 * Clase base de los adaptadores departamentales de NVIDIA BRAIN.
 *
 * Cada adaptador (Almacen, Finanzas, Tecnologia, WhatsApp) extiende esta
 * clase y define:
 *   - getSystemPrompt(): el System Prompt inyectado al modelo.
 *   - procesar($mensaje, $contexto): el flujo departamental de la petición.
 *
 * Conexión al LLM (regla de proyecto):
 *   - Endpoint: http://localhost:11434/v1/chat/completions (Ollama).
 *   - Modelo:   qwen2.5-coder:3b.
 *   - Fast-Fail estricto: CURLOPT_CONNECTTIMEOUT = 3s, CURLOPT_TIMEOUT = 25s
 *     (implementado por NvidiaBrainClient en modo endpoint único, sin
 *     failover a cloud: el adaptador trabaja SIEMPRE contra Ollama local).
 *
 * sanitizar_utf8($datos): recursivo sobre arrays/strings (CP1252 → UTF-8)
 * para que json_encode() jamás falle con datos de SQL Server/Profit Plus.
 *
 * Contrato de retorno de procesar() (SIEMPRE JSON serializable):
 *   ['success' => bool, 'respuesta' => string, 'error' => ?string,
 *    'data' => array]
 * La 'respuesta' NUNCA es vacía ni "No sé": ante falta de datos se listan
 * los parámetros requeridos para procesar la acción.
 */
abstract class BaseAdapter
{
    /** URL base de Ollama local (sin '/v1'; el cliente lo añade). */
    public const OLLAMA_URL = 'http://localhost:11434';

    /** Modelo local obligatorio del módulo. */
    public const OLLAMA_MODEL = 'qwen2.5-coder:3b';

    /** CURLOPT_CONNECTTIMEOUT: Fast-Fail de conexión (3s). */
    public const CONNECT_TIMEOUT_S = 3;

    /** CURLOPT_TIMEOUT: Fast-Fail de respuesta (25s). */
    public const TIMEOUT_S = 25;

    /**
     * Reglas de oro ANTI-FLUFF, antepuestas SIEMPRE al System Prompt de cada
     * subclase (preguntar() las antepone). qwen2.5-coder:3b tiende al relleno
     * conversacional ("Claro", "Hola", explicaciones); este bloque restringe
     * el comportamiento a respuestas operativas directas.
     */
    public const SYSTEM_PROMPT_ANTI_FLUFF = <<<'TXT'
REGLAS DE ORO ANTI-FLUFF (OBLIGATORIAS, NO LAS VIOLES):
- Sin introducciones ni saludos robóticos ("Hola", "Claro", "Con gusto", "Por supuesto").
- Sin ofrecer código ni explicaciones conversacionales: responde solo el resultado operativo.
- Para invocar herramientas responde el JSON directo de la tool call, sin texto envolvente.
- Máximo 3 oraciones o viñetas directas en respuestas al usuario.
TXT;

    protected NvidiaBrainClient $client;

    protected ?ToolRegistry $registry;

    /** @var array<string,mixed> Contexto de sesión (usuario, rol, módulo). */
    protected array $contexto;

    /**
     * @param NvidiaBrainClient|null $client   Cliente inyectable (tests o
     *                                         configuración avanzada); por
     *                                         defecto Ollama local con
     *                                         Fast-Fail 3s/25s.
     * @param ToolRegistry|null      $registry Registro de herramientas del
     *                                         departamento (opcional).
     * @param array<string,mixed>    $contexto Contexto de sesión inicial.
     */
    public function __construct(
        ?NvidiaBrainClient $client = null,
        ?ToolRegistry $registry = null,
        array $contexto = []
    ) {
        $this->client = $client ?? $this->crearClienteOllama();
        $this->registry = $registry;
        $this->contexto = $contexto;
    }

    /**
     * System Prompt del departamento (inyectado como mensaje 'system').
     */
    abstract protected function getSystemPrompt(): string;

    /**
     * Procesa la petición del departamento y retorna la respuesta JSON.
     *
     * @param string               $mensaje  Petición del usuario/operador.
     * @param array<string,mixed>  $contexto Contexto de la petición
     *                                       (identificador, params, archivos...).
     *
     * @return array<string,mixed> {success, respuesta, error, data}.
     */
    abstract public function procesar(string $mensaje, array $contexto = []): array;

    /**
     * Cliente cURL pinneado a Ollama local con Fast-Fail estricto.
     *
     * Modo legacy (endpoint único) de NvidiaBrainClient: URL exacta
     * http://localhost:11434/v1/chat/completions, modelo qwen2.5-coder:3b,
     * CURLOPT_CONNECTTIMEOUT=3, CURLOPT_TIMEOUT=25, sin reintentos (el
     * fallo se reporta de inmediato; nunca congela la aplicación).
     */
    protected function crearClienteOllama(): NvidiaBrainClient
    {
        return new NvidiaBrainClient(
            baseUrl: self::OLLAMA_URL,
            apiKey: null,
            model: self::OLLAMA_MODEL,
            timeoutS: self::TIMEOUT_S,
            connectTimeout: self::CONNECT_TIMEOUT_S,
            maxRetries: 0
        );
    }

    /**
     * Pide al modelo una respuesta para el departamento con el System Prompt
     * de la subclase.
     *
     * @param array<int,array<string,mixed>> $tools Definiciones de tools.
     *
     * @return array<string,mixed> {success, respuesta, error, data, provider}.
     */
    protected function preguntar(
        string $prompt,
        array $tools = [],
        string|array|null $toolChoice = 'auto',
        array $overrides = []
    ): array {
        // Blindaje del modelo (regla de proyecto): el adaptador trabaja SIEMPRE
        // contra Ollama local con qwen2.5-coder:3b. Si el cliente inyectado
        // (tests/configuración avanzada) trae otro modelo primario, se
        // reemplaza por el cliente pinneado del adaptador.
        $modeloActual = $this->client->getPrimaryModel();
        if ($modeloActual === null || $modeloActual !== self::OLLAMA_MODEL) {
            $this->client = $this->crearClienteOllama();
        }

        // ANTI-FLUFF: el bloque de reglas de oro se antepone SIEMPRE al System
        // Prompt departamental de la subclase.
        $systemPrompt = self::SYSTEM_PROMPT_ANTI_FLUFF . PHP_EOL . PHP_EOL . $this->getSystemPrompt();

        $messages = [
            ['role' => 'system', 'content' => $systemPrompt],
            ['role' => 'user', 'content' => $prompt],
        ];

        // Blindaje determinista del payload (regla de proyecto): temperature
        // 0.1 y max_tokens 300 se FORZAN siempre en el JSON enviado a
        // /v1/chat/completions, pisando cualquier override de subclase. Sin
        // temperatura el modelo divaga; sin tope de tokens el relleno
        // conversacional se dispara.
        $overrides = array_merge($overrides, [
            'temperature' => 0.1,
            'max_tokens'  => 300,
        ]);

        $res = $this->client->chatCompletions($messages, $tools, $toolChoice, $overrides);
        if (!$res['success']) {
            return [
                'success'  => false,
                'respuesta'=> '',
                'error'    => $res['error'] ?? 'El servidor IA no respondió.',
                'data'     => ['provider' => $res['provider'] ?? null],
            ];
        }

        $contenido = $res['data']['choices'][0]['message']['content'] ?? null;
        if (is_array($contenido)) {
            $piezas = [];
            foreach ($contenido as $parte) {
                if (is_array($parte) && isset($parte['text'])) {
                    $piezas[] = (string) $parte['text'];
                }
            }
            $contenido = implode('', $piezas);
        }
        $respuesta = trim((string) $contenido);

        return [
            'success'   => $respuesta !== '',
            'respuesta' => $respuesta,
            'error'     => $respuesta === '' ? 'El modelo devolvió una respuesta vacía.' : null,
            'data'      => [
                'provider' => $res['provider'] ?? null,
                'model'    => $res['model'] ?? null,
            ],
        ];
    }

    /**
     * Normaliza una respuesta final del adaptador: NUNCA vacía. Si la
     * respuesta IA está vacía, se usa $fallback o se listan los parámetros
     * requeridos (criterio de aceptación: sin respuestas "No sé").
     *
     * @param array<string,mixed> $data
     */
    protected function responder(string $respuesta, array $data = [], ?string $error = null, string $fallback = ''): array
    {
        $final = trim($respuesta);
        if ($final === '') {
            $final = $fallback !== '' ? $fallback : 'No fue posible completar la solicitud.';
        }
        return [
            'success'   => $error === null,
            'respuesta' => $final,
            'error'     => $error,
            'data'      => self::sanitizar_utf8($data),
        ];
    }

    /**
     * Lista los parámetros requeridos ausentes de una petición.
     *
     * @param list<string>          $requeridos Parámetros obligatorios.
     * @param array<string,mixed>   $datos      Datos recibidos.
     *
     * @return list<string> Parámetros faltantes ([] si no falta ninguno).
     */
    protected function parametrosFaltantes(array $requeridos, array $datos): array
    {
        $faltantes = [];
        foreach ($requeridos as $param) {
            $valor = $datos[$param] ?? null;
            if ($valor === null || (is_string($valor) && trim($valor) === '')) {
                $faltantes[] = $param;
            }
        }
        return $faltantes;
    }

    /**
     * Mensaje amigable de parámetros requeridos (criterio de aceptación 2).
     *
     * @param list<string> $faltantes
     */
    protected function respuestaFaltanParametros(array $faltantes, string $accion): array
    {
        return [
            'success'   => false,
            'respuesta' => sprintf(
                'Para %s se requieren los siguientes datos: %s. Envíelos completos para procesar la solicitud.',
                $accion,
                implode(', ', array_map(static fn (string $p): string => '"' . $p . '"', $faltantes))
            ),
            'error'     => 'Parámetros requeridos ausentes: ' . implode(', ', $faltantes),
            'data'      => ['parametros_requeridos' => $faltantes],
        ];
    }

    /**
     * Sanitiza recursivamente datos mixtos a UTF-8 válido.
     *
     * Regla de proyecto: sanitizar TODAS las entradas/salidas desde SQL
     * Server o Profit Plus para prevenir fallos de json_encode() por
     * caracteres CP1252/Windows-1252. Recorre arrays, corregye strings y
     * devuelve el resto intacto.
     *
     * @param mixed $datos
     *
     * @return mixed
     */
    public static function sanitizar_utf8(mixed $datos): mixed
    {
        if (is_string($datos)) {
            return self::aUtf8($datos);
        }
        if (is_array($datos)) {
            $resultado = [];
            foreach ($datos as $clave => $valor) {
                $resultado[self::aUtf8((string) $clave)] = self::sanitizar_utf8($valor);
            }
            return $resultado;
        }
        return $datos;
    }

    /**
     * Abre la conexión PDO a SQL Server (sqlsrv si está disponible, si no
     * ODBC). Configuración: PROFIT_SQL_* → PROFIT_DB_* → defaults
     * (192.168.4.20:1433, profit/profit, PRUEB25, driver "SQL Server").
     *
     * @throws PDOException Si la conexión falla (el llamador decide cómo
     *                      responder).
     */
    protected function conectar(): PDO
    {
        $host = self::env('PROFIT_SQL_HOST', self::env('PROFIT_DB_HOST', '192.168.4.20'));
        $port = self::env('PROFIT_SQL_PORT', self::env('PROFIT_DB_PORT', '1433'));
        $user = self::env('PROFIT_SQL_USER', self::env('PROFIT_DB_USER', 'profit'));
        $pass = self::env('PROFIT_SQL_PASS', self::env('PROFIT_DB_PASS', 'profit'));
        $name = self::env('PROFIT_SQL_NAME', self::env('PROFIT_DB_NAME', 'PRUEB25'));

        $drivers = PDO::getAvailableDrivers();
        if (in_array('sqlsrv', $drivers, true)) {
            $dsn = 'sqlsrv:Server=' . $host . ',' . $port . ';Database=' . $name;
        } else {
            $driver = self::env('PROFIT_SQL_DRIVER', self::env('PROFIT_DB_DRIVER', 'SQL Server'));
            $dsn = 'odbc:Driver={' . $driver . '};Server=' . $host . ',' . $port . ';Database=' . $name;
        }

        $opts = [
            PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_TIMEOUT            => 8,
        ];
        if (defined('PDO::SQLSRV_ATTR_QUERY_TIMEOUT')) {
            $opts[PDO::SQLSRV_ATTR_QUERY_TIMEOUT] = 8;
        }
        return new PDO($dsn, $user, $pass, $opts);
    }

    /**
     * Normaliza texto del ERP a UTF-8 (CP1252/Latin-1 → UTF-8 válido).
     */
    protected static function aUtf8(string $texto): string
    {
        $limpio = trim($texto);
        if (mb_check_encoding($limpio, 'UTF-8')) {
            return $limpio;
        }
        $convertido = mb_convert_encoding($limpio, 'UTF-8', 'CP1252');
        return is_string($convertido) ? $convertido : $limpio;
    }

    /**
     * Lee una variable de entorno con fallback y tolera valores vacíos.
     */
    private static function env(string $clave, string $default): string
    {
        $valor = getenv($clave);
        if (is_string($valor) && trim($valor) !== '') {
            return trim($valor);
        }
        return $default;
    }
}
