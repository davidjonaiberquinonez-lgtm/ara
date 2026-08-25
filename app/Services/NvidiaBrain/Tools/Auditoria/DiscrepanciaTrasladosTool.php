<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Auditoria;

use App\Services\ConnectionWrapper;
use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Almacen\ProfitStockHelper;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;
use PDO;
use PDOException;

require_once __DIR__ . '/../Almacen/ProfitStockHelper.php';
require_once __DIR__ . '/../Common/CardBuilder.php';

/**
 * Concilia los depósitos de bulto cerrado entre San Cristóbal y Barquisimeto.
 *
 * FUENTE PRIMARIA (esquema real verificado en producción): SQL Server Profit
 * PRUEB25 — st_almac por sub-almacén: DEPOSITO SAN CRISTOBAL (02) contra
 * DEPOSITO BARQUISIMETO (05), por artículo (stock_act).
 *
 * Uso típico: cuando se detecta que un ítem no está en el almacén de surtido,
 * verificar si el inventario quedó "atrapado" en el depósito de la otra sede
 * (indicador de traslado pendiente o registro duplicado).
 *
 * FALLBACK: tabla `articulos` del MySQL legacy (192.168.4.148).
 *
 * Contrato de retorno (SIEMPRE array serializable, sin excepciones):
 *   ['success' => true,  'data' => [...]]      → éxito
 *   ['success' => false, 'error' => 'mensaje'] → error controlado
 *
 * Uso:
 *   $registry->registerTool(new DiscrepanciaTrasladosTool());
 */
final class DiscrepanciaTrasladosTool implements AgentToolInterface
{
    private const CAMPOS = [
        'codigo'       => ['co_art', 'codigo', 'cod_art', 'articulo'],
        'nombre'       => ['art_des', 'descripcion', 'nombre', 'nombre_producto'],
        'deposito_sc'  => ['DEPOSITO', 'stock_alm02', 'stock_almacen_02'],
        'deposito_bqto'=> ['DEPOSTO_BQTO', 'stock_alm05', 'stock_almacen_05'],
    ];

    public function getName(): string
    {
        return 'discrepancia_traslados';
    }

    public function getDescription(): string
    {
        return 'Concilia bulto cerrado S/C (sub-almacén 02, DEPOSITO) contra BQTO '
             . '(sub-almacén 05, DEPOSTO_BQTO) en Profit st_almac: reporta '
             . 'artículos cuya existencia difiere entre ambas sedes por encima '
             . 'de un umbral. Parámetros: umbral (default 0), '
             . 'solo_discrepantes (default true) y limite (default 100).';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'umbral' => [
                    'type'        => 'number',
                    'description' => 'Diferencia minima absoluta para reportar (default 0).',
                ],
                'solo_discrepantes' => [
                    'type'        => 'boolean',
                    'description' => 'Si true (default) solo devuelve los articulos con diferencia, si false devuelve todos los comparados.',
                ],
                'limite' => [
                    'type'        => 'integer',
                    'description' => 'Maximo de registros a evaluar (default 100).',
                ],
            ],
            'required' => [],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $umbral = (float) ($arguments['umbral'] ?? 0);
        $soloDiscrepantes = (bool) ($arguments['solo_discrepantes'] ?? true);
        $limite = max(1, min(500, (int) ($arguments['limite'] ?? 100)));

        try {
            $wrapper = new ConnectionWrapper();
            $discrepancias = $this->discrepanciasDesdeProfit($wrapper, $umbral, $soloDiscrepantes, $limite);
        } catch (PDOException $e) {
            return $this->desdeMySQLLegacy($umbral, $soloDiscrepantes, $limite);
        }

        $resumen = ['SC_MAYOR' => 0, 'BQTO_MAYOR' => 0, 'IGUAL' => 0];
        foreach ($discrepancias as $d) {
            $resumen[$d['orientacion']]++;
        }

        return [
            'success' => true,
            'data'    => [
                'fuente'               => 'profit_st_almac (PRUEB25)',
                'almacenes_comparados' => 'DEPOSITO (02 S/C) vs DEPOSTO_BQTO (05 BQTO)',
                'evaluados'            => count($discrepancias),
                'umbral'               => $umbral,
                'total_discrepancias'  => count($discrepancias),
                'resumen'              => $resumen,
                'discrepancias'        => $discrepancias,
            ],
            'card'    => $this->cardDiscrepancias('profit_st_almac (PRUEB25)', $resumen, $discrepancias, $umbral),
        ];
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
     * Comparación real 02 (depósito S/C) vs 05 (depósito BQTO) desde st_almac.
     *
     * @return array<int,array<string,mixed>>
     */
    private function discrepanciasDesdeProfit(ConnectionWrapper $w, float $umbral, bool $soloDiscrepantes, int $limite): array
    {
        $subSc  = ProfitStockHelper::SUB_DEPOSITO_SC;
        $subBqto = ProfitStockHelper::SUB_DEPOSITO_BQTO;
        // Subconsultas derivadas por sede: el driver ODBC "SQL Server" rechaza
        // "FROM tabla WITH (NOLOCK) alias" (SQLSTATE 102/4104 pre-existente) y
        // el self-join necesita alias; el NOLOCK queda dentro de cada subconsulta.
        $sql = 'SELECT TOP ' . $limite
             . ' s2.[co_art] AS co_art, s2.[stock_act] AS deposito_sc, s5.[stock_act] AS deposito_bqto'
             . ' FROM (SELECT co_art, stock_act FROM ' . ProfitStockHelper::qSrv('st_almac') . ' WITH (NOLOCK)'
             . ' WHERE ' . ProfitStockHelper::qSrv('co_alma') . ' = CAST(? AS varchar(10))) s2'
             . ' INNER JOIN (SELECT co_art, stock_act FROM ' . ProfitStockHelper::qSrv('st_almac') . ' WITH (NOLOCK)'
             . ' WHERE ' . ProfitStockHelper::qSrv('co_alma') . ' = CAST(? AS varchar(10))) s5'
             . ' ON s5.[co_art] = s2.[co_art]';
        $params = [$subSc, $subBqto];
        if ($soloDiscrepantes) {
            $sql .= ' AND ABS(s2.[stock_act] - s5.[stock_act]) > ' . sprintf('%.2f', $umbral);
        }
        $sql .= ' ORDER BY ABS(s2.[stock_act] - s5.[stock_act]) DESC';
        $filas = $w->querySafe($sql, $params);

        $codigos = array_map(static fn (array $f): string => strtoupper(trim((string) $f['co_art'])), $filas);
        $maestros = ProfitStockHelper::maestros($w, $codigos);

        $discrepancias = [];
        foreach ($filas as $fila) {
            $co = strtoupper(trim((string) $fila['co_art']));
            $depositoSc  = (float) $fila['deposito_sc'];
            $depositoBqto = (float) $fila['deposito_bqto'];
            $diferencia = $depositoSc - $depositoBqto;
            $discrepancias[] = [
                'co_art'        => $co,
                'nombre'        => ProfitStockHelper::aUtf8((string) ($maestros[$co]['art_des'] ?? '')),
                'deposito_sc'   => $depositoSc,
                'deposito_bqto' => $depositoBqto,
                'diferencia'    => $diferencia,
                'orientacion'   => $diferencia > 0 ? 'SC_MAYOR' : ($diferencia < 0 ? 'BQTO_MAYOR' : 'IGUAL'),
            ];
        }
        return $discrepancias;
    }

    /** Fallback: tabla `articulos` del MySQL legacy (ruta previa). */
    private function desdeMySQLLegacy(float $umbral, bool $soloDiscrepantes, int $limite): array
    {
        try {
            $pdo = $this->conectarMySQL();
        } catch (PDOException $e) {
            return ['success' => false, 'error' => 'Profit no respondió y el MySQL legacy tampoco: ' . ProfitStockHelper::aUtf8($e->getMessage())];
        }
        $cols = $this->resolverColumnas($pdo, 'articulos');
        if ($cols === null || $cols['deposito_sc'] === null || $cols['deposito_bqto'] === null) {
            return ['success' => false, 'error' => 'Profit sin st_almac y la tabla "articulos" no tiene las columnas de depositos en el MySQL legacy.'];
        }
        $sql = 'SELECT ' . self::q($cols['codigo']) . ' AS co_art'
             . (($cols['nombre'] !== null) ? ', ' . self::q($cols['nombre']) . ' AS nombre' : '')
             . ', ' . self::q($cols['deposito_sc']) . ' AS deposito_sc'
             . ', ' . self::q($cols['deposito_bqto']) . ' AS deposito_bqto'
             . ' FROM ' . self::q('articulos') . ' LIMIT ' . $limite;
        try {
            $filas = $pdo->query($sql)->fetchAll(PDO::FETCH_ASSOC);
        } catch (PDOException $e) {
            return ['success' => false, 'error' => 'Error consultando existencias de depositos (MySQL legacy): ' . ProfitStockHelper::aUtf8($e->getMessage())];
        }
        $discrepancias = [];
        $evaluados = 0;
        foreach ($filas as $fila) {
            $depositoSc  = (float) ($fila['deposito_sc'] ?? 0);
            $depositoBqto = (float) ($fila['deposito_bqto'] ?? 0);
            $diferencia = $depositoSc - $depositoBqto;
            $evaluados++;
            if (!$soloDiscrepantes || abs($diferencia) > $umbral) {
                $discrepancias[] = [
                    'co_art'        => ProfitStockHelper::aUtf8((string) $fila['co_art']),
                    'nombre'        => ProfitStockHelper::aUtf8((string) ($fila['nombre'] ?? '')),
                    'deposito_sc'   => $depositoSc,
                    'deposito_bqto' => $depositoBqto,
                    'diferencia'    => $diferencia,
                    'orientacion'   => $diferencia > 0 ? 'SC_MAYOR' : ($diferencia < 0 ? 'BQTO_MAYOR' : 'IGUAL'),
                ];
            }
        }
        $resumen = ['SC_MAYOR' => 0, 'BQTO_MAYOR' => 0, 'IGUAL' => 0];
        foreach ($discrepancias as $d) {
            $resumen[$d['orientacion']]++;
        }
        return [
            'success' => true,
            'data'    => [
                'fuente'               => 'mysql_legacy articulos',
                'almacenes_comparados' => 'DEPOSITO (02 S/C) vs DEPOSTO_BQTO (05 BQTO)',
                'evaluados'            => $evaluados,
                'umbral'               => $umbral,
                'total_discrepancias'  => count($discrepancias),
                'resumen'              => $resumen,
                'discrepancias'        => $discrepancias,
            ],
            'card'    => $this->cardDiscrepancias('mysql_legacy articulos', $resumen, $discrepancias, $umbral),
        ];
    }

    /** Tarjeta estándar v4.13 de discrepancias (CardBuilder). */
    private function cardDiscrepancias(string $fuente, array $resumen, array $discrepancias, float $umbral): string
    {
        $cb = CardBuilder::iniciar('🔀', 'DISCREPANCIAS TRASLADOS S/C ↔ BQTO')
            ->campo('EVALUADOS', count($discrepancias), 'numero')
            ->campo('UMBRAL', $umbral, 'decimal')
            ->seccion('Resumen')
            ->campo('SC > BQTO', (int) ($resumen['SC_MAYOR'] ?? 0), 'numero')
            ->campo('BQTO > SC', (int) ($resumen['BQTO_MAYOR'] ?? 0), 'numero')
            ->campo('IGUALES', (int) ($resumen['IGUAL'] ?? 0), 'numero');
        if ($discrepancias !== []) {
            $cb->seccion('Detalle (top 6)');
            foreach (array_slice($discrepancias, 0, 6) as $d) {
                $cb->campo(
                    (string) $d['co_art'],
                    $d['orientacion'] . ' · dif ' . $d['diferencia']
                );
            }
        }
        return $cb->footer('Fuente: ' . $fuente . ' (NOLOCK)')->tarjeta();
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
