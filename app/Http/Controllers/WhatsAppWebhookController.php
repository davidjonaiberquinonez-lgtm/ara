<?php

declare(strict_types=1);

namespace App\Http\Controllers;

use App\Services\NvidiaBrain\Adapters\AtencionClienteBridge;
use App\Services\NvidiaBrain\Adapters\WhatsappClienteAdapter;
use App\Services\NvidiaBrain\NvidiaBrainClient;
use App\Services\NvidiaBrain\Security\CustomerAuthenticator;
use PDO;
use PDOException;
use Throwable;

/**
 * Controlador del Webhook de WhatsApp (Proyecto ARA).
 *
 * Recibe eventos y mensajes entrantes de WhatsApp de forma multi-proveedor
 * (Meta Cloud API, Baileys, WPPConnect y Evolution API), extrae el número
 * remitente y el texto, autentica al cliente contra Profit Plus (PRUEB25) y
 * procesa la consulta con WhatsappClienteAdapter.
 *
 * FLUJO:
 *   1. GET  → Verificación de Webhook (Meta): compara hub.verify_token contra
 *             WHATSAPP_WEBHOOK_VERIFY_TOKEN (.env) y responde hub.challenge.
 *   2. POST → Recepción del payload JSON:
 *       a. Normalización multi-proveedor del payload (Meta/Baileys/
 *          WPPConnect/Evolution) a {numero, texto}.
 *       b. Se IGNORAN las notificaciones de estado (confirmaciones de
 *          lectura/entrega, mensajes enviados por la propia empresa o
 *          protocolMessages) para evitar loops infinitos de respuesta.
 *       c. Autenticación del cliente: búsqueda por los últimos 4 dígitos del
 *          número en clientes.telefonos + validación estricta con
 *          CustomerAuthenticator (factor secundario). Sin cliente → mensaje
 *          amigable genérico, SIN exponer información sensible.
 *       d. WhatsappClienteAdapter con un cliente LLM ACOTADO (timeout 2s)
 *          para garantizar respuesta < 5s: si el modelo no responde rápido,
 *          el adaptador degrada a su fallback determinista.
 *
 * RESPUESTA: siempre JSON limpio; cualquier excepción se reporta como error
 * genérico al proveedor y se loguea internamente (nunca se exponen detalles).
 *
 * Tiempo objetivo: < 5s de ejecución (autenticación + tool + LLM ≤ 2s).
 */
final class WhatsAppWebhookController
{
    /** Token de verificación por defecto (sobrescribible con la env). */
    private const DEFAULT_VERIFY_TOKEN = 'ARA_PROYECT_WEBHOOK_2026';

    /** Claves del .env que alimentan el webhook. */
    private const ENV_KEYS = ['WHATSAPP_WEBHOOK_VERIFY_TOKEN', 'WHATSAPP_WEBHOOK_REPLY_URL'];

    private ?PDO $pdo;

    private CustomerAuthenticator $authenticator;

    /** @var array<string,string> Variables de entorno resueltas (.env + getenv). */
    private array $env;

    /**
     * @param PDO|null $pdo Conexión de lectura a PRUEB25 (si null se abre
     *                       bajo demanda con conectar_profit_read()).
     */
    public function __construct(?PDO $pdo = null)
    {
        $this->pdo = $pdo;
        $this->authenticator = new CustomerAuthenticator();
        $this->env = $this->cargarEnv(__DIR__ . '/../../../.env');
    }

    /**
     * Punto de entrada: enruta por método HTTP.
     *
     * @return array{code: int, payload: array<string,mixed>|string}
     */
    public function handle(string $method, array $query, ?string $rawBody): array
    {
        $tInicio = microtime(true);

        $resultado = strtoupper($method) === 'POST'
            ? $this->handlePost($rawBody)
            : $this->handleVerification($query);

        if (is_array($resultado['payload']) && !isset($resultado['payload']['timestamp'])) {
            $resultado['payload']['timestamp'] = date('c');
        }
        if (is_array($resultado['payload'])) {
            $resultado['payload']['latency_ms'] = (int) round((microtime(true) - $tInicio) * 1000);
        }

        return $resultado;
    }

    /**
     * GET: verificación de Webhook (Meta Cloud API).
     *
     * @param array<string,mixed> $query Parámetros de la petición (hub_*).
     *
     * @return array{code: int, payload: array<string,mixed>|string}
     */
    private function handleVerification(array $query): array
    {
        $mode = (string) ($query['hub_mode'] ?? '');
        $token = (string) ($query['hub_verify_token'] ?? '');
        $challenge = (string) ($query['hub_challenge'] ?? '');

        $esperado = $this->env['WHATSAPP_WEBHOOK_VERIFY_TOKEN'] ?? self::DEFAULT_VERIFY_TOKEN;

        if ($mode === 'subscribe' && hash_equals($esperado, $token) && $challenge !== '') {
            // Meta espera el challenge como texto plano con HTTP 200.
            return ['code' => 200, 'payload' => $challenge];
        }

        error_log('[WhatsAppWebhook] Verificación fallida: mode=' . $mode
            . ' | token_valid=' . ($esperado !== '' && hash_equals($esperado, $token) ? 'si' : 'no'));
        return [
            'code'    => 403,
            'payload' => [
                'status'  => 'error',
                'message' => 'Verificación de webhook rechazada.',
            ],
        ];
    }

    /**
     * POST: recepción del payload y procesamiento del mensaje.
     *
     * @return array{code: int, payload: array<string,mixed>}
     */
    private function handlePost(?string $rawBody): array
    {
        $body = trim((string) $rawBody);
        if ($body === '') {
            return ['code' => 400, 'payload' => ['status' => 'error', 'message' => 'Body vacío.']];
        }

        $payload = json_decode($body, true);
        if (!is_array($payload)) {
            return ['code' => 400, 'payload' => ['status' => 'error', 'message' => 'Payload JSON inválido.']];
        }

        // ── 1) Extracción multi-proveedor + filtro de ignorados ──────────────
        $extraido = $this->extraerMensaje($payload);
        if (!$extraido['procesar']) {
            return [
                'code'    => 200,
                'payload' => [
                    'status' => 'ignored',
                    'reason' => $extraido['motivo'] ?? 'sin mensaje procesable',
                ],
            ];
        }

        $numero = $extraido['numero'];
        $texto = $extraido['texto'];
        $phoneNumberId = (string) ($extraido['phone_number_id'] ?? '');

        $pdoLocal = $this->pdo === null;
        try {
            $pdo = $this->pdo ?? conectar_profit_read();

            // ── 2) Autenticación del cliente (últimos 4 dígitos + telefonos) ─
            $cliente = $this->autenticarPorTelefono($pdo, $numero);

            if ($cliente === null) {
                return [
                    'code'    => 200,
                    'payload' => [
                        'status'  => 'ok',
                        'source'  => 'whatsapp',
                        'reply'   => 'Hola, gracias por escribirnos. Para atender tu '
                            . 'consulta necesitamos confirmar tu identidad: por favor '
                            . 'escríbenos con el número de teléfono registrado en tu '
                            . 'cuenta. Nuestro equipo de soporte está disponible en '
                            . 'horario laboral para ayudarte.',
                        'client'  => null,
                    ],
                ];
            }

            // ── 2.5) Puente al módulo Atención al Cliente (bandeja Python,
            //         v4.41): registra el mensaje entrante y decide si esta
            //         conversación la controla la IA o ya la tomó un agente
            //         humano. Best-effort absoluto — cualquier falla aquí
            //         (tabla no migrada, phone_number_id desconocido, etc.)
            //         NUNCA debe romper la respuesta automática al cliente,
            //         así que se degrada a "sin bandeja, responde la IA".
            $conversacionBandeja = null;
            $controladaPorAgente = false;
            if ($phoneNumberId !== '') {
                try {
                    $bridge = new AtencionClienteBridge();
                    $numeroAgente = $bridge->resolverAgentePorNumero($phoneNumberId);
                    if ($numeroAgente !== null) {
                        $conversacionBandeja = $bridge->buscarOCrearConversacion(
                            (string) $cliente['co_cli'],
                            (string) ($cliente['nombre'] ?? ''),
                            $numero,
                            $phoneNumberId
                        );
                        $bridge->registrarMensajeCliente((int) $conversacionBandeja['id'], $texto);
                        $controladaPorAgente = ($conversacionBandeja['modo'] ?? 'ia') === 'agente';
                    }
                } catch (Throwable $e) {
                    error_log('[WhatsAppWebhook] Puente Atención al Cliente falló (no bloquea): '
                        . $e->getMessage());
                }
            }

            if ($controladaPorAgente) {
                // Un agente humano ya tomó este hilo desde la bandeja: la IA
                // NO debe autoresponder, el mensaje queda encolado para que
                // el agente lo conteste manualmente vía /api/atencion/enviar.
                return [
                    'code'    => 200,
                    'payload' => [
                        'status' => 'ok',
                        'source' => 'whatsapp',
                        'reply'  => null,
                        'client' => (string) $cliente['co_cli'],
                        'mode'   => 'queued_for_agent',
                    ],
                ];
            }

            // ── 3) Adaptador con LLM ACOTADO (≤ 2s; fallback determinista) ───
            // Blindaje de BaseAdapter::preguntar(): solo respeta el cliente
            // inyectado si su modelo primario es qwen2.5-coder:3b (el mismo).
            $clientLl = new NvidiaBrainClient(
                baseUrl: NvidiaBrainClient::OLLAMA_BASE_URL,
                apiKey: null,
                model: NvidiaBrainClient::OLLAMA_MODEL,
                timeoutS: 2,
                connectTimeout: 1,
                maxRetries: 0
            );

            $adapter = new WhatsappClienteAdapter(
                client: $clientLl,
                authenticator: $this->authenticator,
                dbProfit: $pdo
            );

            $resultado = $adapter->procesar($texto, [
                'identificador'     => (string) $cliente['co_cli'],
                'factor_secundario' => $cliente['ultimos_4'],
                'origen_whatsapp'   => $numero,
            ]);
            $resultado = WhatsappClienteAdapter::sanitizar_utf8($resultado);

            $respuesta = (string) ($resultado['respuesta'] ?? '');
            if (trim($respuesta) === '') {
                $respuesta = 'Recibimos tu mensaje. Nuestro equipo de soporte te '
                    . 'responderá en breve.';
            }

            // Envío externo opcional (encolado directo vía cURL si se configura).
            $this->enviarRespuestaExterna((string) $cliente['co_cli'], $respuesta);

            // Registra la respuesta de la IA en la bandeja (si había puente activo).
            if ($conversacionBandeja !== null) {
                try {
                    $origenDatos = is_array($resultado['data'] ?? null) && isset($resultado['data']['saldo'])
                        ? 'consultar_saldo_cliente' : '';
                    (new AtencionClienteBridge())->registrarMensajeIA(
                        (int) $conversacionBandeja['id'],
                        $respuesta,
                        $origenDatos
                    );
                } catch (Throwable $e) {
                    error_log('[WhatsAppWebhook] No se pudo registrar respuesta IA en bandeja: '
                        . $e->getMessage());
                }
            }

            return [
                'code'    => 200,
                'payload' => [
                    'status' => 'ok',
                    'source' => 'whatsapp',
                    'reply'  => $respuesta,
                    'client' => (string) $cliente['co_cli'],
                ],
            ];
        } catch (Throwable $e) {
            error_log('[WhatsAppWebhook] Error procesando mensaje: ' . $e->getMessage());
            return [
                'code'    => 200,
                'payload' => [
                    'status'  => 'error',
                    'message' => 'No pudimos procesar tu mensaje en este momento. '
                        . 'Intenta de nuevo en unos minutos.',
                ],
            ];
        } finally {
            // Candado anti-zombi: solo cierra la conexión que este método abrió
            // (conectar_profit_read()); la inyectada por constructor la maneja
            // su dueño.
            if ($pdoLocal) {
                $pdo = null;
                gc_collect_cycles();
            }
        }
    }

    /**
     * Normaliza el payload multi-proveedor a {numero, texto} y decide si el
     * mensaje debe procesarse (ignora estados, mensajes propios y vacíos).
     *
     * Proveedores soportados:
     *   - Meta Cloud API: entry[].changes[].value.messages[] / value.statuses.
     *   - Baileys / Evolution API: data.key.remoteJid, data.message.*.
     *   - WPPConnect / directos: {from, text|message|body}.
     *
     * @param array<string,mixed> $payload
     *
     * @return array{procesar: bool, motivo?: string, numero: string, texto: string}
     */
    private function extraerMensaje(array $payload): array
    {
        $numero = '';
        $texto = '';
        $procesar = false;
        $motivo = '';
        $phoneNumberId = '';

        // ── Meta Cloud API ───────────────────────────────────────────────────
        if (isset($payload['entry']) && is_array($payload['entry'])) {
            foreach ($payload['entry'] as $entry) {
                $changes = is_array($entry['changes'] ?? null) ? $entry['changes'] : [];
                foreach ($changes as $change) {
                    $value = $change['value'] ?? null;
                    if (!is_array($value)) {
                        continue;
                    }
                    // Estados (lectura/entrega): se ignoran sin responder.
                    if (isset($value['statuses']) && !isset($value['messages'])) {
                        return ['procesar' => false, 'motivo' => 'status', 'numero' => '', 'texto' => ''];
                    }
                    // metadata.phone_number_id identifica CUÁL número de Meta
                    // recibió el mensaje (módulo Atención al Cliente, v4.41):
                    // con "1 WABA / varios números" es la única forma de saber
                    // a qué agente le pertenece esta conversación.
                    $metadata = $value['metadata'] ?? null;
                    if (is_array($metadata) && isset($metadata['phone_number_id'])) {
                        $phoneNumberId = trim((string) $metadata['phone_number_id']);
                    }
                    $messages = is_array($value['messages'] ?? null) ? $value['messages'] : [];
                    foreach ($messages as $msg) {
                        if (!is_array($msg)) {
                            continue;
                        }
                        $from = trim((string) ($msg['from'] ?? ''));
                        $cuerpo = '';
                        if (isset($msg['text']) && is_array($msg['text'])) {
                            $cuerpo = (string) ($msg['text']['body'] ?? '');
                        } elseif (isset($msg['text']) && is_string($msg['text'])) {
                            $cuerpo = $msg['text'];
                        }
                        if ($from !== '') {
                            $numero = $from;
                            $texto = trim($cuerpo);
                            $procesar = $texto !== '';
                            $motivo = $procesar ? '' : 'mensaje_sin_texto';
                        }
                    }
                }
            }
            $resultado = $this->finalizarExtraccion($procesar, $motivo, $numero, $texto);
            $resultado['phone_number_id'] = $phoneNumberId;
            return $resultado;
        }

        // ── Baileys / Evolution API (wrapper de Baileys) ─────────────────────
        $nodo = $payload['data'] ?? $payload;
        if (is_array($nodo)) {
            $key = $nodo['key'] ?? $payload['key'] ?? null;
            if (is_array($key)) {
                $fromMe = (bool) ($key['fromMe'] ?? false);
                $remoteJid = (string) ($key['remoteJid'] ?? '');
                if ($fromMe) {
                    return ['procesar' => false, 'motivo' => 'mensaje_propio', 'numero' => '', 'texto' => ''];
                }
                if ($remoteJid !== '') {
                    $numero = $this->limpiarNumero($remoteJid);
                    $tipo = (string) ($nodo['messageType'] ?? '');
                    if (str_contains($tipo, 'protocolMessage')) {
                        return ['procesar' => false, 'motivo' => 'protocol_message', 'numero' => '', 'texto' => ''];
                    }
                    $msg = $nodo['message'] ?? null;
                    if (is_array($msg)) {
                        if (isset($msg['conversation']) && is_string($msg['conversation'])) {
                            $texto = trim($msg['conversation']);
                        } elseif (isset($msg['extendedTextMessage']['text']) && is_string($msg['extendedTextMessage']['text'])) {
                            $texto = trim($msg['extendedTextMessage']['text']);
                        }
                    }
                    $procesar = $numero !== '' && $texto !== '';
                    $motivo = $procesar ? '' : 'mensaje_sin_texto';
                    return $this->finalizarExtraccion($procesar, $motivo, $numero, $texto);
                }
            }
        }

        // ── WPPConnect / directos: {from, message|text|body} ─────────────────
        $from = (string) ($payload['from'] ?? '');
        if ($from !== '') {
            $numero = $this->limpiarNumero($from);
            $cuerpo = '';
            foreach (['message', 'text', 'body'] as $clave) {
                if (isset($payload[$clave]) && is_string($payload[$clave])) {
                    $cuerpo = $payload[$clave];
                    break;
                }
            }
            $texto = trim($cuerpo);
            $procesar = $numero !== '' && $texto !== '';
            $motivo = $procesar ? '' : 'mensaje_sin_texto';
            return $this->finalizarExtraccion($procesar, $motivo, $numero, $texto);
        }

        return ['procesar' => false, 'motivo' => 'payload_no_reconocido', 'numero' => '', 'texto' => ''];
    }

    /**
     * Normaliza el resultado de la extracción (número limpio y decidir si el
     * número es demasiado corto para autenticar).
     *
     * @return array{procesar: bool, motivo?: string, numero: string, texto: string}
     */
    private function finalizarExtraccion(bool $procesar, string $motivo, string $numero, string $texto): array
    {
        $numero = $this->limpiarNumero($numero);
        if ($procesar && strlen(preg_replace('/\D/', '', $numero) ?? '') < 8) {
            return ['procesar' => false, 'motivo' => 'numero_invalido', 'numero' => $numero, 'texto' => $texto];
        }
        return ['procesar' => $procesar, 'motivo' => $motivo, 'numero' => $numero, 'texto' => $texto];
    }

    /**
     * Autentica al cliente por teléfono: busca en clientes.telefonos por los
     * últimos 4 dígitos del número y valida estrictamente con
     * CustomerAuthenticator (factor secundario). NUNCA expone datos si falla.
     *
     * @return array{co_cli: string, nombre: ?string, ultimos_4: string}|null
     */
    private function autenticarPorTelefono(PDO $pdo, string $numero): ?array
    {
        $digitos = preg_replace('/\D/', '', $numero) ?? '';
        if (strlen($digitos) < 8) {
            return null;
        }
        $ultimos4 = substr($digitos, -4);

        // Candidatos cuyo teléfono termina en los mismos 4 dígitos.
        $stmt = $pdo->prepare(
            'SELECT TOP 10 [co_cli] AS co_cli, [cli_des] AS cli_des, [telefonos] AS telefonos '
            . 'FROM [clientes] '
            . 'WHERE [inactivo] = 0 '
            . 'AND RIGHT(REPLACE(REPLACE(LTRIM(RTRIM([telefonos])), \' \', \'\'), \'-\', \'\'), 4) = ? '
            . 'ORDER BY [co_cli]'
        );
        $stmt->execute([$ultimos4]);
        $candidatos = $stmt->fetchAll(PDO::FETCH_ASSOC);

        foreach ($candidatos as $candidato) {
            $coCli = trim((string) ($candidato['co_cli'] ?? ''));
            if ($coCli === '') {
                continue;
            }
            // Validación estricta: el factor secundario debe coincidir con el
            // teléfono registrado del cliente (y/o el origen de WhatsApp).
            $cliente = $this->authenticator->validarCliente(
                $pdo,
                $coCli,
                $ultimos4,
                $digitos
            );
            if ($cliente !== null && isset($cliente['co_cli'])) {
                return [
                    'co_cli'    => (string) $cliente['co_cli'],
                    'nombre'    => isset($cliente['nombre']) ? (string) $cliente['nombre'] : null,
                    'ultimos_4' => $ultimos4,
                ];
            }
        }

        return null;
    }

    /**
     * Envío externo opcional (best-effort, no bloquea la respuesta): si la
     * env WHATSAPP_WEBHOOK_REPLY_URL está configurada, POSTea {to, message}
     * al servicio de mensajería con timeout corto.
     */
    private function enviarRespuestaExterna(string $destino, string $texto): void
    {
        $url = $this->env['WHATSAPP_WEBHOOK_REPLY_URL'] ?? '';
        if ($url === '') {
            return;
        }
        try {
            $ch = curl_init($url);
            if ($ch === false) {
                return;
            }
            curl_setopt_array($ch, [
                CURLOPT_RETURNTRANSFER => true,
                CURLOPT_POST           => true,
                CURLOPT_POSTFIELDS     => json_encode([
                    'to'      => $destino,
                    'message' => $texto,
                ], JSON_UNESCAPED_UNICODE),
                CURLOPT_HTTPHEADER     => ['Content-Type: application/json', 'Expect:'],
                CURLOPT_CONNECTTIMEOUT => 2,
                CURLOPT_TIMEOUT        => 3,
                CURLOPT_SSL_VERIFYPEER => true,
            ]);
            curl_exec($ch);
            curl_close($ch);
        } catch (Throwable $e) {
            error_log('[WhatsAppWebhook] Envío externo falló: ' . $e->getMessage());
        }
    }

    /**
     * Normaliza un número de WhatsApp: solo dígitos, sin sufijo @s.whatsapp.net.
     */
    private function limpiarNumero(string $numero): string
    {
        $sinSufijo = str_replace('@s.whatsapp.net', '', trim($numero));
        return preg_replace('/\D/', '', $sinSufijo) ?? '';
    }

    /**
     * Mini cargador de .env: solo las claves del webhook.
     *
     * @return array<string,string>
     */
    private function cargarEnv(string $file): array
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
            [$k, $v] = array_map('trim', explode('=', $line, 2));
            if (in_array($k, self::ENV_KEYS, true)) {
                $out[$k] = trim($v, "\"'");
            }
        }
        foreach (self::ENV_KEYS as $k) {
            $g = getenv($k);
            if (is_string($g) && trim($g) !== '') {
                $out[$k] = trim($g);
            }
        }
        return $out;
    }
}
