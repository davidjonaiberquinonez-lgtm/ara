<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Auditoria;

use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;

require_once __DIR__ . '/../Common/CardBuilder.php';

/**
 * Detector de malsurtido a partir del LOG_REPORTE "Bulto Cerrado".
 *
 * Analiza el texto del log (p.ej. LOG_REPORTE_BULTO_CERRADO) comparando por
 * ítem lo SOLICITADO contra lo ESCANEADO al surtir el bulto cerrado. Un ítem
 * con diferencia ≠ 0 queda marcado como malsurtido (FALTANTE o EXCEDENTE).
 *
 * Formatos de línea aceptados (por ítem):
 *   <co_art>|<solicitado>|<escaneado>
 *   <co_art> <solicitado> <escaneado> <descripcion>
 *   co_art;sol;esc
 * También tolera encabezados (solicitado/escaneado/Bulto Cerrado) y líneas
 * sueltas; lo que no cuadra con el patrón se ignora sin romper el análisis.
 *
 * Contrato de retorno (SIEMPRE array serializable, sin excepciones):
 *   ['success' => true,  'data' => [...]]      → éxito
 *   ['success' => false, 'error' => 'mensaje'] → error controlado
 *
 * Uso:
 *   $registry->registerTool(new DetectorMalsurtidoLogTool());
 */
final class DetectorMalsurtidoLogTool implements AgentToolInterface
{
    public function getName(): string
    {
        return 'detector_malsurtido_log';
    }

    public function getDescription(): string
    {
        return 'Detecta malsurtido en el LOG_REPORTE "Bulto Cerrado": compara por '
             . 'item lo SOLICITADO contra lo ESCANEADO y clasifica FALTANTE, '
             . 'EXCEDENTE u OK. Parametros: log (texto del reporte, '
             . 'obligatorio) y umbral (diferencia minima para marcar, default 0).';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'log' => [
                    'type'        => 'string',
                    'description' => 'Texto del LOG_REPORTE "Bulto Cerrado". Una linea por item: co_art|solicitado|escaneado.',
                ],
                'umbral' => [
                    'type'        => 'number',
                    'description' => 'Diferencia minima para marcar malsurtido (default 0).',
                ],
            ],
            'required' => ['log'],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $log = (string) ($arguments['log'] ?? '');
        $log = str_replace(["\r\n", "\r"], "\n", $log);
        if (trim($log) === '') {
            return ['success' => false, 'error' => 'Parámetro "log" es obligatorio (contenido del LOG_REPORTE).'];
        }
        $umbral = (float) ($arguments['umbral'] ?? 0);

        $lineas = array_values(array_filter(array_map('trim', explode("\n", $log)), static fn ($l) => $l !== ''));

        $renglones = [];
        $descartados = 0;
        $re = '/^([A-Za-z0-9_.\-]{1,30})[\s|;](\d+(?:[.,]\d+)?)[\s|;](\d+(?:[.,]\d+)?)(?:[\s|;](.+))?$/';
        foreach ($lineas as $linea) {
            if (preg_match('/^(solicitado|escaneado|bulto|item|articulo|cant|cantidad)\b/i', $linea)) {
                continue;
            }
            $normalizada = preg_replace('/\t+/', '|', $linea) ?? $linea;
            $normalizada = preg_replace('/\s{2,}/', '|', $normalizada) ?? $normalizada;
            if (preg_match($re, $normalizada, $m)) {
                $solicitado = self::numero($m[2]);
                $escaneado  = self::numero($m[3]);
                $renglones[] = [
                    'co_art'      => self::aUtf8($m[1]),
                    'solicitado'  => $solicitado,
                    'escaneado'   => $escaneado,
                    'diferencia'  => $escaneado - $solicitado,
                    'descripcion' => isset($m[4]) ? self::aUtf8(trim($m[4])) : '',
                ];
            } else {
                $descartados++;
            }
        }
        if ($renglones === []) {
            return [
                'success' => false,
                'error'   => 'No se reconocio ningun renglon de item en el log. Formato esperado por linea: co_art|solicitado|escaneado.',
            ];
        }

        $discrepancias = [];
        foreach ($renglones as $r) {
            if (abs($r['diferencia']) > $umbral) {
                $discrepancias[] = [
                    'co_art'      => $r['co_art'],
                    'solicitado'  => $r['solicitado'],
                    'escaneado'   => $r['escaneado'],
                    'diferencia'  => $r['diferencia'],
                    'tipo'        => $r['diferencia'] > 0 ? 'EXCEDENTE' : 'FALTANTE',
                    'descripcion' => $r['descripcion'],
                ];
            }
        }

        $resumen = [
            'FALTANTE' => 0,
            'EXCEDENTE' => 0,
        ];
        foreach ($discrepancias as $d) {
            $resumen[$d['tipo']]++;
        }

        return [
            'success' => true,
            'data'    => [
                'fuente'           => 'LOG_REPORTE_BULTO_CERRADO',
                'total_renglones'  => count($renglones),
                'renglones_descartados' => $descartados,
                'total_discrepancias'   => count($discrepancias),
                'resumen'          => $resumen,
                'discrepancias'    => $discrepancias,
                'umbral_aplicado'  => $umbral,
            ],
            'card'    => $this->cardDetector(count($renglones), $descartados, $resumen, $discrepancias, $umbral),
        ];
    }

    /** Tarjeta estándar v4.13 del detector (CardBuilder). */
    private function cardDetector(int $totalRenglones, int $descartados, array $resumen, array $discrepancias, float $umbral): string
    {
        $cb = CardBuilder::iniciar('⚠️', 'DETECTOR DE MALSURTIDO')
            ->campo('RENGLONES ANALIZADOS', $totalRenglones, 'numero')
            ->campo('DESCARTADOS', $descartados, 'numero')
            ->campo('DISCREPANCIAS', count($discrepancias), 'numero')
            ->campo('UMBRAL', $umbral, 'decimal')
            ->seccion('Resumen')
            ->campo('FALTANTES', (int) ($resumen['FALTANTE'] ?? 0), 'numero')
            ->campo('EXCEDENTES', (int) ($resumen['EXCEDENTE'] ?? 0), 'numero');
        if ($discrepancias !== []) {
            $cb->seccion('Detalle (top 6)');
            foreach (array_slice($discrepancias, 0, 6) as $d) {
                $cb->campo(
                    (string) $d['co_art'],
                    $d['tipo'] . ' · sol ' . $d['solicitado'] . ' → esc ' . $d['escaneado'] . ' (dif ' . $d['diferencia'] . ')'
                );
            }
        }
        return $cb->footer('Fuente: LOG_REPORTE_BULTO_CERRADO')->tarjeta();
    }

    public function getDefinition(): array
    {
        return [
            'name'        => $this->getName(),
            'description' => $this->getDescription(),
            'parameters'  => $this->getParameters(),
        ];
    }

    private function numero(string $valor): float
    {
        return (float) str_replace(',', '.', trim($valor));
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
