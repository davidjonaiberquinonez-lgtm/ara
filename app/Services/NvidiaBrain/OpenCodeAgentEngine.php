<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain;

use App\Services\NvidiaBrain\Contracts\IaLoggerInterface;

/**
 * Motor orquestador agéntico "NVIDIA BRAIN".
 *
 * Ejecuta el bucle autónomo de Tool Calling:
 *
 *   usuario → [system + historial] → LLM
 *      │                              │
 *      │          si tool_calls       ▼
 *      └───── executeTool(ToolRegistry) ─► mensaje tool → nuevo turno
 *
 * Características:
 *  - Bucle `while` con límite MÁXIMO de 5 iteraciones por petición
 *    (evita bucles infinitos y consumo excesivo del servidor local).
 *  - Deduplicación: si una herramienta se intenta ejecutar 2 veces
 *    CONSECUTIVAS con los MISMOS parámetros exactos, se intercepta el bucle
 *    y se inyecta un mensaje de advertencia al modelo.
 *  - Auditoría: registra la métrica final (tiempo MS, iteraciones, estado) en
 *    la tabla IA_Logs de SQL Server vía IaLoggerInterface (best-effort).
 *  - Manejo defensivo: los fallos de herramienta y del LLM se convierten en
 *    mensajes del historial para que el modelo reintente; nunca revientan el
 *    request del controlador.
 *
 * Retorno (formato estándar PARTE 3):
 *   [
 *     'success'      => bool,
 *     'respuesta'    => string,
 *     'error'        => ?string,
 *     'iteraciones'  => int,
 *     'max_agotado'  => bool,
 *     'tiempo_ms'    => int,
 *     'status'       => 'OK' | 'ERROR' | 'MAX_ITERACIONES',
 *     'tool_calls'   => array,
 *     'log_id'       => ?int,   // (opcional, si el logger lo devuelve)
 *   ]
 */
final class OpenCodeAgentEngine
{
    /** Límite máximo de iteraciones por petición. */
    public const MAX_ITERACIONES = 5;

    /** @var NvidiaBrainClient Cliente HTTP del servidor IA local. */
    private NvidiaBrainClient $client;

    /** @var ToolRegistry Registro de herramientas con permisos. */
    private ToolRegistry $registry;

    /** @var IaLoggerInterface|null Persistencia de auditoría (IA_Logs). */
    private ?IaLoggerInterface $logger;

    /** @var bool Logs de depuración del bucle. */
    private bool $debug;

    /**
     * @param NvidiaBrainClient      $client   Cliente HTTP (vLLM/Ollama).
     * @param ToolRegistry           $registry Registro de herramientas.
     * @param IaLoggerInterface|null $logger   Auditoría IA_Logs (opcional).
     * @param bool                   $debug    Loguear pasos del bucle.
     */
    public function __construct(
        NvidiaBrainClient $client,
        ToolRegistry $registry,
        ?IaLoggerInterface $logger = null,
        bool $debug = false
    ) {
        $this->client = $client;
        $this->registry = $registry;
        $this->logger = $logger;
        $this->debug = $debug;
    }

    /**
     * Ejecuta el bucle completo del agente.
     *
     * @param string                   $prompt         Pregunta/instrucción del usuario.
     * @param string                   $modulo         Módulo activo.
     * @param array<string,mixed>      $contexto       Contexto de sesión:
     *                                                 usuario_id, nombre_usuario,
     *                                                 rol, ruta_id, tool_call_id
     *                                                 (se inyecta a las herramientas).
     * @param array<string,mixed>      $overrides      Parámetros del servidor
     *                                                 (model, temperature, max_tokens).
     * @param array<int,array<string,mixed>> $history  Historial previo.
     * @param int                      $conversacionId ID de IA_Conversaciones (auditoría).
     *
     * @return array<string,mixed> Respuesta estandarizada (formato PARTE 3).
     */
    public function run(
        string $prompt,
        string $modulo = 'General',
        array $contexto = [],
        array $overrides = [],
        array $history = [],
        ?int $conversacionId = null
    ): array {
        $inicioMs = hrtime(true);
        $systemPrompt = OpenCodeRules::getAgentSystemPrompt($modulo, $contexto);

        // Historial: system + (previo) + user.
        $messages = [['role' => 'system', 'content' => $systemPrompt]];
        foreach ($history as $msg) {
            if (isset($msg['role'], $msg['content'])) {
                $messages[] = $msg;
            }
        }
        $messages[] = ['role' => 'user', 'content' => $prompt];

        $tools = $this->registry->getDefinitions();
        $toolCallsEjecutadas = [];
        $ultimoError = null;
        $maxAgotado = false;
        $dedupAnterior = null;   // firma del turno anterior (name+args)
        $dedupConsecutivo = 0;   // veces consecutivas con la misma firma
        $raw = [];

        $iteracion = 0;
        while ($iteracion < self::MAX_ITERACIONES) {
            ++$iteracion;
            $this->log(sprintf('Iteración %d/%d — %d mensajes.', $iteracion, self::MAX_ITERACIONES, count($messages)));

            // ── 1) Llamada al LLM ──────────────────────────────────────────────
            $resp = $this->client->chatCompletions($messages, $tools, 'auto', $overrides);
            if (!$resp['success']) {
                $ultimoError = $resp['error'];
                $this->log('Error LLM: ' . (string) $ultimoError);
                // Con presupuesto, un mensaje del usuario con el error permite
                // que el modelo reintente; si no, cerramos.
                if ($iteracion >= self::MAX_ITERACIONES) {
                    $maxAgotado = true;
                    break;
                }
                $messages[] = [
                    'role'    => 'user',
                    'content' => sprintf(
                        'Hubo un error al comunicarme con el servidor de IA. Reintenta la tarea o responde con lo que tengas. Detalle técnico: %s',
                        $ultimoError
                    ),
                ];
                continue;
            }
            $raw = $resp['data'];

            // ── 2) Extracción del mensaje assistant ────────────────────────────
            $choice = $raw['choices'][0] ?? null;
            if (!is_array($choice)) {
                $ultimoError = 'El servidor IA no devolvió "choices".';
                $maxAgotado = true;
                break;
            }
            $assistantMessage = $choice['message'] ?? [];
            if (!is_array($assistantMessage)) {
                $assistantMessage = [];
            }

            $assistant = [
                'role'    => 'assistant',
                'content' => is_string($assistantMessage['content'] ?? null)
                    ? $assistantMessage['content']
                    : null,
            ];
            if (isset($assistantMessage['tool_calls']) && is_array($assistantMessage['tool_calls'])) {
                $assistant['tool_calls'] = $assistantMessage['tool_calls'];
            }
            $messages[] = $assistant;

            $toolCalls = $assistantMessage['tool_calls'] ?? null;

            // ── 3) Sin herramientas → respuesta final ──────────────────────────
            if (!is_array($toolCalls) || $toolCalls === []) {
                $this->log('Sin tool_calls: respuesta final.');
                $contenido = $assistant['content'];
                $respuesta = is_string($contenido) ? $contenido : '(El asistente no generó contenido.)';
                return $this->finalizar($inicioMs, $iteracion, $respuesta, null, false, $toolCallsEjecutadas, $conversacionId, $modulo, $contexto);
            }

            // ── 4) Deduplicación: 2 ejecuciones consecutivas idénticas ─────────
            $firma = $this->firmaTurno($toolCalls);
            if ($firma !== null && $firma === $dedupAnterior) {
                ++$dedupConsecutivo;
                if ($dedupConsecutivo >= 2) {
                    $this->log('Deduplicación detectada: mismas tool_calls 2 veces consecutivas.');
                    // Intercepta el bucle: inyecta advertencia al modelo.
                    $messages[] = [
                        'role'    => 'user',
                        'content' => '⚠️ ADVERTENCIA: Estás repitiendo exactamente la misma llamada a herramienta '
                                   . 'con los mismos parámetros. Detén la repetición: analiza la respuesta de la '
                                   . 'herramienta y responde al usuario, o cambia los parámetros de forma razonada.',
                    ];
                    $dedupConsecutivo = 0;
                    $dedupAnterior = null;
                    continue;
                }
            } else {
                $dedupConsecutivo = 0;
            }
            $dedupAnterior = $firma;

            // ── 5) Ejecutar cada tool_call de forma segura ─────────────────────
            foreach ($toolCalls as $tc) {
                $call = $this->normalizarToolCall($tc);
                $toolCallsEjecutadas[] = $call;
                $ctxTool = $contexto;
                $ctxTool['tool_call_id'] = $call['id'];
                $messages[] = $this->registry->executeTool($call['name'], $call['arguments'], $ctxTool);
            }
        }

        // ── 6) Límite alcanzado: cierre controlado ─────────────────────────────
        $maxAgotado = true;
        $this->log('Límite de iteraciones alcanzado; forzando cierre.');

        // Última oportunidad: pedir al modelo un resumen de cierre.
        $cierre = null;
        try {
            $messages[] = ['role' => 'system', 'content' => OpenCodeRules::closureDirective()];
            $respCierre = $this->client->chatCompletions($messages, [], 'none', $overrides);
            if ($respCierre['success']) {
                $choice = $respCierre['data']['choices'][0] ?? null;
                $contenido = is_array($choice) && isset($choice['message']['content']) && is_string($choice['message']['content'])
                    ? $choice['message']['content']
                    : '';
                if ($contenido !== '') {
                    $cierre = $contenido;
                }
            } else {
                $ultimoError = $respCierre['error'];
            }
        } catch (\Throwable $e) {
            $ultimoError = $e->getMessage();
        }

        $respuesta = $cierre ?? sprintf(
            'No pude completar la consulta tras %d iteraciones.%s Reformula la pregunta o verifica la conexión con el servidor de IA.',
            self::MAX_ITERACIONES,
            $ultimoError !== null ? ' (Detalle: ' . $ultimoError . ')' : ''
        );

        return $this->finalizar($inicioMs, $iteracion, $respuesta, $ultimoError, true, $toolCallsEjecutadas, $conversacionId, $modulo, $contexto);
    }

    /**
     * Compone la respuesta estandarizada y registra la métrica en IA_Logs.
     *
     * @return array<string,mixed>
     */
    private function finalizar(
        int $inicioHrtimeNs,
        int $iteraciones,
        string $respuesta,
        ?string $error,
        bool $maxAgotado,
        array $toolCalls,
        ?int $conversacionId,
        string $modulo,
        array $contexto
    ): array {
        $tiempoMs = (int) round((hrtime(true) - $inicioHrtimeNs) / 1_000_000);
        $status = $error !== null ? 'ERROR' : ($maxAgotado ? 'MAX_ITERACIONES' : 'OK');

        // Auditoría best-effort en IA_Logs.
        if ($this->logger !== null) {
            $herramienta = $toolCalls === [] ? null : (string) $toolCalls[count($toolCalls) - 1]['name'];
            $this->logger->log([
                'id_conversacion'     => $conversacionId,
                'id_usuario'          => $contexto['usuario_id'] ?? 0,
                'modulo'              => $modulo,
                'herramienta_ejecutada' => $herramienta,
                'tiempo_ms'           => $tiempoMs,
                'iteraciones_usuadas' => $iteraciones,
                'status_code'         => $status,
                'error_detalle'       => $error,
            ]);
        }

        return [
            'success'     => $error === null,
            'respuesta'   => $respuesta,
            'error'       => $error,
            'iteraciones' => $iteraciones,
            'max_agotado' => $maxAgotado,
            'tiempo_ms'   => $tiempoMs,
            'status'      => $status,
            'tool_calls'  => $toolCalls,
        ];
    }

    /**
     * Firma estable de un turno de tool_calls para la deduplicación.
     *
     * @param array $toolCalls tool_calls del assistant.
     *
     * @return string|null null si no hay tool_calls.
     */
    private function firmaTurno(array $toolCalls): ?string
    {
        $partes = [];
        foreach ($toolCalls as $tc) {
            $fn = is_array($tc['function'] ?? null) ? $tc['function'] : [];
            $partes[] = (string) ($fn['name'] ?? '')
                . ':' . (string) ($fn['arguments'] ?? '');
        }
        return $partes === [] ? null : implode('|', $partes);
    }

    /**
     * Normaliza un tool_call del LLM a un descriptor interno.
     *
     * @param mixed $tc Elemento de tool_calls.
     *
     * @return array{name: string, arguments: array, id: string}
     */
    private function normalizarToolCall(mixed $tc): array
    {
        if (!is_array($tc)) {
            return ['name' => '', 'arguments' => [], 'id' => ''];
        }
        $fn = is_array($tc['function'] ?? null) ? $tc['function'] : [];
        $name = (string) ($fn['name'] ?? '');
        $rawArgs = (string) ($fn['arguments'] ?? '');
        $arguments = [];
        if (trim($rawArgs) !== '') {
            $decoded = json_decode($rawArgs, true);
            if (is_array($decoded)) {
                $arguments = $decoded;
            } else {
                $arguments = ['_raw' => $rawArgs];
            }
        }
        return [
            'name'      => $name,
            'arguments' => $arguments,
            'id'        => is_string($tc['id'] ?? null) ? $tc['id'] : '',
        ];
    }

    /** Log de depuración condicionado. */
    private function log(string $message): void
    {
        if ($this->debug) {
            error_log('[NvidiaBrain] ' . $message);
        }
    }
}
