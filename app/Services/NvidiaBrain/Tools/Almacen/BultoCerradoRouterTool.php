<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Almacen;

use App\Services\ConnectionWrapper;
use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Almacen\ProfitStockHelper;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;
use PDO;
use PDOException;

require_once __DIR__ . '/ProfitStockHelper.php';
require_once __DIR__ . '/../Common/CardBuilder.php';

/**
 * Enrutador de artículos hacia bulto cerrado según la disponibilidad real por
 * sub-almacén (SQL Server Profit PRUEB25, tabla st_almac).
 *
 * Regla de negocio: un ítem va a Bulto Cerrado (sub-almacén 02 S/C o 05 BQTO)
 * cuando el estante de picking (01 S/C o 04 BQTO) no cubre la cantidad pedida
 * pero el depósito/bulto cerrado sí tiene existencia.
 *
 * Mapeo de existencias (verificado en producción):
 *   st_almac.co_alma: 01 DESPACHO S/C · 02 DEPOSITO S/C · 04 DESPACHO BQTO ·
 *                     05 DEPOSITO BQTO. art.stock_act = stock consolidado.
 *
 * Contrato de retorno (SIEMPRE array serializable, sin excepciones):
 *   ['success' => true,  'data' => [...]]      → éxito
 *   ['success' => false, 'error' => 'mensaje'] → error controlado
 *
 * Uso:
 *   $registry->registerTool(new BultoCerradoRouterTool());
 */
final class BultoCerradoRouterTool implements AgentToolInterface
{
    private const CAMPOS_ALMACEN = [
        'despacho_sc'  => ['DESPACHO', 'stock_alm01', 'stock_almacen_01'],
        'deposito_sc'  => ['DEPOSITO', 'stock_alm02', 'stock_almacen_02'],
        'despacho_bqto'=> ['DESPACHO_BQTO', 'stock_alm04', 'stock_almacen_04'],
        'deposito_bqto'=> ['DEPOSTO_BQTO', 'stock_alm05', 'stock_almacen_05'],
        'stock_total'  => ['STOCK_ACT', 'stock', 'existencia'],
        'codigo'       => ['co_art', 'codigo', 'cod_art', 'articulo'],
        'nombre'       => ['art_des', 'descripcion', 'nombre', 'nombre_producto'],
    ];

    public function getName(): string
    {
        return 'bulto_cerrado_router';
    }

    public function getDescription(): string
    {
        return 'Enruta artículos hacia Bulto Cerrado: decide si cada ítem se '
             . 'surtirá del estante de picking (Almacén 01 S/C o 04 BQTO) o del '
             . 'bulto cerrado/depósito (02 S/C o 05 BQTO) según la cantidad '
             . 'pedida vs. la existencia real por sub-almacén (Profit st_almac). '
             . 'Parámetros: items (lista [{co_art, cantidad}], opcional — '
             . 'también acepta texto plano "CODIGO:CANTIDAD, CODIGO:CANTIDAD", '
             . 'ej. "MD04668:5, MD01707:3") y almacen (sc|bqto, default sc). '
             . 'SIN "items" (modo proactivo): en vez de enrutar pedidos puntuales, '
             . 'analiza el bulto cerrado/depósito completo y sugiere QUÉ bajar a '
             . 'picking ANTES de que el estante se quede en 0 — cruza volumen '
             . 'almacenado en depósito con rotación de venta histórica, para '
             . 'anticipar el traslado en vez de reaccionar cuando ya escasea. '
             . 'Parámetros de este modo: umbral_volumen (mínimo en depósito para '
             . 'considerarlo, default 10), umbral_rotacion (mínimo de ventas '
             . 'históricas para no bajar stock muerto, default 1) y limite '
             . '(default 20).';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'items' => [
                    'type'        => 'array',
                    'description' => 'Lista de ítems a enrutar: [{"co_art": "ALIM0001", "cantidad": 8}]. '
                                    . 'Si se omite, corre el modo proactivo (sugerencia de traslado por volumen/rotación).',
                    'items'       => [
                        'type' => 'object',
                        'properties' => [
                            'co_art'   => ['type' => 'string', 'description' => 'Código del artículo.'],
                            'cantidad' => ['type' => 'number', 'description' => 'Cantidad solicitada.'],
                        ],
                    ],
                ],
                'almacen' => [
                    'type'        => 'string',
                    'enum'        => ['sc', 'bqto'],
                    'description' => 'Almacén operativo: "sc" (San Cristóbal) o "bqto" (Barquisimeto).',
                ],
                'umbral_volumen' => [
                    'type'        => 'integer',
                    'description' => 'Modo proactivo: mínimo de unidades en depósito/bulto cerrado para considerar el artículo (default 10).',
                ],
                'umbral_rotacion' => [
                    'type'        => 'integer',
                    'description' => 'Modo proactivo: mínimo de ventas históricas (reng_fac/reng_nde) para excluir stock muerto (default 1).',
                ],
                'limite' => [
                    'type'        => 'integer',
                    'description' => 'Modo proactivo: máximo de artículos a sugerir (default 20).',
                ],
            ],
            'required' => [],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        // Captura de texto plano (a pedido): "items" es un array de objetos
        // {co_art, cantidad}, imposible de escribir cómodo como key=value en
        // el chat — antes SOLO aceptaba JSON real, así que /bulto_cerrado_router
        // MD04668:5 (o cualquier variante en texto) siempre fallaba con
        // "Parámetro items es obligatorio" sin explicar el formato esperado.
        // Ahora, si "items" (o el texto plano capturado bajo 'text'/posición 0)
        // llega como STRING, se interpreta como lista "CODIGO:CANTIDAD" o
        // "CODIGO CANTIDAD" separada por comas o saltos de línea.
        $almacen = strtolower(trim((string) ($arguments['almacen'] ?? 'sc')));
        if (!in_array($almacen, ['sc', 'bqto'], true)) {
            return ['success' => false, 'error' => 'Parámetro "almacen" debe ser "sc" o "bqto".'];
        }

        // Sin "items" (ni ninguna de sus variantes de captura): modo proactivo
        // — no hay un pedido puntual que enrutar, así que en vez de exigir
        // items se corre el análisis de todo el bulto cerrado (a pedido del
        // usuario: "aconseja por filtros específicos que stock surtir por su
        // volumen y movimiento").
        $itemsRaw = $arguments['items'] ?? $arguments[0] ?? $arguments['text'] ?? null;
        if ($itemsRaw === null) {
            return $this->modoProactivo($arguments, $almacen);
        }

        $items = is_string($itemsRaw) ? $this->parsearItemsTexto($itemsRaw) : $itemsRaw;
        if (!is_array($items) || $items === []) {
            return [
                'success' => false,
                'error'   => 'Parámetro "items" inválido: lista de {co_art, cantidad} o texto '
                           . '"CODIGO:CANTIDAD, CODIGO:CANTIDAD" (ej. "MD04668:5, MD01707:3"). '
                           . 'Omitilo por completo para el modo proactivo (sugerencia de traslado).',
            ];
        }

        $normalizados = [];
        foreach ($items as $it) {
            if (!is_array($it)) {
                continue;
            }
            $co = strtoupper(trim((string) ($it['co_art'] ?? $it['codigo'] ?? '')));
            if ($co === '') {
                continue;
            }
            $cant = (float) ($it['cantidad'] ?? 0);
            if ($cant <= 0) {
                continue;
            }
            $normalizados[$co] = $cant;
        }
        if ($normalizados === []) {
            return ['success' => false, 'error' => 'Ningún ítem válido en "items" ({co_art, cantidad > 0}).'];
        }

        $subEstante  = $almacen === 'sc' ? ProfitStockHelper::SUB_DESPACHO_SC : ProfitStockHelper::SUB_DESPACHO_BQTO;
        $subDeposito = $almacen === 'sc' ? ProfitStockHelper::SUB_DEPOSITO_SC : ProfitStockHelper::SUB_DEPOSITO_BQTO;

        try {
            $w = new ConnectionWrapper();
            $codigos = array_keys($normalizados);
            $stocks = ProfitStockHelper::stockPorSubAlmacen($w, $codigos, [$subEstante, $subDeposito]);
            $maestros = ProfitStockHelper::maestros($w, $codigos);
        } catch (PDOException $e) {
            return $this->desdeMySQLLegacy($normalizados, $almacen);
        }

        $rutas = [];
        $faltantes = [];
        foreach ($normalizados as $co => $cantidad) {
            if (!isset($maestros[$co]) && !isset($stocks[$co])) {
                $faltantes[] = $co;
                continue;
            }
            $enEstante  = $stocks[$co][$subEstante] ?? 0.0;
            $enDeposito = $stocks[$co][$subDeposito] ?? 0.0;
            $surtirDelEstante = min($enEstante, $cantidad);
            $delDeposito = max(0, $cantidad - $surtirDelEstante);
            $rutas[] = [
                'co_art'           => $co,
                'nombre'           => ProfitStockHelper::aUtf8((string) ($maestros[$co]['art_des'] ?? '')),
                'cantidad'         => $cantidad,
                'desde_estante'    => $surtirDelEstante,
                'desde_bulto_cerrado' => min($delDeposito, $enDeposito),
                'faltante_total'   => max(0, $cantidad - $surtirDelEstante - $enDeposito),
                'requiere_bulto_cerrado' => $delDeposito > 0,
            ];
        }
        $pdo = null;

        $card = $this->tarjetaRutas($almacen, $rutas, $faltantes, 'profit_st_almac (PRUEB25)');

        return [
            'success' => true,
            'data'    => [
                'fuente'       => 'profit_st_almac (PRUEB25)',
                'almacen'      => $almacen,
                'estante'      => $almacen === 'sc' ? 'DESPACHO (01)' : 'DESPACHO_BQTO (04)',
                'bulto_cerrado' => $almacen === 'sc' ? 'DEPOSITO (02)' : 'DEPOSTO_BQTO (05)',
                'items'        => $rutas,
                'sin_registro' => $faltantes,
                'total_items'  => count($rutas),
            ],
            'card'    => $card,
        ];
    }

    /**
     * Modo proactivo: sin un pedido puntual que enrutar, analiza el bulto
     * cerrado/depósito completo y sugiere qué bajar a picking ANTES de que el
     * estante se quede en 0 — cruza volumen almacenado en depósito (art) con
     * rotación de venta histórica (reng_fac/reng_nde vía ProfitStockHelper),
     * a diferencia de stock_surtido_prioritario que reacciona quan el estante
     * YA está bajo (<5 und). Aquí se anticipa el traslado usando la relación
     * rotación/estante como "urgencia": mientras más se vende un artículo y
     * menos le queda en el estante, más arriba sale en la sugerencia.
     */
    private function modoProactivo(array $arguments, string $almacen): array
    {
        $umbralVolumen  = max(1, min(10000, (int) ($arguments['umbral_volumen'] ?? 10)));
        $umbralRotacion = max(0, min(10000, (int) ($arguments['umbral_rotacion'] ?? 1)));
        $limite         = max(1, min(100, (int) ($arguments['limite'] ?? 20)));

        $subEstante  = $almacen === 'sc' ? ProfitStockHelper::SUB_DESPACHO_SC : ProfitStockHelper::SUB_DESPACHO_BQTO;
        $subDeposito = $almacen === 'sc' ? ProfitStockHelper::SUB_DEPOSITO_SC : ProfitStockHelper::SUB_DEPOSITO_BQTO;
        $sucursal    = $almacen === 'sc' ? '01' : '02';

        try {
            $w = new ConnectionWrapper();
            $candidatos = $this->candidatosProactivosDesdeProfit($w, $subDeposito, $umbralVolumen, $limite);
            if ($candidatos === []) {
                return [
                    'success' => true,
                    'data'    => [
                        'fuente'  => 'profit_st_almac (PRUEB25)',
                        'almacen' => $almacen,
                        'total'   => 0,
                        'articulos' => [],
                    ],
                    'card' => CardBuilder::iniciar('✅', 'BULTO CERRADO SIN CANDIDATOS')
                        ->linea('Ningún artículo en depósito supera ' . $umbralVolumen . ' und para sugerir traslado.')
                        ->footer('Fuente: profit_st_almac (PRUEB25)')
                        ->tarjeta(),
                ];
            }
            $codigos  = array_keys($candidatos);
            $stocks   = ProfitStockHelper::stockPorSubAlmacen($w, $codigos, [$subEstante, $subDeposito]);
            $maestros = ProfitStockHelper::maestros($w, $codigos);
            $rotacion = ProfitStockHelper::rotacion($w, $codigos, $sucursal);
        } catch (PDOException $e) {
            return [
                'success' => false,
                'error'   => 'Modo proactivo requiere Profit (rotación de venta real): '
                           . ProfitStockHelper::aUtf8($e->getMessage()),
            ];
        }

        $sugerencias = [];
        foreach ($codigos as $co) {
            $rotacionVenta = $rotacion[$co] ?? 0;
            if ($rotacionVenta < $umbralRotacion) {
                continue; // sin movimiento real: no vale la pena adelantarlo
            }
            $enEstante  = $stocks[$co][$subEstante] ?? 0.0;
            $enDeposito = $stocks[$co][$subDeposito] ?? $candidatos[$co];
            $campo7 = trim((string) ($maestros[$co]['campo7'] ?? ''));
            $sugerencias[] = [
                'co_art'         => $co,
                'nombre'         => ProfitStockHelper::aUtf8((string) ($maestros[$co]['art_des'] ?? '')),
                'en_estante'     => $enEstante,
                'en_deposito'    => $enDeposito,
                'rotacion_venta' => $rotacionVenta,
                // Urgencia: rotación por unidad disponible en estante — a más
                // ventas históricas y menos queda en picking, más urgente.
                'urgencia'       => round($rotacionVenta / max($enEstante, 0.5), 2),
                'ubicacion'      => $campo7,
                'pasillo'        => $this->resolverPasillo($campo7),
            ];
        }
        usort($sugerencias, static fn (array $a, array $b): int => $b['urgencia'] <=> $a['urgencia']);
        $sugerencias = array_slice($sugerencias, 0, $limite);

        return [
            'success' => true,
            'data'    => [
                'fuente'          => 'profit_st_almac (PRUEB25)',
                'almacen'         => $almacen,
                'umbral_volumen'  => $umbralVolumen,
                'umbral_rotacion' => $umbralRotacion,
                'bulto_cerrado'   => $almacen === 'sc' ? 'DEPOSITO (02)' : 'DEPOSTO_BQTO (05)',
                'total'           => count($sugerencias),
                'articulos'       => $sugerencias,
            ],
            'card' => $this->tarjetaProactiva($almacen, $sugerencias, $umbralVolumen),
        ];
    }

    /**
     * Candidatos del modo proactivo: artículos en el depósito/bulto cerrado
     * con volumen >= umbral. Mismo cuidado que StockSurtidoPrioritarioTool
     * (bug real de driver v18/08): el sub-almacén y el umbral se embeben como
     * literales (nunca vienen de input libre del usuario — son constantes o
     * ya validados como int), NUNCA como '?' dentro de HAVING.
     *
     * @return array<string,float> co_art => volumen en depósito
     */
    private function candidatosProactivosDesdeProfit(ConnectionWrapper $w, string $subDeposito, int $umbralVolumen, int $limite): array
    {
        if (!preg_match('/^\d{2}$/', $subDeposito)) {
            throw new \InvalidArgumentException('Sub-almacén inválido.');
        }
        $topCandidatos = min(300, max($limite * 5, $limite));
        $sql = 'SELECT TOP ' . $topCandidatos . ' ' . ProfitStockHelper::qSrv('co_art')
             . ', SUM(' . ProfitStockHelper::qSrv('stock_act') . ') AS vol_deposito'
             . ' FROM ' . ProfitStockHelper::qSrv('st_almac') . ' WITH (NOLOCK)'
             . ' WHERE ' . ProfitStockHelper::qSrv('co_alma') . " = '" . $subDeposito . "'"
             . ' GROUP BY ' . ProfitStockHelper::qSrv('co_art')
             . ' HAVING SUM(' . ProfitStockHelper::qSrv('stock_act') . ') >= ' . $umbralVolumen
             . ' ORDER BY vol_deposito DESC';
        $candidatos = [];
        foreach ($w->querySafe($sql, []) as $fila) {
            $candidatos[strtoupper(trim((string) $fila['co_art']))] = (float) $fila['vol_deposito'];
        }
        return $candidatos;
    }

    /** Pasillo = prefijo de campo7 antes de "-P" (ej. "4MDB04-P6" → pasillo "4MDB04"). */
    private function resolverPasillo(string $campo7): string
    {
        if ($campo7 === '') {
            return 'SIN UBICACIÓN';
        }
        if (preg_match('/^(.+?)-P\d+$/i', $campo7, $m) === 1) {
            return strtoupper($m[1]);
        }
        return strtoupper($campo7);
    }

    /** Tarjeta del modo proactivo: qué bajar del bulto cerrado antes de que falte. */
    private function tarjetaProactiva(string $almacen, array $sugerencias, int $umbralVolumen): string
    {
        if ($sugerencias === []) {
            return CardBuilder::iniciar('✅', 'SIN SUGERENCIAS DE TRASLADO')
                ->linea('Ningún artículo con volumen en depósito y movimiento real supera los filtros pedidos.')
                ->footer('Fuente: profit_st_almac (PRUEB25)')
                ->tarjeta();
        }
        $card = CardBuilder::iniciar('📦', 'BAJAR DE BULTO CERRADO (' . strtoupper($almacen) . ')')
            ->campo('UMBRAL VOLUMEN', '>= ' . $umbralVolumen . ' und')
            ->campo('SUGERENCIAS', count($sugerencias), 'numero')
            ->seccion('Por urgencia (rotación vs. estante)');
        foreach (array_slice($sugerencias, 0, 10) as $s) {
            $marca = $s['en_estante'] < 5 ? '🔴' : '🟡';
            $card->linea($marca . ' ' . $s['co_art'] . ' · ' . $s['nombre'] . ' · pasillo ' . $s['pasillo']);
            $card->linea('estante ' . number_format((float) $s['en_estante'], 0, ',', '.')
                . ' und · depósito ' . number_format((float) $s['en_deposito'], 0, ',', '.')
                . ' und · rotación ' . $s['rotacion_venta'] . ' · urgencia ' . $s['urgencia']);
        }
        $ocultos = count($sugerencias) - 10;
        if ($ocultos > 0) {
            $card->linea('⏩ +' . $ocultos . ' artículo(s) más (usa "limite" para ver más).');
        }
        return $card
            ->footer('Fuente: profit_st_almac (PRUEB25, NOLOCK)')
            ->tarjeta();
    }

    /** Tarjeta estándar v4.13 del enrutamiento a bulto cerrado (CardBuilder). */
    private function tarjetaRutas(string $almacen, array $rutas, array $faltantes, string $fuente): string
    {
        $card = CardBuilder::iniciar('🧭', 'ENRUTAMIENTO A BULTO CERRADO (' . strtoupper($almacen) . ')')
            ->campo('ÍTEMS EVALUADOS', count($rutas), 'numero');
        if ($rutas === []) {
            $card->linea('Sin ítems enrutables en la consulta.');
        } else {
            $card->seccion('Estante → Bulto Cerrado');
            foreach (array_slice($rutas, 0, 6) as $r) {
                $ruta = ($r['requiere_bulto_cerrado'] ? '📦 ' : '🟢 ') . $r['co_art'] . ' · ' . $r['nombre'];
                $card->linea($ruta);
                $card->linea('pedido ' . number_format((float) $r['cantidad'], 0, ',', '.')
                    . ' · estante ' . number_format((float) $r['desde_estante'], 0, ',', '.')
                    . ' · BC ' . number_format((float) $r['desde_bulto_cerrado'], 0, ',', '.')
                    . (($r['faltante_total'] ?? 0) > 0 ? ' · ⚠️ faltante ' . number_format((float) $r['faltante_total'], 0, ',', '.') : ''));
            }
            $ocultos = count($rutas) - 6;
            if ($ocultos > 0) {
                $card->linea('⏩ +' . $ocultos . ' ítem(s) más en la consulta.');
            }
        }
        if ($faltantes !== []) {
            $card->linea('⚠️ Sin registro: ' . implode(', ', array_slice($faltantes, 0, 5)) . (count($faltantes) > 5 ? '…' : ''));
        }
        return $card
            ->footer('Fuente: ' . $fuente . ' (NOLOCK)')
            ->tarjeta();
    }

    /**
     * Parsea "MD04668:5, MD01707:3" (o "MD04668 5" sin dos puntos, separado
     * por coma o salto de línea) a la forma [{co_art, cantidad}, ...]. Si el
     * texto es en realidad JSON válido (alguien mandó el array de siempre),
     * lo usa directo — no rompe el formato anterior, solo agrega el atajo.
     *
     * @return list<array{co_art:string,cantidad:float}>
     */
    private function parsearItemsTexto(string $texto): array
    {
        $texto = trim($texto);
        if ($texto === '') {
            return [];
        }
        $comoJson = json_decode($texto, true);
        if (is_array($comoJson)) {
            return $comoJson;
        }
        $items = [];
        foreach (preg_split('/[,\n]+/', $texto) as $par) {
            $par = trim($par);
            if ($par === '') {
                continue;
            }
            if (preg_match('/^([A-Za-z0-9_\-]+)\s*[:=]?\s*(\d+(?:[.,]\d+)?)$/', $par, $m) !== 1) {
                continue;
            }
            $items[] = [
                'co_art'   => strtoupper($m[1]),
                'cantidad' => (float) str_replace(',', '.', $m[2]),
            ];
        }
        return $items;
    }

    public function getDefinition(): array
    {
        return [
            'name'        => $this->getName(),
            'description' => $this->getDescription(),
            'parameters'  => $this->getParameters(),
        ];
    }

    /** Fallback: tabla `articulos` del MySQL legacy (ruta previa). */
    private function desdeMySQLLegacy(array $normalizados, string $almacen): array
    {
        try {
            $pdo = $this->conectarMySQL();
        } catch (PDOException $e) {
            return ['success' => false, 'error' => 'Profit no respondió y el MySQL legacy tampoco: ' . ProfitStockHelper::aUtf8($e->getMessage())];
        }
        $cols = $this->resolverColumnas($pdo, 'articulos');
        if ($cols === null) {
            return ['success' => false, 'error' => 'Profit sin st_almac y la tabla "articulos" no existe en el MySQL legacy.'];
        }
        $colEstante = $almacen === 'sc' ? $cols['despacho_sc'] : $cols['despacho_bqto'];
        $colDeposito = $almacen === 'sc' ? $cols['deposito_sc'] : $cols['deposito_bqto'];
        $codigos = array_keys($normalizados);
        $marks = implode(',', array_fill(0, count($codigos), '?'));
        $sql = 'SELECT ' . self::q($cols['codigo']) . ' AS co_art'
             . (($cols['nombre'] !== null) ? ', ' . self::q($cols['nombre']) . ' AS nombre' : '')
             . ', ' . self::q($colEstante) . ' AS en_estante'
             . ', ' . self::q($colDeposito) . ' AS en_deposito'
             . ' FROM ' . self::q('articulos')
             . ' WHERE ' . self::q($cols['codigo']) . ' IN (' . $marks . ')';
        try {
            $stmt = $pdo->prepare($sql);
            $stmt->execute($codigos);
            $filas = $stmt->fetchAll(PDO::FETCH_ASSOC);
        } catch (PDOException $e) {
            return ['success' => false, 'error' => 'Error consultando existencias (MySQL legacy): ' . ProfitStockHelper::aUtf8($e->getMessage())];
        }
        $disponibles = [];
        foreach ($filas as $fila) {
            $disponibles[strtoupper((string) $fila['co_art'])] = [
                'nombre'      => ProfitStockHelper::aUtf8((string) ($fila['nombre'] ?? '')),
                'en_estante'  => (float) ($fila['en_estante'] ?? 0),
                'en_deposito' => (float) ($fila['en_deposito'] ?? 0),
            ];
        }
        $rutas = [];
        $faltantes = [];
        foreach ($normalizados as $co => $cantidad) {
            if (!isset($disponibles[$co])) {
                $faltantes[] = $co;
                continue;
            }
            $stock = $disponibles[$co];
            $surtirDelEstante = min($stock['en_estante'], $cantidad);
            $delDeposito = max(0, $cantidad - $surtirDelEstante);
            $rutas[] = [
                'co_art'           => $co,
                'nombre'           => $stock['nombre'],
                'cantidad'         => $cantidad,
                'desde_estante'    => $surtirDelEstante,
                'desde_bulto_cerrado' => min($delDeposito, $stock['en_deposito']),
                'faltante_total'   => max(0, $cantidad - $surtirDelEstante - $stock['en_deposito']),
                'requiere_bulto_cerrado' => $delDeposito > 0,
            ];
        }
        $pdo = null;
        return [
            'success' => true,
            'data'    => [
                'fuente'       => 'mysql_legacy articulos',
                'almacen'      => $almacen,
                'estante'      => $almacen === 'sc' ? 'DESPACHO (01)' : 'DESPACHO_BQTO (04)',
                'bulto_cerrado' => $almacen === 'sc' ? 'DEPOSITO (02)' : 'DEPOSTO_BQTO (05)',
                'items'        => $rutas,
                'sin_registro' => $faltantes,
                'total_items'  => count($rutas),
            ],
            'card'    => $this->tarjetaRutas($almacen, $rutas, $faltantes, 'mysql_legacy articulos'),
        ];
    }

    /** @return array<string,string|null>|null */
    private function resolverColumnas(PDO $pdo, string $tabla): ?array
    {
        try {
            $stmt = $pdo->prepare(
                'SELECT COLUMN_NAME FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = ?'
            );
            $stmt->execute([$tabla]);
            $existentes = $stmt->fetchAll(PDO::FETCH_COLUMN);
        } catch (PDOException $e) {
            return null;
        }
        if ($existentes === []) {
            return null;
        }
        $mapa = [];
        foreach (self::CAMPOS_ALMACEN as $campo => $candidatos) {
            $mapa[$campo] = null;
            foreach ($candidatos as $candidato) {
                if (in_array($candidato, $existentes, true)) {
                    $mapa[$campo] = $candidato;
                    break;
                }
            }
        }
        return $mapa['codigo'] !== null ? $mapa : null;
    }

    private function conectarMySQL(): PDO
    {
        $host = ProfitStockHelper::env('MYSQL_HOST', '192.168.4.148');
        $port = (int) ProfitStockHelper::env('MYSQL_PORT', '3306');
        $user = ProfitStockHelper::env('MYSQL_USER', 'root');
        $pass = ProfitStockHelper::env('MYSQL_PASSWORD', '');
        $name = ProfitStockHelper::env('MYSQL_DATABASE', '');
        $drivers = PDO::getAvailableDrivers();
        if (in_array('mysql', $drivers, true)) {
            return new PDO(
                'mysql:host=' . $host . ';port=' . $port . ';dbname=' . $name . ';charset=utf8mb4',
                $user,
                $pass,
                [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_TIMEOUT => 8, PDO::ATTR_PERSISTENT => false]
            );
        }
        throw new PDOException('Sin driver MySQL disponible (pdo_mysql requerido para el fallback).');
    }

    private static function q(string $identificador): string
    {
        if (preg_match('/^[A-Za-z_][A-Za-z0-9_]*$/', $identificador) !== 1) {
            throw new \RuntimeException('Identificador SQL inválido: ' . $identificador);
        }
        return '`' . $identificador . '`';
    }
}
