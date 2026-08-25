<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Auditoria;

use App\Services\ConnectionWrapper;
use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Almacen\ProfitStockHelper;
use PDOException;

require_once __DIR__ . '/../Almacen/ProfitStockHelper.php';

/**
 * Herramienta "auditoria_articulo": auditoría de UN artículo puntual (por
 * código) — compara su existencia entre TODOS los sub-almacenes relevantes
 * (despacho/depósito de San Cristóbal y Barquisimeto), a diferencia de
 * discrepancia_traslados (reporte masivo TOP-N solo depósito-vs-depósito).
 *
 * Reutiliza ProfitStockHelper::stockPorSubAlmacen, mismo namespace que
 * DiscrepanciaTrasladosTool. Solo lectura (SQL Server Profit, NOLOCK).
 */
final class AuditoriaArticuloTool implements AgentToolInterface
{
    public function getName(): string
    {
        return 'auditoria_articulo';
    }

    public function getDescription(): string
    {
        return 'Auditoría de UN artículo puntual por código: compara su stock '
             . 'entre los 4 sub-almacenes (despacho y depósito de San '
             . 'Cristóbal y Barquisimeto) y marca si hay discrepancia entre '
             . 'depósitos de ambas sedes (posible traslado pendiente o '
             . 'registro duplicado). A diferencia de discrepancia_traslados '
             . '(reporte masivo), esta audita un solo código. Parámetro: '
             . 'co_art (requerido).';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'co_art' => [
                    'type'        => 'string',
                    'description' => 'Código del artículo a auditar (requerido).',
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

        $stockPorSub = ProfitStockHelper::stockPorSubAlmacen($w, [$coArt])[$coArt] ?? [];
        $despachoSc  = (float) ($stockPorSub[ProfitStockHelper::SUB_DESPACHO_SC] ?? 0);
        $depositoSc  = (float) ($stockPorSub[ProfitStockHelper::SUB_DEPOSITO_SC] ?? 0);
        $despachoBqto = (float) ($stockPorSub[ProfitStockHelper::SUB_DESPACHO_BQTO] ?? 0);
        $depositoBqto = (float) ($stockPorSub[ProfitStockHelper::SUB_DEPOSITO_BQTO] ?? 0);
        $diferenciaDepositos = $depositoSc - $depositoBqto;
        $nombre = ProfitStockHelper::aUtf8((string) ($art['art_des'] ?? ''));

        return [
            'ok'     => true,
            'co_art' => $coArt,
            'nombre' => $nombre,
            'stock_total'    => (float) ($art['stock_act'] ?? 0),
            'despacho_sc'    => $despachoSc,
            'deposito_sc'    => $depositoSc,
            'despacho_bqto'  => $despachoBqto,
            'deposito_bqto'  => $depositoBqto,
            'diferencia_depositos_sc_menos_bqto' => $diferenciaDepositos,
            'discrepancia_detectada' => abs($diferenciaDepositos) > 0,
            'respuesta' => abs($diferenciaDepositos) > 0
                ? "{$coArt} ({$nombre}): discrepancia de {$diferenciaDepositos} entre depósito S/C ({$depositoSc}) y depósito BQTO ({$depositoBqto})."
                : "{$coArt} ({$nombre}): depósitos S/C y BQTO cuadran ({$depositoSc} unidades cada uno).",
        ];
    }
}
