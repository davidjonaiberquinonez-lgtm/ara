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
 * Detección de estantes con poca existencia (por defecto: menos de 5
 * unidades, incluye 0 — antes SOLO detectaba exactamente 0) en picking S/C
 * sub-almacén 01 y BQTO sub-almacén 04, que SÍ tienen existencia disponible
 * en bulto cerrado/depósito (02 y 05 respectivamente), agrupados POR PASILLO
 * (prefijo de art.campo7 antes de "-P", ej. "4MDB04-P6" → pasillo "4MDB04")
 * para que el operador surta en una sola pasada por zona física, ordenados
 * dentro de cada pasillo por mayor rotación de venta (reng_fac/reng_nde).
 *
 * FUENTE PRIMARIA (esquema real verificado en producción):
 *   - st_almac (SQL Server Profit PRUEB25): stock por sub-almacén.
 *     co_alma: 01 DESPACHO S/C · 02 DEPOSITO S/C · 04 DESPACHO BQTO ·
 *              05 DEPOSITO BQTO.
 *   - art: art_des, stock_act (stock consolidado), anulado.
 *   - reng_fac/reng_nde: rotación de venta (co_alma = sucursal 01/02).
 * FALLBACK: tabla `articulos` del MySQL legacy (192.168.4.148) si Profit
 * no responde o st_almac no existe.
 *
 * Mapeo directiva: DESPACHO→01, DEPOSITO→02, DESPACHO_BQTO→04, DEPOSTO_BQTO→05.
 *
 * Contrato de retorno (SIEMPRE array serializable, sin excepciones):
 *   ['success' => true,  'data' => [...]]      → éxito
 *   ['success' => false, 'error' => 'mensaje'] → error controlado
 *
 * Uso:
 *   $registry->registerTool(new StockSurtidoPrioritarioTool());
 */
final class StockSurtidoPrioritarioTool implements AgentToolInterface
{
    /** Columnas candidatas del stock por rol de almacén (fallback MySQL). */
    private const CAMPOS_ALMACEN = [
        'despacho_sc'  => ['DESPACHO', 'stock_alm01', 'stock_almacen_01'],
        'deposito_sc'  => ['DEPOSITO', 'stock_alm02', 'stock_almacen_02'],
        'despacho_bqto'=> ['DESPACHO_BQTO', 'stock_alm04', 'stock_almacen_04'],
        'deposito_bqto'=> ['DEPOSTO_BQTO', 'stock_alm05', 'stock_almacen_05'],
        'stock_total'  => ['STOCK_ACT', 'stock', 'existencia'],
        'codigo'       => ['co_art', 'codigo', 'cod_art', 'articulo'],
        'nombre'       => ['art_des', 'descripcion', 'nombre', 'nombre_producto'],
    ];

    private const LIMITE = 50;

    public function getName(): string
    {
        return 'stock_surtido_prioritario';
    }

    public function getDescription(): string
    {
        return 'Detecta artículos con poco stock en el estante de picking '
             . '(Almacén 01 S/C o Almacén 04 BQTO — por defecto menos de 5 '
             . 'unidades, incluye 0) que SÍ tienen existencia en bulto cerrado '
             . 'o depósito (Almacén 02 S/C o 05 BQTO), agrupados POR PASILLO '
             . '(ubicación física real, campo7 de Profit) para surtir por zona '
             . 'en una sola pasada, y dentro de cada pasillo ordenados por '
             . 'mayor rotación de venta. Parámetros: almacen (sc|bqto, default '
             . 'sc), umbral (unidades, default 5 — <umbral cuenta como poco '
             . 'stock) y limite (default 50).';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'almacen' => [
                    'type'        => 'string',
                    'enum'        => ['sc', 'bqto'],
                    'description' => 'Almacén operativo: "sc" (San Cristóbal) o "bqto" (Barquisimeto).',
                ],
                'umbral' => [
                    'type'        => 'integer',
                    'description' => 'Unidades en estante por debajo de las cuales se considera "poco stock" (default 5, incluye 0).',
                ],
                'limite' => [
                    'type'        => 'integer',
                    'description' => 'Máximo de artículos a devolver (default 50).',
                ],
            ],
            'required' => ['almacen'],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $almacen = strtolower(trim((string) ($arguments['almacen'] ?? 'sc')));
        if (!in_array($almacen, ['sc', 'bqto'], true)) {
            return ['success' => false, 'error' => 'Parámetro "almacen" debe ser "sc" o "bqto".'];
        }
        $limite = max(1, min(200, (int) ($arguments['limite'] ?? self::LIMITE)));
        $umbral = max(1, min(1000, (int) ($arguments['umbral'] ?? 5)));

        if ($almacen === 'sc') {
            $subEstante  = ProfitStockHelper::SUB_DESPACHO_SC;
            $subDeposito = ProfitStockHelper::SUB_DEPOSITO_SC;
            $sucursal    = '01';
        } else {
            $subEstante  = ProfitStockHelper::SUB_DESPACHO_BQTO;
            $subDeposito = ProfitStockHelper::SUB_DEPOSITO_BQTO;
            $sucursal    = '02';
        }

        // Conexión protegida (ConnectionWrapper v4.14: SELECT-only, NOLOCK,
        // timeout 5s, cache local) con fallback progresivo al MySQL legacy.
        $wrapper = new ConnectionWrapper();
        try {
            $candidatos = $this->candidatosDesdeProfit($wrapper, $subEstante, $subDeposito, $limite, $umbral);
            if ($candidatos !== null) {
                $codigos = array_keys($candidatos);
                $stocks  = ProfitStockHelper::stockPorSubAlmacen($wrapper, $codigos, [$subEstante, $subDeposito]);
                $maestros = ProfitStockHelper::maestros($wrapper, $codigos);
                $rotacion = ProfitStockHelper::rotacion($wrapper, $codigos, $sucursal);

                $articulos = [];
                foreach ($codigos as $co) {
                    $campo7 = trim((string) ($maestros[$co]['campo7'] ?? ''));
                    $articulos[] = [
                        'co_art'         => $co,
                        'nombre'         => ProfitStockHelper::aUtf8((string) ($maestros[$co]['art_des'] ?? '')),
                        'en_estante'     => $stocks[$co][$subEstante] ?? 0.0,
                        'en_deposito'    => $stocks[$co][$subDeposito] ?? 0.0,
                        'stock_total'    => isset($maestros[$co]['stock_act']) ? (float) $maestros[$co]['stock_act'] : null,
                        'rotacion_venta' => $rotacion[$co] ?? 0,
                        'ubicacion'      => $campo7,
                        'pasillo'        => $this->resolverPasillo($campo7),
                    ];
                }
                usort($articulos, static function (array $a, array $b): int {
                    $p = $a['pasillo'] <=> $b['pasillo'];
                    return $p !== 0 ? $p : ($b['rotacion_venta'] <=> $a['rotacion_venta']);
                });

                $card = $this->tarjetaArticulos($almacen, 'profit_st_almac (PRUEB25)', $articulos, $umbral);

                return [
                    'success' => true,
                    'data'    => [
                        'fuente'     => 'profit_st_almac (PRUEB25)',
                        'almacen'    => $almacen,
                        'umbral'     => $umbral,
                        'estante_bajo' => $almacen === 'sc' ? 'DESPACHO (01)' : 'DESPACHO_BQTO (04)',
                        'deposito_con_existencia' => $almacen === 'sc' ? 'DEPOSITO (02)' : 'DEPOSTO_BQTO (05)',
                        'total'      => count($articulos),
                        'por_pasillo' => $this->agruparPorPasillo($articulos),
                        'articulos'  => $articulos,
                    ],
                    'card'    => $card,
                ];
            }
        } catch (PDOException $e) {
            $candidatos = null;
        }

        return $this->desdeMySQLLegacy($almacen, $limite, $umbral, $wrapper);
    }

    /**
     * Pasillo = prefijo de campo7 antes de "-P" (ej. "4MDB04-P6" → "4MDB04").
     * Si campo7 no tiene ese patrón, usa el valor completo; vacío si no hay
     * ubicación asignada (se agrupan aparte como "SIN UBICACIÓN").
     */
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

    /** Agrupa artículos ya ordenados por pasillo → lista de {pasillo, articulos}. */
    private function agruparPorPasillo(array $articulos): array
    {
        $grupos = [];
        foreach ($articulos as $a) {
            $grupos[$a['pasillo']][] = $a;
        }
        $resultado = [];
        foreach ($grupos as $pasillo => $items) {
            $resultado[] = ['pasillo' => $pasillo, 'total' => count($items), 'articulos' => $items];
        }
        return $resultado;
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
     * Artículos con estante < umbral (incluye 0) y depósito > 0 desde
     * st_almac (Profit).
     *
     * @return array<string,array<string,float>> co_art => [estante, deposito]
     */
    private function candidatosDesdeProfit(ConnectionWrapper $w, string $subEstante, string $subDeposito, int $limite, int $umbral): array
    {
        // BUG REAL de driver detectado en vivo (18/08): el driver ODBC nativo
        // "SQL Server" (odbc_prepare/odbc_execute, ver ConnectionWrapper::
        // ejecutarOdbc) falla con "expression of non-boolean type... near
        // ','" o "Incorrect syntax near 'from'" cuando un placeholder '?' se
        // usa DENTRO de un HAVING SUM(CASE WHEN col = ? ...) — probado
        // aislado: el MISMO valor como literal SQL funciona perfecto, así
        // que esta tool NUNCA había podido leer Profit por esta vía (caía
        // siempre al fallback MySQL legacy, que tampoco tiene la tabla). Los
        // sub-almacenes son constantes internas de ProfitStockHelper
        // ('01'/'02'/'04'/'05', nunca input de usuario) y $umbral ya viene
        // validado como int (execute()) — se embeben como literales, solo
        // el WHERE...IN sigue parametrizado (ese sí funciona con '?').
        if (!preg_match('/^\d{2}$/', $subEstante) || !preg_match('/^\d{2}$/', $subDeposito)) {
            throw new \InvalidArgumentException('Sub-almacén inválido.');
        }
        $colAlma = ProfitStockHelper::qSrv('co_alma');
        $colStock = ProfitStockHelper::qSrv('stock_act');
        // Sin alias de tabla: el driver ODBC "SQL Server" rechaza el patrón
        // "FROM tabla WITH (NOLOCK) alias" (SQLSTATE 102/4104 pre-existente).
        $sql = 'SELECT TOP ' . $limite . ' ' . ProfitStockHelper::qSrv('co_art')
             . ' FROM ' . ProfitStockHelper::qSrv('st_almac') . ' WITH (NOLOCK)'
             . ' WHERE ' . $colAlma . " IN ('" . $subEstante . "', '" . $subDeposito . "')"
             . ' GROUP BY ' . ProfitStockHelper::qSrv('co_art')
             . " HAVING SUM(CASE WHEN " . $colAlma . " = '" . $subEstante . "' THEN " . $colStock . " ELSE 0 END) < " . $umbral
             . " AND SUM(CASE WHEN " . $colAlma . " = '" . $subDeposito . "' THEN " . $colStock . " ELSE 0 END) > 0"
             . " ORDER BY SUM(CASE WHEN " . $colAlma . " = '" . $subDeposito . "' THEN " . $colStock . " ELSE 0 END) DESC";
        $candidatos = [];
        foreach ($w->querySafe($sql, []) as $fila) {
            $candidatos[strtoupper(trim((string) $fila['co_art']))] = [];
        }
        return $candidatos;
    }

    /** Fallback: tabla `articulos` del MySQL legacy (ruta previa). */
    private function desdeMySQLLegacy(string $almacen, int $limite, int $umbral, ConnectionWrapper $w): array
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

        $where = '(' . self::q($colEstante) . ' IS NULL OR ' . self::q($colEstante) . ' < ' . $umbral . ')'
               . ' AND ' . self::q($colDeposito) . ' IS NOT NULL AND ' . self::q($colDeposito) . ' > 0';
        $sql = 'SELECT ' . self::q($cols['codigo']) . ' AS co_art'
             . (($cols['nombre'] !== null) ? ', ' . self::q($cols['nombre']) . ' AS nombre' : '')
             . ', ' . self::q($colEstante) . ' AS en_estante'
             . ', ' . self::q($colDeposito) . ' AS en_deposito'
             . (($cols['stock_total'] !== null) ? ', ' . self::q($cols['stock_total']) . ' AS stock_total' : '')
             . ' FROM ' . self::q('articulos') . ' WHERE ' . $where
             . ' ORDER BY ' . self::q($colDeposito) . ' DESC LIMIT ' . $limite;
        try {
            $filas = $pdo->query($sql)->fetchAll(PDO::FETCH_ASSOC);
        } catch (PDOException $e) {
            return ['success' => false, 'error' => 'Error consultando stock prioritario (MySQL legacy): ' . ProfitStockHelper::aUtf8($e->getMessage())];
        }
        $rotacion = [];
        try {
            $rotacion = ProfitStockHelper::rotacion($w, array_column($filas, 'co_art'));
        } catch (PDOException $e) {
            $rotacion = [];
        }
        $articulos = [];
        foreach ($filas as $fila) {
            $co = strtoupper(ProfitStockHelper::aUtf8((string) ($fila['co_art'] ?? '')));
            $articulos[] = [
                'co_art'         => $co,
                'nombre'         => ProfitStockHelper::aUtf8((string) ($fila['nombre'] ?? '')),
                'en_estante'     => (float) ($fila['en_estante'] ?? 0),
                'en_deposito'    => (float) ($fila['en_deposito'] ?? 0),
                'stock_total'    => isset($fila['stock_total']) && $fila['stock_total'] !== null ? (float) $fila['stock_total'] : null,
                'rotacion_venta' => $rotacion[$co] ?? 0,
                // La tabla legacy `articulos` no trae ubicación física (campo7
                // es exclusivo de Profit) — sin pasillo en este fallback.
                'ubicacion'      => '',
                'pasillo'        => 'SIN UBICACIÓN',
            ];
        }
        usort($articulos, static fn (array $a, array $b): int => (int) ($b['rotacion_venta'] <=> $a['rotacion_venta']));
        $pdo = null;
        return [
            'success' => true,
            'data'    => [
                'fuente'     => 'mysql_legacy articulos',
                'almacen'    => $almacen,
                'umbral'     => $umbral,
                'estante_bajo' => 'DESPACHO' . ($almacen === 'bqto' ? '_BQTO' : ''),
                'deposito_con_existencia' => 'DEPOSITO' . ($almacen === 'bqto' ? '_BQTO' : ''),
                'total'      => count($articulos),
                'por_pasillo' => $this->agruparPorPasillo($articulos),
                'articulos'  => $articulos,
            ],
            'card'    => $this->tarjetaArticulos($almacen, 'mysql_legacy articulos', $articulos, $umbral),
        ];
    }

    /** Tarjeta estándar v4.13 de artículos a surtir, agrupados por pasillo (CardBuilder). */
    private function tarjetaArticulos(string $almacen, string $fuente, array $articulos, int $umbral): string
    {
        if ($articulos === []) {
            return CardBuilder::iniciar('✅', 'SIN PENDIENTES DE SURTIDO')
                ->linea('No hay artículos con estante por debajo de ' . $umbral . ' unidad(es) que tengan existencia en depósito.')
                ->footer('Fuente: ' . $fuente)
                ->tarjeta();
        }
        $card = CardBuilder::iniciar('📦', 'SURTIDO PRIORITARIO (' . strtoupper($almacen) . ')')
            ->campo('UMBRAL', '< ' . $umbral . ' und')
            ->campo('TOTAL ARTÍCULOS', count($articulos), 'numero');

        $grupos = $this->agruparPorPasillo($articulos);
        $mostrados = 0;
        foreach ($grupos as $grupo) {
            if ($mostrados >= 12) {
                break;
            }
            $card->seccion('📍 Pasillo ' . $grupo['pasillo'] . ' (' . $grupo['total'] . ')');
            foreach (array_slice($grupo['articulos'], 0, 6) as $a) {
                $card->linea($a['co_art'] . ' · ' . $a['nombre']);
                $card->linea(
                    'estante ' . number_format($a['en_estante'], 0, ',', '.') . ' und · depósito '
                    . number_format($a['en_deposito'], 0, ',', '.') . ' und · rotación ' . $a['rotacion_venta']
                );
                $mostrados++;
            }
            $ocultosGrupo = $grupo['total'] - 6;
            if ($ocultosGrupo > 0) {
                $card->linea('⏩ +' . $ocultosGrupo . ' artículo(s) más en este pasillo.');
            }
        }
        $ocultos = count($articulos) - $mostrados;
        if ($ocultos > 0) {
            $card->linea('⏩ +' . $ocultos . ' artículo(s) más en la consulta (usa "limite" para ver más).');
        }
        return $card
            ->footer('Fuente: ' . $fuente . ' (NOLOCK)')
            ->tarjeta();
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
