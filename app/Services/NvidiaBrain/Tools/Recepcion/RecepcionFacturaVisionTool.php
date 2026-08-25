<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Recepcion;

use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;

require_once __DIR__ . '/../Common/CardBuilder.php';

/**
 * Visión de factura en Recepción: convierte el texto obtenido por OCR/visión
 * de una factura de proveedor en un registro estructurado y (opcionalmente)
 * en un PDF estandarizado de recepción.
 *
 * Sin librerías externas (sin TCPDF/FPDF/gd): el PDF se genera en PHP puro
 * (sintaxis PDF 1.4, Helvetica, A4) y se devuelve en base64. Solo ASCII:
 * los campos con caracteres especiales se transliteran.
 *
 * Formato de línea aceptado (una por ítem):
 *   <codigo>|<cantidad>|<precio>|<total>|<descripcion>
 * También acepta tabuladores o varios espacios como separador.
 *
 * Contrato de retorno (SIEMPRE array serializable, sin excepciones):
 *   ['success' => true,  'data' => [...]]      → éxito
 *   ['success' => false, 'error' => 'mensaje'] → error controlado
 *
 * Uso:
 *   $registry->registerTool(new RecepcionFacturaVisionTool());
 */
final class RecepcionFacturaVisionTool implements AgentToolInterface
{
    public function getName(): string
    {
        return 'recepcion_factura_vision';
    }

    public function getDescription(): string
    {
        return 'Procesa la vista/OCR de una factura de proveedor en Recepcion: '
             . 'extrae numero, proveedor, fecha, total y renglones '
             . '(codigo|cantidad|precio|total|descripcion) y opcionalmente '
             . 'genera el PDF estandarizado de recepcion (base64). Parametros: '
             . 'texto (obligatorio) y generar_pdf (bool, default false).';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'texto' => [
                    'type'        => 'string',
                    'description' => 'Texto completo obtenido por OCR/vision de la factura. Una linea por item: codigo|cantidad|precio|total|descripcion.',
                ],
                'generar_pdf' => [
                    'type'        => 'boolean',
                    'description' => 'Si es true, ademas devuelve el PDF estandarizado en base64.',
                ],
            ],
            'required' => ['texto'],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $texto = (string) ($arguments['texto'] ?? '');
        $texto = str_replace(["\r\n", "\r"], "\n", $texto);
        if (trim($texto) === '') {
            return ['success' => false, 'error' => 'Parámetro "texto" es obligatorio (OCR/visión de la factura).'];
        }
        $generarPdf = (bool) ($arguments['generar_pdf'] ?? false);

        $lineas = array_values(array_filter(array_map('trim', explode("\n", $texto)), static fn ($l) => $l !== ''));

        $campos = [
            'factura'   => $this->extraer($lineas, ['factura', 'nro', 'numero', 'num']),
            'proveedor' => $this->extraer($lineas, ['proveedor', 'rif', 'razon']),
            'fecha'     => $this->extraer($lineas, ['fecha', 'date']),
            'total'     => $this->extraer($lineas, ['total', 'monto', 'importe']),
        ];

        $items = [];
        $re = '/^(\S+)[\s|]+(\d+(?:[.,]\d+)?)[\s|]+(\d+(?:[.,]\d+)?)[\s|]+(\d+(?:[.,]\d+)?)(?:[\s|]+(.+))?$/';
        foreach ($lineas as $linea) {
            if (preg_match('/^(factura|proveedor|fecha|total|rif|nro)\b/i', $linea)) {
                continue;
            }
            $lineaNormalizada = preg_replace('/\t+/', '|', $linea) ?? $linea;
            $lineaNormalizada = preg_replace('/\s{2,}/', '|', $lineaNormalizada) ?? $lineaNormalizada;
            if (preg_match($re, $lineaNormalizada, $m)) {
                $items[] = [
                    'codigo'      => self::aUtf8($m[1]),
                    'cantidad'    => self::numero($m[2]),
                    'precio'      => self::numero($m[3]),
                    'total'       => self::numero($m[4]),
                    'descripcion' => isset($m[5]) ? self::aUtf8(trim($m[5])) : '',
                ];
            }
        }

        $data = [
            'factura'        => self::aUtf8($campos['factura']),
            'proveedor'      => self::aUtf8($campos['proveedor']),
            'fecha'          => self::aUtf8($campos['fecha']),
            'total'          => self::numero($campos['total']),
            'total_por_items'=> array_sum(array_column($items, 'total')),
            'items'          => $items,
            'total_items'    => count($items),
            'fuente'         => 'ocr_vision_local',
        ];

        if ($generarPdf) {
            $pdf = $this->pdfEstandarizado($data);
            $data['pdf_base64'] = $pdf === '' ? null : base64_encode($pdf);
        }

        $card = CardBuilder::iniciar('📄', 'FACTURA DE RECEPCIÓN (VISIÓN)')
            ->campo('FACTURA', (string) ($data['factura'] ?: 'N/D'))
            ->campo('PROVEEDOR', (string) ($data['proveedor'] ?: 'N/D'))
            ->campo('FECHA', (string) ($data['fecha'] ?: 'N/D'))
            ->campo('TOTAL DETECTADO', (float) $data['total'], 'moneda')
            ->campo('TOTAL POR ÍTEMS', (float) $data['total_por_items'], 'moneda')
            ->campo('RENGLONES', (int) $data['total_items'], 'numero');
        if ($items !== []) {
            $card->seccion('Renglones (top 6)');
            foreach (array_slice($items, 0, 6) as $item) {
                $card->campo(
                    (string) $item['codigo'],
                    'x' . $item['cantidad'] . ' = Bs ' . number_format($item['total'], 2, ',', '.')
                );
            }
        }
        if (isset($data['pdf_base64']) && $data['pdf_base64'] !== null) {
            $card->linea('PDF de recepción generado (base64, ' . round(strlen((string) $data['pdf_base64']) / 1024) . ' KB)');
        }
        $card->footer('Fuente: ocr_vision_local');

        return ['success' => true, 'data' => $data, 'card' => $card->tarjeta()];
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
     * @param array<int,string> $lineas
     * @param array<int,string> $etiquetas
     */
    private function extraer(array $lineas, array $etiquetas): string
    {
        $patron = '/^\s*(?:' . implode('|', $etiquetas) . ')\s*(?:[:#=-]|N\.?o?\.?)\s*(.+)$/i';
        foreach ($lineas as $linea) {
            if (preg_match($patron, $linea, $m)) {
                return trim($m[1]);
            }
        }
        return '';
    }

    private function numero(string $valor): float
    {
        return (float) str_replace(',', '.', trim($valor));
    }

    private function pdfEstandarizado(array $data): string
    {
        $lineas = [
            'FACTURA DE RECEPCION - ARA SYNC',
            'Factura: ' . self::ascii((string) ($data['factura'] ?? '')),
            'Proveedor: ' . self::ascii((string) ($data['proveedor'] ?? '')),
            'Fecha: ' . self::ascii((string) ($data['fecha'] ?? '')),
            'Total: ' . number_format((float) ($data['total'] ?? 0), 2, '.', ''),
            '',
        ];
        foreach ($data['items'] as $item) {
            $lineas[] = sprintf(
                '%s  x%s  %s  = %s  %s',
                self::ascii((string) $item['codigo']),
                $item['cantidad'],
                $item['precio'],
                $item['total'],
                self::ascii((string) $item['descripcion'])
            );
        }

        $contenido = '';
        $y = 800.0;
        foreach ($lineas as $texto) {
            $contenido .= sprintf(
                'BT /F1 10 Tf %s %s Td (%s) Tj ET' . "\n",
                '50',
                number_format($y, 2, '.', ''),
                self::escapePdf($texto)
            );
            $y -= 16.0;
        }

        return "%PDF-1.4\n"
            . "1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            . "2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            . "3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
            . "4 0 obj<</Length " . strlen($contenido) . ">>stream\n"
            . $contenido . "endstream\nendobj\n"
            . "5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
            . "trailer<</Root 1 0 R/Size 6>>\n"
            . "%%EOF";
    }

    private static function ascii(string $texto): string
    {
        $limpio = self::aUtf8($texto);
        $translit = iconv('UTF-8', 'ASCII//TRANSLIT//IGNORE', $limpio);
        return is_string($translit) ? $translit : preg_replace('/[^\x20-\x7E]/', ' ', $limpio) ?? ' ';
    }

    private static function escapePdf(string $texto): string
    {
        return str_replace(['\\', '(', ')'], ['\\\\', '\\(', '\\)'], $texto);
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
}
