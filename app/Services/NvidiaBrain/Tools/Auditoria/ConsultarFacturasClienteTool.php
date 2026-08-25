<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Auditoria;

use App\Services\ConnectionWrapper;
use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;
use PDOException;

require_once __DIR__ . '/../Common/CardBuilder.php';

/**
 * Herramienta "consultar_facturas_cliente": lista las facturas ACTIVAS
 * (no anuladas) con saldo pendiente de un cliente — pendientes por pagar o
 * ya vencidas — directo de Profit (tabla `factura`, confirmada en vivo
 * contra CRISTM25/PRUEB25 el 18/08).
 *
 * Distinta de consultar_saldo_cliente (Auditoria/ConsultarSaldoClienteTool):
 * esa da el RESUMEN de cartera (saldo total, límite, descuento) buscando
 * por RIF/cédula/código y con conversión a USD vía tasa histórica. Esta es
 * el DETALLE documento-por-documento, buscando específicamente por código
 * de cliente (co_cli), con la fecha de vencimiento real de cada factura y
 * su estado (VENCIDA / POR VENCER) — para saber exactamente cuáles
 * facturas cobrar y desde cuándo.
 *
 * Contrato de retorno (SIEMPRE array serializable, sin excepciones):
 *   ['success' => true,  'data' => [...]]          → éxito
 *   ['success' => false, 'error' => 'mensaje']     → error controlado
 */
final class ConsultarFacturasClienteTool implements AgentToolInterface
{
    /** Tablas candidatas de facturas (primera = confirmada en vivo). */
    private const TABLAS_FACTURA = ['factura', 'FacturasDrogueria'];

    /** Sinónimos de columna por campo lógico. */
    private const CAMPOS_FACTURA = [
        'doc'      => ['fact_num', 'num_doc', 'documento'],
        'cliente'  => ['co_cli', 'cliente_id', 'id_cliente'],
        'saldo'    => ['saldo', 'saldo_pendiente', 'monto_pendiente'],
        'monto'    => ['tot_neto', 'monto_total', 'total_neto', 'monto'],
        'emision'  => ['fec_emis', 'fecha_emision', 'fecha'],
        'vencim'   => ['fec_venc', 'fecha_vencimiento', 'fec_ven'],
        'anulada'  => ['anulada'],
        'moneda'   => ['moneda', 'co_mone'],
        'status'   => ['status', 'estatus', 'estado'],
        'tasa'     => ['tasa', 'tasag'],
    ];

    public function getName(): string
    {
        return 'consultar_facturas_cliente';
    }

    public function getDescription(): string
    {
        return 'Lista las facturas ACTIVAS de un cliente que tienen saldo '
             . 'pendiente por pagar en Profit (SQL Server), marcando cuáles '
             . 'ya están VENCIDAS (fecha de vencimiento pasada) y cuáles '
             . 'siguen POR VENCER. Búsqueda EXCLUSIVA por código de cliente '
             . '(co_cli, exacto o parcial) — para el estado de cuenta general '
             . '(saldo/límite/crédito) buscando por RIF o cédula, usa '
             . 'consultar_saldo_cliente. Úsala cuando el usuario pregunte qué '
             . 'facturas o notas debe cobrar/pagar un cliente, cuáles están '
             . 'vencidas, o el detalle de su deuda documento por documento. '
             . 'Parámetro: co_cli (obligatorio). Opcional: solo_vencidas '
             . '(true = solo las que ya vencieron).';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'co_cli' => [
                    'type'        => 'string',
                    'description' => 'Código del cliente en Profit (ej. "FAR01680", exacto o parcial).',
                ],
                'solo_vencidas' => [
                    'type'        => 'boolean',
                    'description' => 'Si es true, solo devuelve facturas con fecha de vencimiento ya pasada (default: false, muestra todas las pendientes).',
                ],
            ],
            'required' => ['co_cli'],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $coCli = trim((string) ($arguments['co_cli'] ?? ''));
        if ($coCli === '') {
            return ['success' => false, 'error' => 'Parámetro "co_cli" es obligatorio.'];
        }
        $soloVencidas = (bool) ($arguments['solo_vencidas'] ?? false);

        $wrapper = new ConnectionWrapper();
        $tabla = $wrapper->resolverTabla(self::TABLAS_FACTURA);
        if ($tabla === null) {
            try {
                $wrapper->queryProfit('SELECT 1');
            } catch (PDOException $e) {
                return ['success' => false, 'error' => 'No se pudo conectar a SQL Server: ' . self::aUtf8($e->getMessage())];
            }
            return [
                'success' => false,
                'error'   => sprintf(
                    'Ninguna tabla de facturas encontrada (buscadas: %s).',
                    implode(', ', self::TABLAS_FACTURA)
                ),
            ];
        }

        $cols = $wrapper->resolverColumnas($tabla, self::CAMPOS_FACTURA);
        if ($cols['doc'] === null || $cols['cliente'] === null || $cols['saldo'] === null) {
            return [
                'success' => false,
                'error'   => sprintf('La tabla %s no tiene las columnas mínimas (documento/cliente/saldo).', $tabla),
            ];
        }

        $select = 'SELECT TOP 100 ' . self::q($cols['doc']) . ' AS doc'
             . ', ' . self::q($cols['saldo']) . ' AS saldo'
             . ($cols['monto'] !== null ? ', ' . self::q($cols['monto']) . ' AS monto' : '')
             . ($cols['emision'] !== null ? ', ' . self::q($cols['emision']) . ' AS emision' : '')
             . ($cols['vencim'] !== null ? ', ' . self::q($cols['vencim']) . ' AS vencim' : '')
             . ($cols['moneda'] !== null ? ', ' . self::q($cols['moneda']) . ' AS moneda' : '')
             . ($cols['status'] !== null ? ', ' . self::q($cols['status']) . ' AS status' : '')
             . ($cols['tasa'] !== null ? ', ' . self::q($cols['tasa']) . ' AS tasa' : '')
             . ' FROM ' . self::q($tabla) . ' WITH (NOLOCK)'
             . ' WHERE ' . $this->coincidenciaNormalizada($cols['cliente']) . ' LIKE ?'
             . ' AND ISNULL(' . self::q($cols['saldo']) . ', 0) > 0';
        if ($cols['anulada'] !== null) {
            $select .= ' AND ISNULL(' . self::q($cols['anulada']) . ', 0) = 0';
        }
        $select .= ' ORDER BY ' . ($cols['vencim'] !== null ? self::q($cols['vencim']) : self::q($cols['doc'])) . ' ASC';

        $normCli = $this->normalizar($coCli);
        try {
            $filas = $wrapper->querySafe($select, ['%' . $normCli . '%']);
        } catch (\InvalidArgumentException $e) {
            return ['success' => false, 'error' => 'Error al consultar facturas: ' . $e->getMessage()];
        } catch (PDOException $e) {
            return ['success' => false, 'error' => 'Error al consultar facturas: ' . self::aUtf8($e->getMessage())];
        }

        // Nombre del cliente (denormalizado en `factura.nombre` suele venir
        // vacío en este ERP) — se resuelve aparte contra `clientes`.
        $nombreCliente = $this->resolverNombreCliente($wrapper, $coCli);

        $hoy = (int) floor(time() / 86400);
        $facturas = [];
        $totalPendiente = 0.0;
        $totalPendienteUsd = 0.0;
        $totalVencidas = 0;
        foreach ($filas as $fila) {
            $vencim = (string) ($fila['vencim'] ?? '');
            $diasVencidos = $vencim !== '' ? $this->diasVencidos($vencim, $hoy) : 0;
            $vencida = $diasVencidos > 0;
            if ($soloVencidas && !$vencida) {
                continue;
            }
            $saldo = $fila['saldo'] !== null && $fila['saldo'] !== '' ? (float) $fila['saldo'] : 0.0;
            $totalPendiente += $saldo;
            if ($vencida) {
                $totalVencidas++;
            }
            // BUG REAL detectado en vivo (18/08): `saldo`/`tot_neto` SIEMPRE
            // vienen en Bs, aunque `moneda` diga 'US$' (esa columna indica
            // que la factura está INDEXADA en dólares, no que el monto ya
            // esté en dólares). Antes se mostraba el monto en Bs con la
            // etiqueta "US$" pegada — el valor real en USD es saldo/tasa,
            // usando la tasa del DÍA DE ESA FACTURA (columna `tasa`, ya
            // viene guardada por factura — no hace falta ir a buscarla a
            // una tabla histórica como en ConsultarSaldoClienteTool).
            $tasa = $cols['tasa'] !== null && $fila['tasa'] !== null && $fila['tasa'] !== ''
                ? (float) $fila['tasa'] : null;
            $saldoUsd = ($tasa !== null && $tasa > 0) ? round($saldo / $tasa, 2) : null;
            if ($saldoUsd !== null) {
                $totalPendienteUsd += $saldoUsd;
            }
            $facturas[] = [
                'documento'         => self::aUtf8((string) ($fila['doc'] ?? '')),
                'fecha_emision'     => (string) ($fila['emision'] ?? ''),
                'fecha_vencimiento' => $vencim,
                'saldo_pendiente_bs'=> $saldo,
                'saldo_pendiente_usd' => $saldoUsd,
                'tasa_dia'          => $tasa,
                'monto_original'    => $cols['monto'] !== null && $fila['monto'] !== null && $fila['monto'] !== ''
                    ? (float) $fila['monto'] : $saldo,
                'moneda'            => $cols['moneda'] !== null ? trim(self::aUtf8((string) ($fila['moneda'] ?? ''))) : null,
                'estatus_legacy'    => $cols['status'] !== null ? trim(self::aUtf8((string) ($fila['status'] ?? ''))) : null,
                'dias_vencidos'     => $diasVencidos,
                'estado'            => $vencida ? 'VENCIDA' : 'POR_VENCER',
            ];
        }

        return [
            'success' => true,
            'data'    => [
                'tabla_origen'       => $tabla,
                'co_cli'             => $coCli,
                'nombre_cliente'     => $nombreCliente,
                'total_facturas'     => count($facturas),
                'total_vencidas'     => $totalVencidas,
                'total_por_vencer'   => count($facturas) - $totalVencidas,
                'saldo_total_pendiente_bs'  => round($totalPendiente, 2),
                'saldo_total_pendiente_usd' => round($totalPendienteUsd, 2),
                'facturas'           => $facturas,
            ],
            'card'    => $this->cardFacturas($coCli, $nombreCliente, $facturas, $totalPendiente, $totalPendienteUsd, $totalVencidas, $tabla),
        ];
    }

    /** Tarjeta de facturas pendientes/vencidas (CardBuilder). */
    private function cardFacturas(
        string $coCli,
        string $nombreCliente,
        array $facturas,
        float $totalPendiente,
        float $totalPendienteUsd,
        int $totalVencidas,
        string $tabla
    ): string {
        $icono = $totalVencidas > 0 ? '🔴' : '🧾';
        $cb = CardBuilder::iniciar($icono, 'FACTURAS PENDIENTES — ' . $coCli)
            ->campo('CLIENTE', $nombreCliente !== '' ? $nombreCliente : 'N/D')
            ->campo('TOTAL FACTURAS', count($facturas), 'numero')
            ->campo('VENCIDAS', $totalVencidas, 'numero')
            ->campo('SALDO PENDIENTE', round($totalPendiente, 2), 'moneda');
        if ($totalPendienteUsd > 0) {
            $cb->campo('SALDO PENDIENTE USD', '$' . number_format($totalPendienteUsd, 2), 'texto');
        }

        if ($facturas === []) {
            $cb->linea('✅ Sin facturas pendientes por cobrar.');
        } else {
            $cb->seccion('Detalle (ordenado por vencimiento)');
            foreach (array_slice($facturas, 0, 15) as $f) {
                $marca = $f['estado'] === 'VENCIDA' ? '🔴' : '🟡';
                $usd = $f['saldo_pendiente_usd'] !== null
                    ? ' (≈ $' . number_format($f['saldo_pendiente_usd'], 2) . ')' : '';
                $cb->linea(
                    $marca . ' ' . $f['documento']
                    . ' · vence ' . ($f['fecha_vencimiento'] !== '' ? substr($f['fecha_vencimiento'], 0, 10) : 's/fecha')
                    . ($f['estado'] === 'VENCIDA' ? ' (' . $f['dias_vencidos'] . ' días vencida)' : ' (por vencer)')
                    . ' · Bs ' . number_format($f['saldo_pendiente_bs'], 2) . $usd
                );
            }
            $ocultas = count($facturas) - 15;
            if ($ocultas > 0) {
                $cb->linea('⏩ +' . $ocultas . ' factura(s) más en la consulta.');
            }
        }
        return $cb->footer('Fuente: ' . $tabla . ' (Profit SQL Server, NOLOCK)')->tarjeta();
    }

    /** Nombre del cliente vía tabla `clientes` (best-effort, nunca falla). */
    private function resolverNombreCliente(ConnectionWrapper $wrapper, string $coCli): string
    {
        try {
            $colNombre = $wrapper->resolverColumnas('clientes', [
                'nombre' => ['cli_des', 'nombre', 'razon_social'],
            ])['nombre'] ?? null;
            if ($colNombre === null) {
                return '';
            }
            $filas = $wrapper->querySafe(
                'SELECT TOP 1 ' . self::q($colNombre) . ' AS nombre FROM [clientes] WITH (NOLOCK) '
                . 'WHERE RTRIM(LTRIM(co_cli)) = ?',
                [trim($coCli)]
            );
            return $filas !== [] ? trim(self::aUtf8((string) ($filas[0]['nombre'] ?? ''))) : '';
        } catch (\Throwable $e) {
            return '';
        }
    }

    /** Días vencidos respecto a hoy (nunca negativo; 0 = no vencida aún). */
    private function diasVencidos(string $fechaVencimiento, int $hoyDias): int
    {
        $f = preg_replace('/[T ]/', ' ', trim($fechaVencimiento));
        $f = (string) substr((string) $f, 0, 19);
        $ts = strtotime($f);
        if ($ts === false) {
            return 0;
        }
        $diaVenc = (int) floor($ts / 86400);
        return max(0, $hoyDias - $diaVenc);
    }

    /** Expresión SQL que compara una columna ignorando separadores y mayúsculas. */
    private function coincidenciaNormalizada(string $columna): string
    {
        $col = self::q($columna);
        return "REPLACE(REPLACE(REPLACE(REPLACE($col,'-',''),'.',''),'/',''),' ','')";
    }

    /** Normaliza un identificador: mayúsculas y sin caracteres no alfanuméricos. */
    private function normalizar(string $texto): string
    {
        return preg_replace('/[^A-Za-z0-9]/', '', strtoupper($texto)) ?? '';
    }

    /** Envuelve un identificador validado en corchetes de SQL Server. */
    private static function q(string $identificador): string
    {
        if (preg_match('/^[A-Za-z_][A-Za-z0-9_]*$/', $identificador) !== 1) {
            throw new \RuntimeException('Identificador SQL inválido: ' . $identificador);
        }
        return '[' . $identificador . ']';
    }

    /** Normaliza texto del ERP a UTF-8 (defensa adicional, ver ConnectionWrapper::ejecutar). */
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
