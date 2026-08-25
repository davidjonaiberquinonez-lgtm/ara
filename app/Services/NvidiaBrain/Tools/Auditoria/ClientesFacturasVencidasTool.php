<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Auditoria;

use App\Services\ConnectionWrapper;
use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;
use PDOException;

require_once __DIR__ . '/../Common/CardBuilder.php';

/**
 * Herramienta "clientes_facturas_vencidas": lista GLOBAL de clientes con
 * facturas YA VENCIDAS (fecha de vencimiento pasada, saldo pendiente > 0,
 * no anuladas) en Profit (tabla `factura`), agrupados por cliente con el
 * conteo de cuántas facturas vencidas tiene cada uno y el saldo total.
 *
 * Distinta de consultar_facturas_cliente (detalle documento-por-documento de
 * UN cliente conocido): esta es el reporte de cobranza — "¿a quién le debo
 * cobrar y cuántas facturas vencidas tiene cada uno?" — SOLO vencidas
 * (fec_venc < hoy), nunca "por vencer".
 *
 * Contrato de retorno (SIEMPRE array serializable, sin excepciones):
 *   ['success' => true,  'data' => [...]]      → éxito
 *   ['success' => false, 'error' => 'mensaje'] → error controlado
 */
final class ClientesFacturasVencidasTool implements AgentToolInterface
{
    private const LIMITE = 30;

    public function getName(): string
    {
        return 'clientes_facturas_vencidas';
    }

    public function getDescription(): string
    {
        return 'Lista GLOBAL de clientes con facturas YA VENCIDAS en Profit '
             . '(fecha de vencimiento pasada, saldo pendiente > 0) — NO incluye '
             . 'facturas por vencer. Agrupa por cliente con el CONTEO de cuántas '
             . 'facturas vencidas tiene cada uno y el saldo vencido total, '
             . 'ordenado de mayor a menor. Úsala cuando el usuario pida la lista '
             . 'de clientes morosos/con facturas vencidas para cobranza. '
             . 'Parámetros: limite (default 30, máx 100) y orden (cantidad|saldo, '
             . 'default cantidad).';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'limite' => [
                    'type'        => 'integer',
                    'description' => 'Máximo de clientes a devolver (default 30, máx 100).',
                ],
                'orden' => [
                    'type'        => 'string',
                    'enum'        => ['cantidad', 'saldo'],
                    'description' => 'Criterio de orden: "cantidad" (más facturas vencidas primero, default) o "saldo" (mayor deuda vencida primero).',
                ],
            ],
            'required' => [],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $limite = max(1, min(100, (int) ($arguments['limite'] ?? self::LIMITE)));
        $orden  = strtolower(trim((string) ($arguments['orden'] ?? 'cantidad')));
        if (!in_array($orden, ['cantidad', 'saldo'], true)) {
            $orden = 'cantidad';
        }

        $wrapper = new ConnectionWrapper();
        try {
            $wrapper->queryProfit('SELECT 1');
        } catch (PDOException $e) {
            return ['success' => false, 'error' => 'No se pudo conectar a SQL Server: ' . self::aUtf8($e->getMessage())];
        }

        $ordenSql = $orden === 'saldo' ? 'saldo_vencido DESC' : 'num_vencidas DESC';
        $sql = 'SELECT TOP ' . $limite . ' co_cli, COUNT(*) AS num_vencidas, SUM(saldo) AS saldo_vencido'
             . ' FROM factura'
             . ' WHERE ISNULL(saldo, 0) > 0'
             . ' AND ISNULL(anulada, 0) = 0'
             . ' AND fec_venc < GETDATE()'
             . ' GROUP BY co_cli'
             . ' ORDER BY ' . $ordenSql;
        try {
            $filas = $wrapper->querySafe($sql, []);
        } catch (\InvalidArgumentException $e) {
            return ['success' => false, 'error' => 'Error al consultar facturas vencidas: ' . $e->getMessage()];
        } catch (PDOException $e) {
            return ['success' => false, 'error' => 'Error al consultar facturas vencidas: ' . self::aUtf8($e->getMessage())];
        }

        $codigos = array_map(static fn (array $f): string => trim((string) $f['co_cli']), $filas);
        $nombres = $this->resolverNombresClientes($wrapper, $codigos);

        $clientes = [];
        $totalFacturasVencidas = 0;
        $totalSaldoVencido = 0.0;
        foreach ($filas as $fila) {
            $coCli = trim((string) $fila['co_cli']);
            $numVencidas = (int) $fila['num_vencidas'];
            $saldoVencido = (float) ($fila['saldo_vencido'] ?? 0);
            $totalFacturasVencidas += $numVencidas;
            $totalSaldoVencido += $saldoVencido;
            $clientes[] = [
                'co_cli'             => $coCli,
                'nombre'             => $nombres[$coCli] ?? '',
                'facturas_vencidas'  => $numVencidas,
                'saldo_vencido_bs'   => round($saldoVencido, 2),
            ];
        }

        return [
            'success' => true,
            'data'    => [
                'fuente'                  => 'profit_factura (PRUEB25)',
                'total_clientes'          => count($clientes),
                'total_facturas_vencidas' => $totalFacturasVencidas,
                'saldo_vencido_total_bs'  => round($totalSaldoVencido, 2),
                'orden'                   => $orden,
                'clientes'                => $clientes,
            ],
            'card'    => $this->card($clientes, $totalFacturasVencidas, $totalSaldoVencido),
        ];
    }

    /** Tarjeta de clientes con facturas vencidas (CardBuilder). */
    private function card(array $clientes, int $totalFacturasVencidas, float $totalSaldoVencido): string
    {
        if ($clientes === []) {
            return CardBuilder::iniciar('✅', 'SIN CLIENTES CON FACTURAS VENCIDAS')
                ->linea('Ningún cliente tiene facturas con vencimiento pasado y saldo pendiente.')
                ->footer('Fuente: profit_factura (PRUEB25)')
                ->tarjeta();
        }
        $card = CardBuilder::iniciar('🔴', 'CLIENTES CON FACTURAS VENCIDAS')
            ->campo('CLIENTES', count($clientes), 'numero')
            ->campo('FACTURAS VENCIDAS', $totalFacturasVencidas, 'numero')
            ->campo('SALDO VENCIDO TOTAL', round($totalSaldoVencido, 2), 'moneda')
            ->seccion('Por cliente (mayor a menor)');
        foreach (array_slice($clientes, 0, 15) as $c) {
            $nombre = $c['nombre'] !== '' ? $c['nombre'] : 'N/D';
            $card->linea('🔴 ' . $c['co_cli'] . ' · ' . $nombre);
            $card->linea('  ' . $c['facturas_vencidas'] . ' factura(s) vencida(s) · Bs ' . number_format($c['saldo_vencido_bs'], 2));
        }
        $ocultos = count($clientes) - 15;
        if ($ocultos > 0) {
            $card->linea('⏩ +' . $ocultos . ' cliente(s) más (usa "limite" para ver más).');
        }
        return $card
            ->footer('Fuente: profit_factura (PRUEB25, NOLOCK)')
            ->tarjeta();
    }

    /**
     * Nombres de clientes vía tabla `clientes` (best-effort, nunca falla).
     *
     * @param array<int,string> $codigos
     *
     * @return array<string,string> co_cli => nombre
     */
    private function resolverNombresClientes(ConnectionWrapper $wrapper, array $codigos): array
    {
        $codigos = array_values(array_unique(array_filter($codigos, static fn (string $c) => $c !== '')));
        if ($codigos === []) {
            return [];
        }
        try {
            $colNombre = $wrapper->resolverColumnas('clientes', [
                'nombre' => ['cli_des', 'nombre', 'razon_social'],
            ])['nombre'] ?? null;
            if ($colNombre === null) {
                return [];
            }
            $marks = implode(',', array_fill(0, count($codigos), '?'));
            $filas = $wrapper->querySafe(
                'SELECT co_cli, ' . self::q($colNombre) . ' AS nombre FROM clientes'
                . ' WHERE RTRIM(LTRIM(co_cli)) IN (' . $marks . ')',
                $codigos
            );
            $mapa = [];
            foreach ($filas as $fila) {
                $mapa[trim((string) $fila['co_cli'])] = trim(self::aUtf8((string) ($fila['nombre'] ?? '')));
            }
            return $mapa;
        } catch (\Throwable $e) {
            return [];
        }
    }

    public function getDefinition(): array
    {
        return [
            'name'        => $this->getName(),
            'description' => $this->getDescription(),
            'parameters'  => $this->getParameters(),
        ];
    }

    /** Envuelve un identificador validado en corchetes de SQL Server. */
    private static function q(string $identificador): string
    {
        if (preg_match('/^[A-Za-z_][A-Za-z0-9_]*$/', $identificador) !== 1) {
            throw new \RuntimeException('Identificador SQL inválido: ' . $identificador);
        }
        return '[' . $identificador . ']';
    }

    /** Normaliza texto del ERP a UTF-8 (defensa adicional). */
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
