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
 * Suite de pruebas del Webhook de WhatsApp (Proyecto ARA).
 *
 * Cubre: verificación Meta (GET), payloads multi-proveedor (Meta Cloud API /
 * Baileys / WPPConnect), notificaciones de estado (ignoradas), mensajes
 * propios (ignorados), JSON inválido, números inválidos, autenticación de
 * cliente real contra PRUEB25 y respuesta del WhatsappClienteAdapter.
 *
 * Uso:
 *   php bin/test_whatsapp_webhook.php
 *
 * Salida: JSON consolidado con summary {total, passed, failed, total_time_ms}.
 * Exit code: 0 si todos pasan, 1 si alguno falla.
 */

use App\Http\Controllers\WhatsAppWebhookController;

require_once __DIR__ . '/../app/Http/Controllers/WhatsAppWebhookController.php';
require_once __DIR__ . '/../app/Services/ConnectionWrapper.php';
require_once __DIR__ . '/../app/Core/conectar_profit_read.php';
require_once __DIR__ . '/../app/Services/NvidiaBrain/Contracts/AgentToolInterface.php';
require_once __DIR__ . '/../app/Services/NvidiaBrain/NvidiaBrainClient.php';
require_once __DIR__ . '/../app/Services/NvidiaBrain/Tools/Auditoria/ConsultarSaldoClienteTool.php';
require_once __DIR__ . '/../app/Services/NvidiaBrain/Tools/Almacen/AlmacenDbTrait.php';
require_once __DIR__ . '/../app/Services/NvidiaBrain/Tools/Despacho/ConsultarClienteTool.php';
require_once __DIR__ . '/../app/Services/NvidiaBrain/Security/CustomerAuthenticator.php';
require_once __DIR__ . '/../app/Services/NvidiaBrain/Adapters/BaseAdapter.php';
require_once __DIR__ . '/../app/Services/NvidiaBrain/Adapters/WhatsappClienteAdapter.php';
require_once __DIR__ . '/../app/Services/NvidiaBrain/Adapters/AtencionClienteBridge.php';

final class TestWhatsAppWebhook
{
    /** Cliente de prueba verificado contra PRUEB25 (CLI00007). */
    private const CLIENTE_PRUEBA = 'CLI00007';

    /** Teléfono registrado en clientes para CLI00007: 584126529490. */
    private const TELEFONO_PRUEBA = '584126529490';

    private array $resultados = [];

    public function __construct()
    {
        if (!extension_loaded('pdo_odbc')) {
            echo json_encode(['fatal' => 'Falta la extensión pdo_odbc.'], JSON_PRETTY_PRINT), PHP_EOL;
            exit(1);
        }
    }

    public function run(): void
    {
        $tInicio = microtime(true);
        $controller = new WhatsAppWebhookController();

        $this->caso('GET verificación token correcto', function () use ($controller) {
            $r = $controller->handle('GET', [
                'hub_mode'         => 'subscribe',
                'hub_verify_token' => 'ARA_PROYECT_WEBHOOK_2026',
                'hub_challenge'    => '12345',
            ], null);
            return $r['code'] === 200 && $r['payload'] === '12345'
                ? ['ok', 'challenge devuelto'] : ['fail', json_encode($r)];
        });

        $this->caso('GET verificación token incorrecto', function () use ($controller) {
            $r = $controller->handle('GET', [
                'hub_mode'         => 'subscribe',
                'hub_verify_token' => 'token-incorrecto',
                'hub_challenge'    => '12345',
            ], null);
            return $r['code'] === 403
                ? ['ok', '403 rechazado'] : ['fail', json_encode($r)];
        });

        $this->caso('POST JSON inválido', function () use ($controller) {
            $r = $controller->handle('POST', [], 'esto-no-es-json');
            return $r['code'] === 400
                ? ['ok', '400 json inválido'] : ['fail', json_encode($r)];
        });

        $this->caso('POST notificación de estado (Meta statuses)', function () use ($controller) {
            $r = $controller->handle('POST', [], json_encode([
                'object' => 'whatsapp_business_account',
                'entry'  => [[
                    'changes' => [[
                        'value' => [
                            'statuses' => [[
                                'id'     => 'wamid.ABCDEF',
                                'status' => 'read',
                            ]],
                        ],
                    ]],
                ]],
            ]));
            return isset($r['payload']['status']) && $r['payload']['status'] === 'ignored'
                ? ['ok', 'ignorado'] : ['fail', json_encode($r)];
        });

        $this->caso('POST mensaje propio (Baileys fromMe)', function () use ($controller) {
            $r = $controller->handle('POST', [], json_encode([
                'event' => 'messages.upsert',
                'data'  => [
                    'key'         => ['remoteJid' => '584126529490@s.whatsapp.net', 'fromMe' => true],
                    'messageType' => 'conversation',
                    'message'     => ['conversation' => 'hola'],
                ],
            ]));
            return isset($r['payload']['status']) && $r['payload']['status'] === 'ignored'
                ? ['ok', 'ignorado'] : ['fail', json_encode($r)];
        });

        $this->caso('POST número inválido (WPPConnect <8 dígitos)', function () use ($controller) {
            $r = $controller->handle('POST', [], json_encode([
                'from'    => '12345',
                'message' => 'hola',
            ]));
            return isset($r['payload']['status']) && $r['payload']['status'] === 'ignored'
                ? ['ok', 'ignorado'] : ['fail', json_encode($r)];
        });

        $this->caso('POST número no registrado → respuesta amigable', function () use ($controller) {
            $r = $controller->handle('POST', [], json_encode([
                'object' => 'whatsapp_business_account',
                'entry'  => [[
                    'changes' => [[
                        'value' => [
                            'messages' => [[
                                'from' => '5841399999999',
                                'id'   => 'wamid.TEST99',
                                'type' => 'text',
                                'text' => ['body' => 'hola'],
                            ]],
                        ],
                    ]],
                ]],
            ]));
            $p = $r['payload'];
            if ($r['code'] !== 200 || ($p['status'] ?? '') !== 'ok' || $p['client'] !== null) {
                return ['fail', json_encode($r)];
            }
            return ['ok', 'reply amigable sin exponer datos'];
        });

        $this->caso('POST Baileys conversation → procesado', function () use ($controller) {
            $r = $controller->handle('POST', [], json_encode([
                'event' => 'messages.upsert',
                'data'  => [
                    'key'         => ['remoteJid' => '584126529490@s.whatsapp.net', 'fromMe' => false],
                    'messageType' => 'conversation',
                    'message'     => ['conversation' => 'hola, quiero ver mi saldo'],
                ],
            ]));
            $p = $r['payload'];
            if ($r['code'] !== 200 || ($p['status'] ?? '') !== 'ok' || $p['client'] !== self::CLIENTE_PRUEBA) {
                return ['fail', json_encode($r)];
            }
            return ['ok', 'cliente ' . $p['client'] . ' autenticado y respondido'];
        });

        $this->caso('POST Meta mensaje texto cliente real → saldo', function () use ($controller) {
            $r = $controller->handle('POST', [], json_encode([
                'object' => 'whatsapp_business_account',
                'entry'  => [[
                    'changes' => [[
                        'value' => [
                            'messages' => [[
                                'from' => self::TELEFONO_PRUEBA,
                                'id'   => 'wamid.TESTCLI00007',
                                'type' => 'text',
                                'text' => ['body' => 'hola buenos dias, necesito mi saldo por favor'],
                            ]],
                        ],
                    ]],
                ]],
            ]));
            $p = $r['payload'];
            if ($r['code'] !== 200 || ($p['status'] ?? '') !== 'ok' || $p['client'] !== self::CLIENTE_PRUEBA) {
                return ['fail', json_encode($r)];
            }
            $reply = (string) ($p['reply'] ?? '');
            if ($reply === '' || str_contains(strtolower($reply), 'no sé')) {
                return ['fail', 'respuesta vacía o "no sé"'];
            }
            return ['ok', 'reply=' . mb_substr($reply, 0, 80)];
        });

        $this->caso('POST WPPConnect directo → procesado', function () use ($controller) {
            $r = $controller->handle('POST', [], json_encode([
                'from'    => self::TELEFONO_PRUEBA,
                'message' => 'mi saldo',
            ]));
            $p = $r['payload'];
            if ($r['code'] !== 200 || ($p['status'] ?? '') !== 'ok' || $p['client'] !== self::CLIENTE_PRUEBA) {
                return ['fail', json_encode($r)];
            }
            return ['ok', 'cliente ' . $p['client'] . ' respondido'];
        });

        $total = count($this->resultados);
        $pasaron = count(array_filter($this->resultados, fn ($c) => $c['status'] === 'PASS'));
        $fallaron = $total - $pasaron;
        $totalMs = (int) round((microtime(true) - $tInicio) * 1000);

        $salida = [
            'suite'   => 'whatsapp-webhook',
            'summary' => [
                'total'         => $total,
                'passed'        => $pasaron,
                'failed'        => $fallaron,
                'total_time_ms' => $totalMs,
            ],
            'results' => $this->resultados,
        ];

        echo json_encode($salida, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT), PHP_EOL;
        exit($fallaron === 0 ? 0 : 1);
    }

    private function caso(string $nombre, callable $fn): void
    {
        $tInicio = microtime(true);
        [$status, $detalle] = $fn();
        $ms = (int) round((microtime(true) - $tInicio) * 1000);
        $this->resultados[] = [
            'id'          => 'wh-' . (count($this->resultados) + 1),
            'nombre'      => $nombre,
            'status'      => $status === 'ok' ? 'PASS' : 'FAIL',
            'detalle'     => $detalle,
            'time_ms'     => $ms,
        ];
    }
}

(new TestWhatsAppWebhook())->run();
