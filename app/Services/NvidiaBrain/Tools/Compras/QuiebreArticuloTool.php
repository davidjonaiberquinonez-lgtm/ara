<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Compras;

use App\Services\ConnectionWrapper;
use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Almacen\ProfitStockHelper;
use PDOException;

require_once __DIR__ . '/../Almacen/ProfitStockHelper.php';

/**
 * Herramienta "quiebre_articulo": ¿está en quiebre UN artículo puntual?
 * Complementa a reporte_quiebres_compras (que es un reporte masivo TOP-N sin
 * filtro por código) reutilizando el mismo ProfitStockHelper: consulta
 * directa a `art` por co_art + desglose de stock por sub-almacén + rotación
 * de venta, para responder "¿tengo quiebre del artículo X?" sin traer una
 * lista completa.
 *
 * Solo lectura (SQL Server Profit, WITH NOLOCK vía ConnectionWrapper).
 */
final class QuiebreArticuloTool implements AgentToolInterface
{
    public function getName(): string
    {
        return 'quiebre_articulo';
    }

    public function getDescription(): string
    {
        return 'Verifica si UN artículo puntual (por código) está en quiebre '
             . 'de stock: stock total, desglose por sub-almacén (despacho/'
             . 'depósito de San Cristóbal y Barquisimeto) y su rotación de '
             . 'venta reciente. A diferencia de reporte_quiebres_compras '
             . '(reporte masivo), esta consulta un solo código. Parámetro: '
             . 'co_art (requerido).';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'co_art' => [
                    'type'        => 'string',
                    'description' => 'Código del artículo a verificar (requerido).',
                ],
            ],
            'required' => ['co_art'],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $coArt = strtoupper(trim((string) ($arguments['co_art'] ?? '')));
        if ($coArt === '') {
            return ['ok' => false, 'error' => 'Falta el parámetro "co_art" (código del artículo).'];
        }

        try {
            $w = new ConnectionWrapper();
            $maestros = ProfitStockHelper::maestros($w, [$coArt]);
        } catch (PDOException $e) {
            return ['ok' => false, 'error' => 'No se pudo consultar Profit: ' . ProfitStockHelper::aUtf8($e->getMessage())];
        }

        if (!isset($maestros[$coArt])) {
            return ['ok' => false, 'error' => "El artículo {$coArt} no existe en el maestro de Profit (art)."];
        }
        $art = $maestros[$coArt];

        $stockPorSub = ProfitStockHelper::stockPorSubAlmacen($w, [$coArt]);
        $rotacion = ProfitStockHelper::rotacion($w, [$coArt]);

        $stockTotal = (float) ($art['stock_act'] ?? 0);
        $enQuiebre = $stockTotal <= 0 && trim((string) ($art['anulado'] ?? '')) === '0';
        $nombre = ProfitStockHelper::aUtf8((string) ($art['art_des'] ?? ''));

        return [
            'ok'                => true,
            'co_art'            => $coArt,
            'nombre'            => $nombre,
            'anulado'           => trim((string) ($art['anulado'] ?? '')) !== '0',
            'en_quiebre'        => $enQuiebre,
            'stock_total'       => $stockTotal,
            'stock_por_sub_almacen' => $stockPorSub[$coArt] ?? [],
            'rotacion_venta'    => $rotacion[$coArt] ?? 0,
            'respuesta'         => $enQuiebre
                ? "SÍ, {$coArt} ({$nombre}) está en quiebre de stock (stock total: {$stockTotal})."
                : "No está en quiebre: {$coArt} ({$nombre}) tiene {$stockTotal} unidades de stock total.",
        ];
    }
}
