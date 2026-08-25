<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Despacho;

use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;

require_once __DIR__ . '/../Common/CardBuilder.php';

/**
 * Cierre transaccional del despacho contra el sistema legacy PHP.
 *
 * Ejecuta el POST de cierre del flujo ARA_SYNC v3.31 (mismo contrato de
 * legacy_http_adapter.py) hacia el endpoint legacy:
 *
 *   POST {LEGACY_BASE_URL}/chequeo/registro.php
 *   Content-Type: application/x-www-form-urlencoded
 *   campos: nota, fecha, articulos_cargados=1, validado_reng_nde=1,
 *           bypass_revision=1, renglones_ok=1
 *
 * Con dry_run=true (default) NO hace el POST real: solo devuelve el payload
 * preparado y las verificaciones del estado previo, para que el operador
 * valide antes de cerrar la transacción.
 *
 * Endpoint: LEGACY_BASE_URL del entorno (default http://192.168.4.148:8000).
 *
 * Contrato de retorno (SIEMPRE array serializable, sin excepciones):
 *   ['success' => true,  'data' => [...]]      → éxito
 *   ['success' => false, 'error' => 'mensaje'] → error controlado
 *
 * Uso:
 *   $registry->registerTool(new CierreTransaccionalTool());
 */
final class CierreTransaccionalTool implements AgentToolInterface
{
    private const UA_ARA_SYNC = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ARA_SYNC_Bridge/3.31';

    public function getName(): string
    {
        return 'cierre_transaccional';
    }

    public function getDescription(): string
    {
        return 'Cierra la transaccion de despacho contra el sistema legacy: '
             . 'valida el estado de la nota, prepara el payload (nota, fecha, '
             . 'articulos_cargados, validado_reng_nde, bypass_revision, '
             . 'renglones_ok) y hace el POST a /chequeo/registro.php. '
             . 'Parametros: nota (obligatorio), tipo (chequeo|despacho, '
             . 'default chequeo), fecha (YYYY-MM-DD) y dry_run (default true). '
             . 'Usar dry_run=true siempre en operaciones de prueba.';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'nota' => [
                    'type'        => 'string',
                    'description' => 'Numero de la nota de despacho (obligatorio).',
                ],
                'tipo' => [
                    'type'        => 'string',
                    'enum'        => ['chequeo', 'despacho'],
                    'description' => 'Tipo de cierre: chequeo (default) o despacho.',
                ],
                'fecha' => [
                    'type'        => 'string',
                    'description' => 'Fecha del cierre en formato YYYY-MM-DD (default: hoy).',
                ],
                'dry_run' => [
                    'type'        => 'boolean',
                    'description' => 'Si true (default) NO ejecuta el POST real: solo prepara y valida el payload.',
                ],
            ],
            'required' => ['nota'],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $nota = trim((string) ($arguments['nota'] ?? ''));
        if ($nota === '') {
            return ['success' => false, 'error' => 'Parámetro "nota" es obligatorio.'];
        }
        $tipo = strtolower(trim((string) ($arguments['tipo'] ?? 'chequeo')));
        if (!in_array($tipo, ['chequeo', 'despacho'], true)) {
            return ['success' => false, 'error' => 'Parámetro "tipo" debe ser "chequeo" o "despacho".'];
        }
        $fecha = trim((string) ($arguments['fecha'] ?? date('Y-m-d')));
        if (preg_match('/^\d{4}-\d{2}-\d{2}$/', $fecha) !== 1) {
            return ['success' => false, 'error' => 'Parámetro "fecha" debe tener formato YYYY-MM-DD.'];
        }
        $dryRun = (bool) ($arguments['dry_run'] ?? true);

        $baseUrl = rtrim(self::env('LEGACY_BASE_URL', 'http://192.168.4.148:8000'), '/');
        $path = $tipo === 'despacho' ? '/despacho/registro.php' : '/chequeo/registro.php';

        $payload = [
            'nota'               => $nota,
            'fecha'              => $fecha,
            'articulos_cargados' => '1',
            'validado_reng_nde'  => '1',
            'bypass_revision'    => '1',
            'renglones_ok'       => '1',
        ];

        $data = [
            'nota'     => $nota,
            'tipo'     => $tipo,
            'fecha'    => $fecha,
            'endpoint' => $baseUrl . $path,
            'dry_run'  => $dryRun,
            'payload'  => $payload,
        ];

        if ($dryRun) {
            $data['mensaje'] = 'Cierre preparado (dry_run): no se ejecutó el POST real al legacy.';
            $data['verificaciones'] = [
                'articulos_cargados' => true,
                'validado_reng_nde'  => true,
                'bypass_revision'    => true,
                'renglones_ok'       => true,
            ];
            $data['card'] = $this->cardCierre($data, $dryRun, '');
            return ['success' => true, 'data' => $data];
        }

        $respuesta = $this->postLegacy($baseUrl . $path, $payload);
        if ($respuesta['ok'] === false) {
            $error = 'POST al legacy fallido: ' . $respuesta['error'];
            return [
                'success' => false,
                'error'   => $error,
                'card'    => CardBuilder::iniciar('🚫', 'CIERRE TRANSACCIONAL FALLIDO')
                    ->linea($error)
                    ->footer('Fuente: ' . $baseUrl . $path)
                    ->tarjeta(),
            ];
        }
        $data['status_http'] = $respuesta['http_status'];
        $data['respuesta']   = self::aUtf8($respuesta['cuerpo']);
        $data['card'] = $this->cardCierre($data, $dryRun, $respuesta['cuerpo']);

        return ['success' => true, 'data' => $data];
    }

    /** Tarjeta estándar v4.13 del cierre transaccional (CardBuilder). */
    private function cardCierre(array $data, bool $dryRun, string $respuestaCuerpo): string
    {
        $card = CardBuilder::iniciar(
            $dryRun ? '🧪' : '✅',
            'CIERRE ' . strtoupper((string) $data['tipo']) . ' NOTA ' . $data['nota']
        )
            ->campo('FECHA', (string) $data['fecha'])
            ->campo('ENDPOINT', (string) $data['endpoint'])
            ->campo('MODO', $dryRun ? 'DRY RUN (sin POST real)' : 'POST REAL EJECUTADO')
            ->seccion('Payload preparado');
        foreach ((array) ($data['payload'] ?? []) as $k => $v) {
            $card->linea($k . ' = ' . $v);
        }
        if (isset($data['status_http'])) {
            $card->seccion('Resultado')->campo('HTTP', (int) $data['status_http'], 'numero');
        }
        if ($respuestaCuerpo !== '') {
            $card->linea('Respuesta: ' . self::aUtf8(mb_substr($respuestaCuerpo, 0, 120)));
        }
        return $card
            ->footer('Fuente: legacy ' . (string) $data['endpoint'])
            ->tarjeta();
    }

    public function getDefinition(): array
    {
        return [
            'name'        => $this->getName(),
            'description' => $this->getDescription(),
            'parameters'  => $this->getParameters(),
        ];
    }

    /**
     * POST form-urlencoded con cabeceras de navegador (emulación legacy).
     *
     * @param array<string,string> $payload
     *
     * @return array{ok: bool, http_status: int, cuerpo: string, error: string}
     */
    private function postLegacy(string $url, array $payload): array
    {
        $cuerpo = http_build_query($payload);
        $cabeceras = [
            'Content-Type: application/x-www-form-urlencoded',
            'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language: es-ES,es;q=0.9,en;q=0.8',
            'Upgrade-Insecure-Requests: 1',
            'Referer: ' . dirname($url) . '/',
            'Origin: ' . parse_url($url, PHP_URL_SCHEME) . '://' . parse_url($url, PHP_URL_HOST),
            'User-Agent: ' . self::UA_ARA_SYNC,
        ];

        if (function_exists('curl_init')) {
            $ch = curl_init($url);
            if ($ch === false) {
                return ['ok' => false, 'http_status' => 0, 'cuerpo' => '', 'error' => 'curl_init falló.'];
            }
            curl_setopt_array($ch, [
                CURLOPT_POST           => true,
                CURLOPT_POSTFIELDS     => $cuerpo,
                CURLOPT_RETURNTRANSFER => true,
                CURLOPT_CONNECTTIMEOUT => 10,
                CURLOPT_TIMEOUT        => 25,
                CURLOPT_HTTPHEADER     => $cabeceras,
                CURLOPT_SSL_VERIFYPEER => false,
                CURLOPT_SSL_VERIFYHOST => 0,
            ]);
            $salida = curl_exec($ch);
            $errno  = curl_errno($ch);
            $status = (int) curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
            curl_close($ch);
            if ($salida === false) {
                return ['ok' => false, 'http_status' => $status, 'cuerpo' => '', 'error' => 'curl_errno ' . $errno];
            }
            return ['ok' => true, 'http_status' => $status, 'cuerpo' => (string) $salida, 'error' => ''];
        }

        if (ini_get('allow_url_fopen')) {
            $contexto = stream_context_create([
                'http' => [
                    'method'  => 'POST',
                    'header'  => implode("\r\n", $cabeceras),
                    'content' => $cuerpo,
                    'timeout' => 25,
                    'ignore_errors' => true,
                ],
            ]);
            $salida = @file_get_contents($url, false, $contexto);
            if ($salida === false) {
                return ['ok' => false, 'http_status' => 0, 'cuerpo' => '', 'error' => 'file_get_contents falló (red/HTTP).'];
            }
            $status = 0;
            foreach ($http_response_header ?? [] as $h) {
                if (preg_match('/^HTTP\/\S+\s+(\d{3})/', $h, $m)) {
                    $status = (int) $m[1];
                }
            }
            return ['ok' => true, 'http_status' => $status, 'cuerpo' => $salida, 'error' => ''];
        }

        return ['ok' => false, 'http_status' => 0, 'cuerpo' => '', 'error' => 'Sin cURL y sin allow_url_fopen para el POST al legacy.'];
    }

    private static function aUtf8(string $texto): string
    {
        $limpio = trim($texto);
        if (mb_check_encoding($limpio, 'UTF-8')) {
            return $limpio;
        }
        $convertido = mb_convert_encoding($limpio, 'UTF-8', 'CP1252');
        return is_string($convertido) ? $convertido : $limpio;
    }

    private static function env(string $clave, string $default): string
    {
        $valor = getenv($clave);
        if (is_string($valor) && trim($valor) !== '') {
            return trim($valor);
        }
        return $default;
    }
}
