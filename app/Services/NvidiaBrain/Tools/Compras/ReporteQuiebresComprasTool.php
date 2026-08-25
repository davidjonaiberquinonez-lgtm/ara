<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Compras;

use App\Services\ConnectionWrapper;
use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Almacen\ProfitStockHelper;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;
use PDO;
use PDOException;

require_once __DIR__ . '/../Almacen/ProfitStockHelper.php';
require_once __DIR__ . '/../Common/CardBuilder.php';

/**
 * Reporte de quiebres de stock para el departamento de Compras.
 *
 * FUENTE PRIMARIA (esquema real verificado en producción): SQL Server Profit
 * PRUEB25 — tabla `art`: quiebre total = stock_act <= 0 AND anulado = '0'.
 * El laboratorio se aproxima por art.co_prov (proveedor) y la rotación se lee
 * de reng_fac/reng_nde (Tier 1 = con rotación, Tier 2 = resto).
 *
 * FALLBACK: tabla `articulos` del MySQL legacy (192.168.4.148).
 *
 * Contrato de retorno (SIEMPRE array serializable, sin excepciones):
 *   ['success' => true,  'data' => [...]]      → éxito
 *   ['success' => false, 'error' => 'mensaje'] → error controlado
 *
 * Uso:
 *   $registry->registerTool(new ReporteQuiebresComprasTool());
 */
final class ReporteQuiebresComprasTool implements AgentToolInterface
{
    private const CAMPOS = [
        'codigo'      => ['co_art', 'codigo', 'cod_art', 'articulo'],
        'nombre'      => ['art_des', 'descripcion', 'nombre', 'nombre_producto'],
        'stock_total' => ['STOCK_ACT', 'stock', 'existencia', 'stock_actual'],
        'laboratorio' => ['laboratorio', 'lab', 'laborator', 'proveedor'],
    ];

    private const LIMITE = 100;

    public function getName(): string
    {
        return 'reporte_quiebres_compras';
    }

    public function getDescription(): string
    {
        return 'Genera el reporte de quiebres de stock para Compras: artículos '
             . 'con stock total == 0 (Profit art, anulado=0) agrupados por '
             . 'proveedor/laboratorio (co_prov) y clasificados en Tier 1 (alta '
             . 'demanda por rotación de venta en reng_fac/reng_nde) y Tier 2. '
             . 'Úsala para planificar compras y priorizar reposición. '
             . 'Parámetros opcionales: laboratorio (filtrar por co_prov) y '
             . 'limite (default 100).';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'laboratorio' => [
                    'type'        => 'string',
                    'description' => 'Filtra los quiebres de un laboratorio/proveedor específico (co_prov, parcial, insensible a mayúsculas).',
                ],
                'limite' => [
                    'type'        => 'integer',
                    'description' => 'Máximo de quiebres a devolver (default 100).',
                ],
            ],
            'required' => [],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $filtroLab = trim((string) ($arguments['laboratorio'] ?? ''));
        $limite = max(1, min(500, (int) ($arguments['limite'] ?? self::LIMITE)));

        try {
            $w = new ConnectionWrapper();
            $filas = $this->quiebresDesdeProfit($w, $filtroLab, $limite);
        } catch (PDOException $e) {
            $filas = null;
        }

        if (is_array($filas)) {
            return $this->reportar($filas, $filtroLab, 'profit_art (PRUEB25)', $w);
        }
        return $this->desdeMySQLLegacy($filtroLab, $limite);
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
     * @param array<int,array<string,mixed>> $filas
     */
    private function reportar(array $filas, string $filtroLab, string $fuente, ?ConnectionWrapper $w = null): array
    {
        $codigos = array_map(static fn (array $f): string => strtoupper(trim((string) $f['co_art'])), $filas);
        $rotacion = [];
        try {
            $w ??= new ConnectionWrapper();
            $rotacion = ProfitStockHelper::rotacion($w, $codigos);
        } catch (PDOException $e) {
            $rotacion = [];
        }

        $quiebres = [];
        foreach ($filas as $fila) {
            $co = strtoupper(trim((string) $fila['co_art']));
            $quiebres[] = [
                'co_art'         => $co,
                'nombre'         => ProfitStockHelper::aUtf8((string) ($fila['nombre'] ?? '')),
                'laboratorio'    => $fila['laboratorio'] !== '' ? $fila['laboratorio'] : 'S/D',
                'stock_total'    => 0.0,
                'rotacion_venta' => $rotacion[$co] ?? 0,
                'tier'           => ($rotacion[$co] ?? 0) > 0 ? 'TIER_1' : 'TIER_2',
            ];
        }

        $porLaboratorio = [];
        foreach ($quiebres as $q) {
            $porLaboratorio[$q['laboratorio']][] = $q;
        }
        uksort($porLaboratorio, static fn (string $a, string $b): int => strcasecmp($a, $b));

        $data = [
            'fuente'             => $fuente,
            'total_quiebres'     => count($quiebres),
            'filtro_laboratorio' => $filtroLab !== '' ? $filtroLab : null,
            'quiebres'           => $quiebres,
            'agrupado_por_laboratorio' => array_map(
                static fn (string $lab, array $items): array => [
                    'laboratorio' => $lab,
                    'total'       => count($items),
                    'tier_1'      => count(array_filter($items, static fn (array $i): bool => $i['tier'] === 'TIER_1')),
                    'items'       => $items,
                ],
                array_keys($porLaboratorio),
                array_values($porLaboratorio)
            ),
        ];

        $top = $quiebres;
        usort($top, static fn (array $a, array $b): int => ($b['rotacion_venta'] ?? 0) <=> ($a['rotacion_venta'] ?? 0));
        $cb = CardBuilder::iniciar('📉', 'QUIEBRES DE STOCK')
            ->campo('TOTAL QUIEBRES', count($quiebres), 'numero')
            ->campo('FILTRO LAB', $filtroLab !== '' ? $filtroLab : 'ninguno');
        $cb->seccion('Resumen por laboratorio');
        foreach (array_slice($porLaboratorio, 0, 4, true) as $lab => $items) {
            $cb->campo((string) $lab, count($items) . ' quiebre(s)', 'numero');
        }
        $cb->seccion('Prioridad (top 8 por rotación)');
        foreach (array_slice($top, 0, 8) as $q) {
            $cb->campo(
                (string) $q['co_art'],
                $q['tier'] . ' · ' . (string) $q['laboratorio'] . ' · rot ' . (int) $q['rotacion_venta']
            );
        }
        $cb->footer('Fuente: ' . $fuente . ' (NOLOCK)');

        return [
            'success' => true,
            'data'    => $data,
            'card'    => $cb->tarjeta(),
        ];
    }

    /**
     * Quiebres reales desde Profit art (stock_act <= 0, anulado = '0').
     *
     * @return array<int,array<string,mixed>>
     */
    private function quiebresDesdeProfit(ConnectionWrapper $w, string $filtroLab, int $limite): array
    {
        // Sin alias de tabla: el driver ODBC "SQL Server" rechaza el patrón
        // "FROM tabla WITH (NOLOCK) alias" (SQLSTATE 102/4104 pre-existente).
        $sql = 'SELECT TOP ' . $limite
             . ' ' . ProfitStockHelper::qSrv('co_art') . ' AS co_art, '
             . ProfitStockHelper::qSrv('art_des') . ' AS nombre, '
             . ProfitStockHelper::qSrv('co_prov') . ' AS laboratorio, '
             . ProfitStockHelper::qSrv('stock_act')
             . ' FROM ' . ProfitStockHelper::qSrv('art') . ' WITH (NOLOCK)'
             . ' WHERE ' . ProfitStockHelper::qSrv('stock_act') . ' <= 0 AND ' . ProfitStockHelper::qSrv('anulado') . ' = ?';
        $params = ['0'];
        if ($filtroLab !== '') {
            $sql .= ' AND ' . ProfitStockHelper::qSrv('co_prov') . ' LIKE ?';
            $params[] = '%' . str_replace(['%', '_'], ['[%]', '[_]'], $filtroLab) . '%';
        }
        $sql .= ' ORDER BY ' . ProfitStockHelper::qSrv('co_art');
        $filas = $w->querySafe($sql, $params);
        return array_map(
            static fn (array $f): array => [
                'co_art'      => trim((string) $f['co_art']),
                'nombre'      => trim((string) $f['nombre']),
                'laboratorio' => trim((string) ($f['laboratorio'] ?? '')),
            ],
            $filas
        );
    }

    /** Fallback: tabla `articulos` del MySQL legacy (ruta previa). */
    private function desdeMySQLLegacy(string $filtroLab, int $limite): array
    {
        try {
            $pdo = $this->conectarMySQL();
        } catch (PDOException $e) {
            return ['success' => false, 'error' => 'Profit no respondió y el MySQL legacy tampoco: ' . ProfitStockHelper::aUtf8($e->getMessage())];
        }
        $cols = $this->resolverColumnas($pdo, 'articulos');
        if ($cols === null || $cols['stock_total'] === null) {
            return ['success' => false, 'error' => 'Profit sin art y la tabla "articulos" no existe o no tiene stock total en el MySQL legacy.'];
        }
        $where = '(' . self::q($cols['stock_total']) . ' = 0 OR ' . self::q($cols['stock_total']) . ' IS NULL)';
        $params = [];
        if ($filtroLab !== '' && $cols['laboratorio'] !== null) {
            $where .= ' AND ' . self::q($cols['laboratorio']) . ' LIKE ?';
            $params[] = '%' . str_replace(['%', '_'], ['\\%', '\\_'], $filtroLab) . '%';
        }
        $sql = 'SELECT ' . self::q($cols['codigo']) . ' AS co_art'
             . (($cols['nombre'] !== null) ? ', ' . self::q($cols['nombre']) . ' AS nombre' : '')
             . (($cols['laboratorio'] !== null) ? ', ' . self::q($cols['laboratorio']) . ' AS laboratorio' : '')
             . ' FROM ' . self::q('articulos') . ' WHERE ' . $where . ' LIMIT ' . $limite;
        try {
            $stmt = $pdo->prepare($sql);
            $stmt->execute($params);
            $filas = $stmt->fetchAll(PDO::FETCH_ASSOC);
        } catch (PDOException $e) {
            return ['success' => false, 'error' => 'Error consultando quiebres (MySQL legacy): ' . ProfitStockHelper::aUtf8($e->getMessage())];
        }
        $filas = array_map(
            static fn (array $f): array => [
                'co_art'      => trim((string) $f['co_art']),
                'nombre'      => trim((string) ($f['nombre'] ?? '')),
                'laboratorio' => trim((string) ($f['laboratorio'] ?? 'S/D')),
            ],
            $filas
        );
        return $this->reportar($filas, $filtroLab, 'mysql_legacy articulos');
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
        foreach (self::CAMPOS as $campo => $candidatos) {
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
