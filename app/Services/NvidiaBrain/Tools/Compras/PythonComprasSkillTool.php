<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Compras;

use App\Services\ConnectionWrapper;
use App\Services\NvidiaBrain\Adapters\PythonSkillExecutor;
use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;
use PDOException;

require_once __DIR__ . '/../../Adapters/PythonSkillExecutor.php';
require_once __DIR__ . '/../Common/CardBuilder.php';

/**
 * Activador/superconector PHP de la Skill de Python del departamento
 * Compras (C:\ARA_PROYECT\skills\compras\quiebres_compras.py), v4.2.
 *
 * Analiza los quiebres de inventario (stock en 0) y clasifica las FALLAS
 * por criticidad: Tier 1 (Top 20% de demanda = acción rápida de compra) y
 * Tier 2 (menor demanda), más la matriz de alertas de stock 0 por SKU y el
 * resumen por laboratorio.
 *
 * Los parámetros viajan en JSON por stdin del subproceso Python y la
 * respuesta llega estandarizada: {"success": true, "data": ...}.
 *
 * Uso:
 *   $registry->registerTool(new PythonComprasSkillTool());
 */
final class PythonComprasSkillTool implements AgentToolInterface
{
    public function getName(): string
    {
        return 'python_compras_skill';
    }

    public function getDescription(): string
    {
        return 'Activa la Skill de Python de Compras (superconector v4.2): '
             . 'analiza los quiebres de inventario (stock 0) y clasifica las '
             . 'fallas por criticidad — Tier 1: Top 20% de demanda con mayor '
             . 'volumen de salidas (compra inmediata); Tier 2: menor demanda '
             . '— y devuelve alertas de stock 0 por SKU y resumen por '
             . 'laboratorio. Parametros: dias_rotacion (default 30), '
             . 'fraccion_tier1 (default 0.20) y minimo_alertar (default 0.0). '
             . 'Retorna JSON {"success": true, "data": ...}.';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'dias_rotacion' => [
                    'type'        => 'integer',
                    'description' => 'Ventana de dias para el volumen de salidas (default 30).',
                ],
                'fraccion_tier1' => [
                    'type'        => 'number',
                    'description' => 'Fraccion del volumen total para el umbral de falla critica Tier 1 (default 0.20 = Top 20%).',
                ],
                'minimo_alertar' => [
                    'type'        => 'number',
                    'description' => 'Stock maximo considerado en alerta (default 0.0 = agotado estricto).',
                ],
            ],
            'required' => [],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $t0 = self::micro();
        $advertencias = [];

        // ── AD9 / v4.17: resumen de compras priorizado (Profit, best-effort) ─
        $priorizado = $this->resumenComprasPriorizado($advertencias);

        // ── Skill Python (compat v4.2, no rompe el contrato previo) ────────
        $params = [];
        if (isset($arguments['dias_rotacion'])) {
            $params['dias_rotacion'] = (int) $arguments['dias_rotacion'];
        }
        if (isset($arguments['fraccion_tier1'])) {
            $params['fraccion_tier1'] = (float) $arguments['fraccion_tier1'];
        }
        if (isset($arguments['minimo_alertar'])) {
            $params['minimo_alertar'] = (float) $arguments['minimo_alertar'];
        }
        $skill = PythonSkillExecutor::runSkill('compras/quiebres_compras', $params, 'analizar_quiebres_compras');
        if (($skill['success'] ?? false)) {
            $skill['card'] = CardBuilder::resumir(
                is_array($skill['data'] ?? null) ? $skill['data'] : [],
                'SKILL PYTHON: COMPRAS (quiebres_compras)',
                '🛒',
                'Fuente: skills/compras/quiebres_compras'
            );
        } else {
            $advertencias[] = 'Skill Python no ejecutada: ' . (string) ($skill['error'] ?? 'Error desconocido');
            $skill['card'] = CardBuilder::iniciar('🚫', 'SKILL PYTHON NO EJECUTADA')
                ->linea((string) ($skill['error'] ?? 'Error desconocido'))
                ->tarjeta();
        }

        $card = $this->tarjetaPriorizados($priorizado, $advertencias, (int) ((self::micro() - $t0) * 1_000_000));

        return [
            'success' => true,
            'data'    => [
                'fecha_consulta'   => date('c'),
                'priorizados'      => $priorizado,
                'prioridad_alta'   => $priorizado['alta'] ?? [],
                'prioridad_media'  => $priorizado['media'] ?? [],
                'prioridad_baja'   => $priorizado['baja'] ?? [],
                'advertencias'     => $advertencias,
                'metric_us'        => (int) ((self::micro() - $t0) * 1_000_000),
            ],
            'card'    => $card,
            'skill'   => $skill,
        ];
    }

    /**
     * Resumen de compras priorizado (AD9 / v4.17):
     *  - rotación 30d y promedio diario de salida
     *  - días inventario = stock_disponible / promedio_diario_salida
     *  - prioridad ALTA cuando stock_disponible/stock_minimo < 1.5 (o stock 0)
     *  - sugerido_comprar = round(stock_max - stock_actual), con defaults
     *    stock_minimo=10 / stock_maximo=50 si la tabla no los expone.
     *
     * @param array<int,string> $advertencias (por referencia)
     *
     * @return array<string,mixed>
     */
    private function resumenComprasPriorizado(array &$advertencias): array
    {
        $vacio = ['alta' => [], 'media' => [], 'baja' => [], 'total' => 0];
        $wrapper = new ConnectionWrapper();
        $tablaStock = $wrapper->resolverTabla(['st_almac', 'existencia', 'art', 'inventario', 'stock']);
        if ($tablaStock === null) {
            try {
                $wrapper->queryProfit('SELECT 1');
            } catch (PDOException $e) {
                $advertencias[] = 'Profit PRUEB25 no disponible: ' . self::aUtf8($e->getMessage());
                return $vacio;
            }
            $advertencias[] = 'Sin tabla de stock en PRUEB25 (candidatas: st_almac, existencia, art, inventario).';
            return $vacio;
        }
        $cols = $wrapper->resolverColumnas($tablaStock, [
            'cod'   => ['co_art', 'cod_art', 'articulo', 'art'],
            'des'   => ['art_des', 'descripcion', 'nombre', 'art_descr'],
            'stock' => ['stock', 'existencia', 'exist', 'st_act', 'stock_actual'],
            'comp'  => ['stock_comprometido', 'comprometido', 'reservado', 'comprom'],
            'min'   => ['stock_minimo', 'stock_min', 'minimo', 'min'],
            'max'   => ['stock_maximo', 'stock_max', 'maximo', 'max'],
        ]);
        if ($cols['cod'] === null || $cols['stock'] === null) {
            $advertencias[] = 'La tabla de stock no expone columna de código o de stock actual.';
            return $vacio;
        }

        $rotacion = $this->rotacion30d($wrapper, $advertencias);

        $sql = 'SELECT TOP 2000 ' . $wrapper->qPara($cols['cod']) . ' AS cod'
             . ($cols['des'] !== null ? ', ' . $wrapper->qPara($cols['des']) . ' AS des' : '')
             . ', ' . $wrapper->qPara($cols['stock']) . ' AS stock'
             . ($cols['comp'] !== null ? ', ' . $wrapper->qPara($cols['comp']) . ' AS comp' : '')
             . ($cols['min'] !== null ? ', ' . $wrapper->qPara($cols['min']) . ' AS min' : '')
             . ($cols['max'] !== null ? ', ' . $wrapper->qPara($cols['max']) . ' AS max' : '')
             . ' FROM ' . $wrapper->qPara($tablaStock) . ' WITH (NOLOCK)'
             . ' ORDER BY ' . $wrapper->qPara($cols['cod']);
        try {
            $filas = $wrapper->querySafe($sql, [], 0);
        } catch (PDOException $e) {
            $advertencias[] = 'Stock no consultable: ' . self::aUtf8($e->getMessage());
            return $vacio;
        }

        $alta = [];
        $media = [];
        $baja = [];
        foreach ($filas as $fila) {
            $cod = self::aUtf8((string) ($fila['cod'] ?? ''));
            $stock = (float) ($fila['stock'] ?? 0);
            $comp = $cols['comp'] !== null && ($fila['comp'] ?? '') !== '' ? (float) $fila['comp'] : 0.0;
            $disponible = max(0.0, $stock - $comp);
            $minimo = $cols['min'] !== null && ($fila['min'] ?? '') !== '' ? (float) $fila['min'] : 10.0;
            $maximo = $cols['max'] !== null && ($fila['max'] ?? '') !== '' ? (float) $fila['max'] : 50.0;
            $rot = $rotacion[$cod] ?? 0.0;
            $promedioDiario = $rot > 0 ? $rot / 30.0 : 0.0;
            $diasInventario = $promedioDiario > 0 ? round($disponible / $promedioDiario, 1) : null;
            $ratio = $minimo > 0 ? $disponible / $minimo : null;

            if ($disponible <= 0) {
                $prioridad = 'alta';
            } elseif ($ratio !== null && $ratio < 1.5) {
                $prioridad = 'alta';
            } elseif ($ratio !== null && $ratio < 3.0) {
                $prioridad = 'media';
            } else {
                $prioridad = 'baja';
            }

            $item = [
                'co_art'                => $cod,
                'art_des'               => $cols['des'] !== null ? self::aUtf8((string) ($fila['des'] ?? '')) : '',
                'stock_actual'          => $stock,
                'stock_disponible'      => $disponible,
                'stock_minimo'          => $minimo,
                'stock_maximo'          => $maximo,
                'rotacion_30d'          => $rot,
                'promedio_diario_salida'=> $promedioDiario,
                'dias_inventario'       => $diasInventario,
                'sugerido_comprar'      => max(0, (int) round($maximo - $stock)),
                'prioridad'             => $prioridad,
            ];
            if ($prioridad === 'alta') {
                $alta[] = $item;
            } elseif ($prioridad === 'media') {
                $media[] = $item;
            } else {
                $baja[] = $item;
            }
        }

        // Orden: alta por ratio (días inventario asc, nulls al final).
        usort($alta, static fn (array $a, array $b): int => ($a['dias_inventario'] ?? PHP_INT_MAX) <=> ($b['dias_inventario'] ?? PHP_INT_MAX));
        usort($media, static fn (array $a, array $b): int => ($a['dias_inventario'] ?? PHP_INT_MAX) <=> ($b['dias_inventario'] ?? PHP_INT_MAX));

        return [
            'alta'  => array_slice($alta, 0, 100),
            'media' => array_slice($media, 0, 100),
            'baja'  => array_slice($baja, 0, 100),
            'total' => count($alta) + count($media) + count($baja),
        ];
    }

    /**
     * Rotación 30d por artículo: suma de cantidades despachadas desde
     * reng_nde unida a la cabecera de nota (Profit) con fecha >= 30 días.
     * Best-effort: [] con advertencia si el cruce no es posible.
     *
     * @param array<int,string> $advertencias (por referencia)
     *
     * @return array<string,float> co_art => total 30d
     */
    private function rotacion30d(ConnectionWrapper $wrapper, array &$advertencias): array
    {
        $tablaReng = $wrapper->resolverTabla(['reng_nde', 'reng_ndd']);
        $tablaCab = $wrapper->resolverTabla(['notas', 'saNotaEntrega', 'repNotaEntrega', 'nota']);
        if ($tablaReng === null || $tablaCab === null) {
            $advertencias[] = 'Sin reng_nde/cabecera de nota en Profit; rotación 30d omitida.';
            return [];
        }
        $colsR = $wrapper->resolverColumnas($tablaReng, [
            'num'  => ['num_doc', 'fact_num'],
            'art'  => ['co_art', 'cod_art'],
            'cant' => ['total_art', 'cant_sol'],
        ]);
        $colsC = $wrapper->resolverColumnas($tablaCab, [
            'num'   => ['num_doc', 'num', 'cod_doc'],
            'fecha' => ['fecha', 'fec_emis', 'fec_emision', 'fec_lac'],
        ]);
        if ($colsR['num'] === null || $colsR['art'] === null || $colsR['cant'] === null
            || $colsC['num'] === null || $colsC['fecha'] === null) {
            $advertencias[] = 'reng_nde/cabecera no expone columnas para rotación 30d.';
            return [];
        }
        $subReng = '(SELECT TOP 20000 ' . $wrapper->qPara($colsR['num']) . ' AS num'
            . ', ' . $wrapper->qPara($colsR['art']) . ' AS art'
            . ', ' . $wrapper->qPara($colsR['cant']) . ' AS cant'
            . ' FROM ' . $wrapper->qPara($tablaReng) . ' WITH (NOLOCK)) r';
        $sql = 'SELECT r.art AS co_art, SUM(r.cant) AS total FROM ' . $subReng
             . ' INNER JOIN ' . $wrapper->qPara($tablaCab) . ' c WITH (NOLOCK)'
             . ' ON r.num = c.' . $wrapper->qPara($colsC['num'])
             . ' WHERE c.' . $wrapper->qPara($colsC['fecha']) . ' >= DATEADD(day,-30,GETDATE())'
             . ' GROUP BY r.art';
        try {
            $mapa = [];
            foreach ($wrapper->querySafe($sql, [], 0) as $fila) {
                $mapa[(string) ($fila['co_art'] ?? '')] = (float) ($fila['total'] ?? 0);
            }
            return $mapa;
        } catch (PDOException $e) {
            $advertencias[] = 'Rotación 30d no consultable: ' . self::aUtf8($e->getMessage());
            return [];
        }
    }

    /** Tarjeta estándar v4.13 del resumen priorizado de compras (CardBuilder). */
    private function tarjetaPriorizados(array $priorizado, array $advertencias, int $us): string
    {
        $alta = (array) ($priorizado['alta'] ?? []);
        $media = (array) ($priorizado['media'] ?? []);
        $card = CardBuilder::iniciar('🛒', 'RESUMEN DE COMPRAS PRIORIZADO (v4.17)')
            ->campo('ALTA PRIORIDAD', count($alta), 'numero')
            ->campo('MEDIA PRIORIDAD', count($media), 'numero')
            ->campo('TOTAL', (int) ($priorizado['total'] ?? 0), 'numero');
        $card->seccion('Compra inmediata (stock/min < 1.5 o agotado)');
        foreach (array_slice($alta, 0, 8) as $a) {
            $card->linea('🔴 ' . $a['co_art'] . ' · ' . $a['art_des'] . ' · disp ' . (float) $a['stock_disponible']
                . ' · sugerido ' . (int) $a['sugerido_comprar']
                . ($a['dias_inventario'] !== null ? ' · ' . $a['dias_inventario'] . ' días inv.' : ' · sin rotación'));
        }
        if (count($alta) > 8) {
            $card->linea('⏩ +' . (count($alta) - 8) . ' ítems más en alta prioridad.');
        }
        foreach (array_slice($advertencias, 0, 2) as $aviso) {
            $card->linea('⚠️ ' . $aviso);
        }
        return $card
            ->footer('Fuente: Profit PRUEB25 (NOLOCK) · ' . round($us / 1000) . ' ms')
            ->tarjeta();
    }

    /**
     * Normaliza texto del ERP a UTF-8 (los datos vienen en CP1252/Latin-1).
     */
    private static function aUtf8(string $texto): string
    {
        $limpio = trim($texto);
        if (mb_check_encoding($limpio, 'UTF-8')) {
            return $limpio;
        }
        $convertido = mb_convert_encoding($limpio, 'UTF-8', 'CP1252');
        return is_string($convertido) ? $convertido : $limpio;
    }

    /** Timestamp con microsegundos (métricas del estándar NvidiaBrain Tools). */
    private static function micro(): float
    {
        return microtime(true);
    }

    public function getDefinition(): array
    {
        return [
            'name'        => $this->getName(),
            'description' => $this->getDescription(),
            'parameters'  => $this->getParameters(),
        ];
    }
}
