<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Recepcion;

use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;

require_once __DIR__ . '/../Common/CardBuilder.php';

/**
 * Herramienta "extraer_campos_factura_ocr": extrae los campos de un
 * comprobante de retención (fecha, nro_comprobante, cliente, nro_factura,
 * rif_cliente, monto_retenido) desde una imagen (JPG/PNG/WebP/BMP) o PDF,
 * usando la API OCR externa del propio usuario (proyecto de retenciones).
 *
 * A diferencia de leer_voucher_ocr (que llama a un modelo de visión NIM
 * genérico para vouchers bancarios), esta tool delega TODO el OCR a un
 * servicio HTTP dedicado ya entrenado para comprobantes de retención —
 * no reimplementa el prompt/modelo, solo hace de puente (multipart/form-data,
 * campo "archivo") y traduce la respuesta al formato estándar de tools.
 *
 * API real (confirmada en vivo por el usuario, 2026-08-21):
 *   Base URL:  http://192.168.4.217:5031 (ARA_RETENCIONES_OCR_URL)
 *   GET  /api/estado    → health-check
 *   POST /api/escanear  → multipart/form-data, campo "archivo"
 *        { total_paginas, resultados: [{ pagina, archivo, ok, motor, campos }] }
 *
 * Contrato de retorno (SIEMPRE array serializable, sin excepciones):
 *   ['success' => true,  'data' => [...]]      → éxito
 *   ['success' => false, 'error' => 'mensaje'] → error controlado
 */
final class ExtraerCamposFacturaOcrTool implements AgentToolInterface
{
    private const EXTENSIONES_VALIDAS = ['bmp', 'jpeg', 'jpg', 'pdf', 'png', 'webp'];

    /** Tamaño máximo del archivo a subir (20 MB — PDFs multipágina). */
    private const TAMANO_MAX_BYTES = 20 * 1024 * 1024;

    public function getName(): string
    {
        return 'extraer_campos_factura_ocr';
    }

    public function getDescription(): string
    {
        return 'Extrae los campos de un comprobante de retención (fecha, '
             . 'número de comprobante, cliente, número de factura, RIF del '
             . 'cliente, monto retenido) desde una imagen (JPG/PNG/WebP/BMP) '
             . 'o PDF, usando el servicio OCR dedicado del usuario. Úsala '
             . 'cuando adjunten un comprobante/retención/factura como archivo '
             . 'para extraer sus datos. Parámetro: ruta_archivo (ruta absoluta '
             . 'del archivo en el servidor, obligatorio).';
    }

    public function getParameters(): array
    {
        return [
            'type'       => 'object',
            'properties' => [
                'ruta_archivo' => [
                    'type'        => 'string',
                    'description' => 'Ruta absoluta del archivo (JPG/PNG/WebP/BMP/PDF) a procesar.',
                ],
            ],
            'required'   => ['ruta_archivo'],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $ruta = trim((string) ($arguments['ruta_archivo'] ?? ''));
        if ($ruta === '') {
            return ['success' => false, 'error' => 'Parámetro "ruta_archivo" es obligatorio.'];
        }

        $rutaReal = realpath($ruta);
        if ($rutaReal === false) {
            return ['success' => false, 'error' => 'El archivo no existe en el servidor: ' . $ruta];
        }
        if (!is_file($rutaReal) || !is_readable($rutaReal)) {
            return ['success' => false, 'error' => 'La ruta no es un archivo legible: ' . $rutaReal];
        }
        $tamano = @filesize($rutaReal);
        if ($tamano === false || $tamano > self::TAMANO_MAX_BYTES) {
            return ['success' => false, 'error' => 'El archivo supera el tamaño máximo permitido (20 MB).'];
        }
        $ext = strtolower(pathinfo($rutaReal, PATHINFO_EXTENSION));
        if (!in_array($ext, self::EXTENSIONES_VALIDAS, true)) {
            return [
                'success' => false,
                'error'   => 'Formato no soportado (' . $ext . '). Use: ' . implode(', ', self::EXTENSIONES_VALIDAS) . '.',
            ];
        }

        $baseUrl = rtrim(self::env('ARA_RETENCIONES_OCR_URL', 'http://192.168.4.217:5031'), '/');
        $timeout = max(5, (int) self::env('ARA_RETENCIONES_OCR_TIMEOUT', '60'));

        $mime = $this->detectarMime($rutaReal, $ext);
        $archivo = new \CURLFile($rutaReal, $mime, basename($rutaReal));

        $ch = curl_init();
        if ($ch === false) {
            return ['success' => false, 'error' => 'curl_init() falló: extensión cURL no disponible.'];
        }
        try {
            curl_setopt_array($ch, [
                CURLOPT_URL            => $baseUrl . '/api/escanear',
                CURLOPT_POST           => true,
                CURLOPT_POSTFIELDS     => ['archivo' => $archivo],
                CURLOPT_RETURNTRANSFER => true,
                CURLOPT_TIMEOUT        => $timeout,
                CURLOPT_CONNECTTIMEOUT => 8,
                CURLOPT_HTTPHEADER     => ['Accept: application/json'],
            ]);
            $raw = curl_exec($ch);
            $status = (int) curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
            if ($raw === false) {
                return ['success' => false, 'error' => 'Error cURL contra el OCR de retenciones: ' . curl_error($ch)];
            }
        } finally {
            curl_close($ch);
        }

        if ($status < 200 || $status >= 300) {
            return ['success' => false, 'error' => 'El OCR de retenciones respondió HTTP ' . $status . '.'];
        }

        $data = json_decode((string) $raw, true);
        if (!is_array($data) || !isset($data['resultados']) || !is_array($data['resultados'])) {
            return ['success' => false, 'error' => 'Respuesta inesperada del OCR de retenciones (sin "resultados").'];
        }

        $paginas = [];
        foreach ($data['resultados'] as $r) {
            if (!is_array($r)) {
                continue;
            }
            $campos = is_array($r['campos'] ?? null) ? $r['campos'] : [];
            $paginas[] = [
                'pagina'          => (int) ($r['pagina'] ?? 0),
                'archivo'         => (string) ($r['archivo'] ?? ''),
                'ok'              => (bool) ($r['ok'] ?? false),
                'motor'           => (string) ($r['motor'] ?? ''),
                'fecha'           => (string) ($campos['fecha'] ?? ''),
                'nro_comprobante' => (string) ($campos['nro_comprobante'] ?? ''),
                'cliente'         => self::aUtf8((string) ($campos['cliente'] ?? '')),
                'nro_factura'     => (string) ($campos['nro_factura'] ?? ''),
                'rif_cliente'     => (string) ($campos['rif_cliente'] ?? ''),
                'monto_retenido'  => (string) ($campos['monto_retenido'] ?? ''),
            ];
        }

        return [
            'success' => true,
            'data'    => [
                'archivo'       => basename($rutaReal),
                'total_paginas' => (int) ($data['total_paginas'] ?? count($paginas)),
                'paginas'       => $paginas,
            ],
            'card'    => $this->card(basename($rutaReal), $paginas),
        ];
    }

    /** Tarjeta de campos extraídos (CardBuilder), una sección por página. */
    private function card(string $nombreArchivo, array $paginas): string
    {
        if ($paginas === []) {
            return CardBuilder::iniciar('⚠️', 'OCR SIN RESULTADOS')
                ->linea('El OCR de retenciones no devolvió ninguna página procesada para "' . $nombreArchivo . '".')
                ->footer('Fuente: OCR retenciones')
                ->tarjeta();
        }
        $card = CardBuilder::iniciar('🧾', 'COMPROBANTE DE RETENCIÓN — ' . $nombreArchivo)
            ->campo('PÁGINAS', count($paginas), 'numero');
        foreach ($paginas as $p) {
            $card->seccion('Página ' . $p['pagina'] . ($p['ok'] ? '' : ' (⚠️ no procesada)'));
            if (!$p['ok']) {
                $card->linea('No se pudo extraer texto de esta página.');
                continue;
            }
            $card->campo('FECHA', $p['fecha'] !== '' ? $p['fecha'] : 'N/D');
            $card->campo('N° COMPROBANTE', $p['nro_comprobante'] !== '' ? $p['nro_comprobante'] : 'N/D');
            $card->campo('CLIENTE', $p['cliente'] !== '' ? $p['cliente'] : 'N/D');
            $card->campo('N° FACTURA', $p['nro_factura'] !== '' ? $p['nro_factura'] : 'N/D');
            $card->campo('RIF CLIENTE', $p['rif_cliente'] !== '' ? $p['rif_cliente'] : 'N/D');
            $card->campo('MONTO RETENIDO', $p['monto_retenido'] !== '' ? $p['monto_retenido'] : 'N/D');
        }
        return $card
            ->footer('Fuente: OCR retenciones (' . ($paginas[0]['motor'] ?? '') . ')')
            ->tarjeta();
    }

    private function detectarMime(string $ruta, string $ext): string
    {
        if (function_exists('finfo_open')) {
            $finfo = finfo_open(FILEINFO_MIME_TYPE);
            if ($finfo !== false) {
                $mime = finfo_file($finfo, $ruta);
                finfo_close($finfo);
                if (is_string($mime) && $mime !== '') {
                    return $mime;
                }
            }
        }
        $porExtension = [
            'png'  => 'image/png',
            'jpg'  => 'image/jpeg',
            'jpeg' => 'image/jpeg',
            'webp' => 'image/webp',
            'bmp'  => 'image/bmp',
            'pdf'  => 'application/pdf',
        ];
        return $porExtension[$ext] ?? 'application/octet-stream';
    }

    public function getDefinition(): array
    {
        return [
            'name'        => $this->getName(),
            'description' => $this->getDescription(),
            'parameters'  => $this->getParameters(),
        ];
    }

    private static function env(string $clave, string $default): string
    {
        $valor = getenv($clave);
        if (is_string($valor) && trim($valor) !== '') {
            return trim($valor);
        }
        return $default;
    }

    private static function aUtf8(string $texto): string
    {
        $limpio = trim($texto);
        if ($limpio === '' || mb_check_encoding($limpio, 'UTF-8')) {
            return $limpio;
        }
        $convertido = mb_convert_encoding($limpio, 'UTF-8', 'CP1252');
        return is_string($convertido) ? $convertido : $limpio;
    }
}
