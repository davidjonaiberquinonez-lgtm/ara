<?php
/**
 * Endpoint público del Webhook de WhatsApp (Proyecto ARA).
 *
 * Entrada HTTP del controlador WhatsAppWebhookController:
 *   - GET  → Verificación de Webhook Meta (hub.verify_token / hub.challenge).
 *   - POST → Payload JSON de Meta Cloud API / Baileys / WPPConnect /
 *            Evolution API; autentica al cliente en PRUEB25 y responde con
 *            la respuesta del WhatsappClienteAdapter (JSON limpio, < 5s).
 *
 * Uso HTTP:
 *   GET  /api/whatsapp/webhook.php?hub_mode=subscribe&hub_verify_token=...&hub_challenge=12345
 *   POST /api/whatsapp/webhook.php   (Content-Type: application/json)
 *
 * Uso CLI (pruebas):
 *   php public/api/whatsapp/webhook.php GET hub_mode=subscribe hub_verify_token=ARA_PROYECT_WEBHOOK_2026 hub_challenge=12345
 *   php public/api/whatsapp/webhook.php POST --payload='{"entry":[...]}'
 */

declare(strict_types=1);

declare(ticks=1);

// ═══════════════════════════════════════════════════════════════════════
// CANDADO ANTI-ZOMBI (Fase 2.5): el script se suicida a los N segundos
// pase lo que pase. Default 300s; override con env ARA_CLI_MAX_S; tope 600s.
// Watchdog por tick: si excede 240s, fuerza desconexión y aborta (FASE25).
// ═══════════════════════════════════════════════════════════════════════
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
        if (defined('STDERR')) {
            fwrite(STDERR, '{"success":false,"error":"FASE25_KILL: public/api/whatsapp/webhook.php excedió tiempo (' . $t_maxS . 's). Proceso abortado."}');
        }
        error_log('FASE25_KILL: script public/api/whatsapp/webhook.php excedió tiempo (' . $t_maxS . 's)');
        exit(1);
    }
});

use App\Http\Controllers\WhatsAppWebhookController;

require_once __DIR__ . '/../../../app/Http/Controllers/WhatsAppWebhookController.php';
require_once __DIR__ . '/../../../app/Core/conectar_profit_read.php';
require_once __DIR__ . '/../../../app/Services/NvidiaBrain/Contracts/AgentToolInterface.php';
require_once __DIR__ . '/../../../app/Services/NvidiaBrain/NvidiaBrainClient.php';
require_once __DIR__ . '/../../../app/Services/NvidiaBrain/Tools/Auditoria/ConsultarSaldoClienteTool.php';
require_once __DIR__ . '/../../../app/Services/ConnectionWrapper.php';
require_once __DIR__ . '/../../../app/Services/NvidiaBrain/Tools/Almacen/AlmacenDbTrait.php';
require_once __DIR__ . '/../../../app/Services/NvidiaBrain/Tools/Despacho/ConsultarClienteTool.php';
require_once __DIR__ . '/../../../app/Services/NvidiaBrain/Security/CustomerAuthenticator.php';
require_once __DIR__ . '/../../../app/Services/NvidiaBrain/Adapters/BaseAdapter.php';
require_once __DIR__ . '/../../../app/Services/NvidiaBrain/Adapters/WhatsappClienteAdapter.php';
require_once __DIR__ . '/../../../app/Services/NvidiaBrain/Adapters/AtencionClienteBridge.php';

// ── Modo CLI de prueba: argv simula método HTTP, query y body ─────────────
$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
$query = $_GET;
$rawBody = file_get_contents('php://input');
$rawBody = is_string($rawBody) ? $rawBody : null;

if (PHP_SAPI === 'cli') {
    $args = array_slice($argv, 1);
    $method = strtoupper((string) ($args[0] ?? 'GET'));
    $query = [];
    $rawBody = null;
    foreach (array_slice($args, 1) as $arg) {
        if (str_starts_with($arg, '--payload=')) {
            $rawBody = substr($arg, strlen('--payload='));
            continue;
        }
        $pos = strpos($arg, '=');
        if ($pos !== false) {
            $query[substr($arg, 0, $pos)] = substr($arg, $pos + 1);
        }
    }
    if ($method === 'POST' && $rawBody === null) {
        $stdin = stream_get_contents(STDIN);
        $rawBody = is_string($stdin) && trim($stdin) !== '' ? $stdin : null;
    }
}

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');

try {
    $controlador = new WhatsAppWebhookController();
    $resultado = $controlador->handle($method, $query, $rawBody);

    http_response_code($resultado['code']);

    if (is_string($resultado['payload'])) {
        // Verificación Meta: el challenge se responde como texto plano.
        echo $resultado['payload'];
    } else {
        echo json_encode($resultado['payload'], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    }
} catch (\Throwable $e) {
    error_log('[WhatsAppWebhook] Error global: ' . $e->getMessage());
    http_response_code(500);
    echo json_encode([
        'status'  => 'error',
        'message' => 'No pudimos procesar la solicitud en este momento.',
    ], JSON_UNESCAPED_UNICODE);
}
