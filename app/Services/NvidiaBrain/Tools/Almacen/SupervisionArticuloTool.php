<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Almacen;

use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;
use App\Services\NvidiaBrain\Tools\Common\FlaskApiTrait;

require_once __DIR__ . '/../Common/FlaskApiTrait.php';
require_once __DIR__ . '/../Common/CardBuilder.php';

/**
 * Herramienta "supervision_articulo": línea de tiempo de notas/movimientos
 * de UN artículo puntual (por código), con rango de fechas definible.
 *
 * Fuente: SQLite de ARA_Brain, tabla `movimientos_preparador` — vía el
 * endpoint ya existente GET /api/trazabilidad/movimientos (co_art,
 * fecha_inicio, fecha_fin). Ninguna tool PHP se conecta directo a ese
 * SQLite; el patrón establecido (ConsultarNotaTool) es consumir el API
 * Flask local por HTTP (ver FlaskApiTrait).
 *
 * Solo lectura, sin excepciones sin capturar: SIEMPRE devuelve
 * ['ok'=>bool, ...].
 */
final class SupervisionArticuloTool implements AgentToolInterface
{
    use FlaskApiTrait;

    public function __construct(string $baseUrl = '', int $timeoutS = 10)
    {
        $this->initFlaskApi($baseUrl, $timeoutS);
    }

    public function getName(): string
    {
        return 'supervision_articulo';
    }

    public function getDescription(): string
    {
        return 'Devuelve la línea de tiempo de movimientos (notas, cantidades, '
             . 'origen/destino) de UN artículo puntual por su código, en un '
             . 'rango de fechas. Uso típico: supervisar qué pasó con un '
             . 'producto específico (para qué notas se usó, cuánto se movió, '
             . 'quién lo movió) en un período dado. Parámetros: co_art '
             . '(código del artículo, requerido), fecha_inicio y fecha_fin '
             . '(YYYY-MM-DD, opcionales — sin ellas trae los últimos '
             . 'movimientos sin filtro de fecha), limite (opcional, tope de '
             . 'filas, default 50, máximo 200).';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'co_art' => [
                    'type'        => 'string',
                    'description' => 'Código del artículo a supervisar (requerido).',
                ],
                'fecha_inicio' => [
                    'type'        => 'string',
                    'description' => 'Fecha inicial del rango (YYYY-MM-DD), opcional.',
                ],
                'fecha_fin' => [
                    'type'        => 'string',
                    'description' => 'Fecha final del rango (YYYY-MM-DD), opcional.',
                ],
                'limite' => [
                    'type'        => 'integer',
                    'description' => 'Tope de movimientos a devolver (default 50, máximo 200).',
                ],
            ],
            'required' => ['co_art'],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $coArt = trim((string) ($arguments['co_art'] ?? ''));
        if ($coArt === '') {
            return ['ok' => false, 'error' => 'Falta el parámetro "co_art" (código del artículo).'];
        }
        $fechaInicio = trim((string) ($arguments['fecha_inicio'] ?? ''));
        $fechaFin = trim((string) ($arguments['fecha_fin'] ?? ''));
        $limite = (int) ($arguments['limite'] ?? 50);
        $limite = min(max($limite, 1), 200);

        $query = ['co_art' => $coArt, 'limite' => (string) $limite];
        if ($fechaInicio !== '') {
            $query['fecha_inicio'] = $fechaInicio;
        }
        if ($fechaFin !== '') {
            $query['fecha_fin'] = $fechaFin;
        }

        try {
            $data = $this->flaskGetJson('/api/trazabilidad/movimientos?' . http_build_query($query));
        } catch (\Throwable $e) {
            return ['ok' => false, 'error' => 'No se pudo consultar la trazabilidad: ' . $e->getMessage()];
        }

        $movimientos = is_array($data) ? $data : [];
        // El endpoint devuelve un array plano de filas (no envuelto en 'data').
        if (isset($movimientos['data']) && is_array($movimientos['data'])) {
            $movimientos = $movimientos['data'];
        }

        if ($movimientos === []) {
            $sinDatos = "No se encontraron movimientos registrados para el artículo {$coArt}"
                      . ($fechaInicio !== '' || $fechaFin !== '' ? ' en el rango de fechas indicado.' : '.');
            return [
                'ok'        => true,
                'co_art'    => $coArt,
                'total'     => 0,
                'timeline'  => [],
                'respuesta' => $sinDatos,
                'card'      => CardBuilder::iniciar('📊', 'SUPERVISIÓN DE ARTÍCULO ' . $coArt)
                    ->campo('RANGO', $fechaInicio !== '' || $fechaFin !== ''
                        ? ($fechaInicio ?: '...') . ' → ' . ($fechaFin ?: '...') : 'sin filtro de fecha')
                    ->campo('MOVIMIENTOS', 0, 'numero')
                    ->linea($sinDatos)
                    ->footer('Fuente: movimientos_preparador (SQLite)')
                    ->tarjeta(),
            ];
        }

        $cb = CardBuilder::iniciar('📊', 'SUPERVISIÓN DE ARTÍCULO ' . $coArt)
            ->campo('RANGO', $fechaInicio !== '' || $fechaFin !== ''
                ? ($fechaInicio ?: '...') . ' → ' . ($fechaFin ?: '...') : 'sin filtro de fecha')
            ->campo('MOVIMIENTOS', count($movimientos), 'numero')
            ->seccion('Línea de tiempo (más reciente primero)');
        foreach (array_slice($movimientos, 0, 10) as $m) {
            $ts = (string) ($m['timestamp'] ?? '');
            $accion = strtoupper((string) ($m['accion'] ?? ''));
            $cant = (string) ($m['cantidad'] ?? '');
            $origen = (string) ($m['origen'] ?? '');
            $destino = (string) ($m['destino'] ?? '');
            $usuario = (string) ($m['usuario'] ?? 'N/D');
            $mov = trim($origen) !== '' || trim($destino) !== '' ? " ({$origen} → {$destino})" : '';
            $cb->linea("{$ts} · {$accion} · {$cant}{$mov} · {$usuario}");
        }
        if (count($movimientos) > 10) {
            $cb->linea('+ ' . (count($movimientos) - 10) . ' movimiento(s) más (usa fecha_inicio/fecha_fin para acotar).');
        }
        $cb->footer('Fuente: movimientos_preparador (SQLite)');

        return [
            'ok'       => true,
            'co_art'   => $coArt,
            'total'    => count($movimientos),
            'timeline' => $movimientos,
            'card'     => $cb->tarjeta(),
        ];
    }
}
