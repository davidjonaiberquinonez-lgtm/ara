<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Almacen;

use App\Services\ConnectionWrapper;
use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;
use App\Services\NvidiaBrain\Tools\Common\FlaskApiTrait;
use PDOException;

require_once __DIR__ . '/../Common/FlaskApiTrait.php';
require_once __DIR__ . '/../Common/CardBuilder.php';
require_once __DIR__ . '/ProfitStockHelper.php';

/**
 * Herramienta "cambios_ubicacion_articulo": ubicación ACTUAL de un artículo
 * en Profit (campo7 + stock por sub-almacén) + su historial de cambios.
 *
 * Fuentes:
 *  - Profit SQL Server (art.campo7, st_almac.stock_act en los sub-almacenes
 *    01 DESPACHO S/C y 04 DESPACHO BQTO): ubicación y stock REALES en este
 *    momento — best-effort, si Profit no responde la tarjeta igual muestra
 *    el historial.
 *  - SQLite de ARA_Brain, tabla `reportes_ubicacion` (usuario, desde, hacia,
 *    fecha, procesado_profit): vía el endpoint GET /api/reportes/trazabilidad
 *    con filtro co_art — QUIÉN movió el artículo y CUÁNDO, no dónde está
 *    ahora (por eso antes NO alcanzaba con solo esto: mostraba a dónde se
 *    movió la última vez, pero no si esa ubicación sigue siendo la vigente
 *    en Profit ni si el artículo tiene stock real en cada sede).
 *
 * campo7 es UN solo valor por artículo en el esquema real de Profit (no hay
 * una columna separada por sub-almacén) — se muestra junto al stock de cada
 * sub-almacén para que el operador vea de una si el artículo está presente
 * en S/C, en BQTO, o en ambos.
 */
final class CambioUbicacionArticuloTool implements AgentToolInterface
{
    use FlaskApiTrait;

    public function __construct(string $baseUrl = '', int $timeoutS = 10)
    {
        $this->initFlaskApi($baseUrl, $timeoutS);
    }

    public function getName(): string
    {
        return 'cambios_ubicacion_articulo';
    }

    public function getDescription(): string
    {
        return 'Devuelve el historial de cambios de ubicación física en '
             . 'almacén de UN artículo puntual por su código (quién lo '
             . 'movió, ubicación anterior, ubicación nueva, fecha, si ya '
             . 'quedó reflejado en Profit). Parámetros: co_art (código del '
             . 'artículo, requerido), fecha_inicio y fecha_fin (YYYY-MM-DD, '
             . 'opcionales).';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'co_art' => [
                    'type'        => 'string',
                    'description' => 'Código del artículo a consultar (requerido).',
                ],
                'fecha_inicio' => [
                    'type'        => 'string',
                    'description' => 'Fecha inicial del rango (YYYY-MM-DD), opcional.',
                ],
                'fecha_fin' => [
                    'type'        => 'string',
                    'description' => 'Fecha final del rango (YYYY-MM-DD), opcional.',
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

        $ubicacionProfit = $this->resolverUbicacionProfit($coArt);

        $query = ['co_art' => $coArt, 'es_admin' => 'true'];
        if ($fechaInicio !== '') {
            $query['fecha_inicio'] = $fechaInicio;
        }
        if ($fechaFin !== '') {
            $query['fecha_fin'] = $fechaFin;
        }

        try {
            $data = $this->flaskGetJson('/api/reportes/trazabilidad?' . http_build_query($query));
        } catch (\Throwable $e) {
            return ['ok' => false, 'error' => 'No se pudo consultar el historial de ubicaciones: ' . $e->getMessage()];
        }

        if (($data['status'] ?? '') === 'error') {
            return ['ok' => false, 'error' => (string) ($data['mensaje'] ?? 'Error del servidor.')];
        }

        $cambios = is_array($data['data'] ?? null) ? $data['data'] : [];

        if ($cambios === []) {
            $sinDatos = "No hay cambios de ubicación registrados para el artículo {$coArt}"
                      . ($fechaInicio !== '' || $fechaFin !== '' ? ' en el rango de fechas indicado.' : '.');
            $cbVacio = CardBuilder::iniciar('📍', 'CAMBIOS DE UBICACIÓN ' . $coArt)
                ->campo('CAMBIOS', 0, 'numero');
            $this->agregarSeccionProfit($cbVacio, $ubicacionProfit);
            $cbVacio->linea($sinDatos)
                ->footer('Fuente: reportes_ubicacion (SQLite)' . ($ubicacionProfit !== null ? ' + Profit (NOLOCK)' : ''));
            return [
                'ok'                => true,
                'co_art'            => $coArt,
                'total'             => 0,
                'cambios'           => [],
                'ubicacion_profit'  => $ubicacionProfit,
                'respuesta'         => $sinDatos,
                'card'              => $cbVacio->tarjeta(),
            ];
        }

        $cb = CardBuilder::iniciar('📍', 'CAMBIOS DE UBICACIÓN ' . $coArt)
            ->campo('TOTAL', count($cambios), 'numero');
        $this->agregarSeccionProfit($cb, $ubicacionProfit);
        $cb->seccion('Historial (más reciente primero)');
        foreach (array_slice($cambios, 0, 10) as $c) {
            $fecha = (string) ($c['fecha'] ?? '');
            $desde = (string) ($c['desde'] ?? 'N/D');
            $hacia = (string) ($c['hacia'] ?? 'N/D');
            $usuario = (string) ($c['usuario'] ?? 'N/D');
            $enProfit = (int) ($c['procesado_profit'] ?? 0) === 1 ? '✅ en Profit' : '⏳ pendiente Profit';
            $cb->linea("{$fecha} · {$desde} → {$hacia} · {$usuario} · {$enProfit}");
        }
        if (count($cambios) > 10) {
            $cb->linea('+ ' . (count($cambios) - 10) . ' cambio(s) más (usa fecha_inicio/fecha_fin para acotar).');
        }
        $cb->footer('Fuente: reportes_ubicacion (SQLite)' . ($ubicacionProfit !== null ? ' + Profit (NOLOCK)' : ''));

        return [
            'ok'               => true,
            'co_art'           => $coArt,
            'total'            => count($cambios),
            'cambios'          => $cambios,
            'ubicacion_profit' => $ubicacionProfit,
            'card'             => $cb->tarjeta(),
        ];
    }

    /**
     * Ubicación ACTUAL (campo7) + stock por sub-almacén en Profit, para los
     * dos almacenes de picking: 01 (DESPACHO S/C) y 04 (DESPACHO BQTO).
     * Best-effort: null si Profit no responde — nunca rompe la tarjeta.
     *
     * @return array{campo7:string, stock_sc:float, stock_bqto:float}|null
     */
    private function resolverUbicacionProfit(string $coArt): ?array
    {
        try {
            $wrapper = new ConnectionWrapper();
            $maestro = ProfitStockHelper::maestros($wrapper, [$coArt]);
            $fila = $maestro[strtoupper($coArt)] ?? null;
            $campo7 = $fila !== null ? trim((string) ($fila['campo7'] ?? '')) : '';

            $stock = ProfitStockHelper::stockPorSubAlmacen(
                $wrapper,
                [$coArt],
                [ProfitStockHelper::SUB_DESPACHO_SC, ProfitStockHelper::SUB_DESPACHO_BQTO]
            );
            $stockArt = $stock[strtoupper($coArt)] ?? [];

            return [
                'campo7'     => $campo7,
                'stock_sc'   => (float) ($stockArt[ProfitStockHelper::SUB_DESPACHO_SC] ?? 0),
                'stock_bqto' => (float) ($stockArt[ProfitStockHelper::SUB_DESPACHO_BQTO] ?? 0),
            ];
        } catch (PDOException $e) {
            error_log('[CambioUbicacionArticuloTool] Profit no consultable: ' . $e->getMessage());
            return null;
        } catch (\Throwable $e) {
            error_log('[CambioUbicacionArticuloTool] Profit no consultable: ' . $e->getMessage());
            return null;
        }
    }

    /** Agrega la sección "Ubicación actual (Profit)" a la tarjeta, si hay datos. */
    private function agregarSeccionProfit(CardBuilder $cb, ?array $ubicacionProfit): void
    {
        if ($ubicacionProfit === null) {
            return;
        }
        $cb->seccion('Ubicación actual (Profit)')
            ->campo('CAMPO7', $ubicacionProfit['campo7'] !== '' ? $ubicacionProfit['campo7'] : 'SIN ASIGNAR')
            ->campo('STOCK DESPACHO S/C (01)', $ubicacionProfit['stock_sc'], 'numero')
            ->campo('STOCK DESPACHO BQTO (04)', $ubicacionProfit['stock_bqto'], 'numero');
    }
}
