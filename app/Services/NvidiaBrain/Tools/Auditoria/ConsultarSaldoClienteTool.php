<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Auditoria;

use App\Services\ConnectionWrapper;
use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;
use PDOException;

require_once __DIR__ . '/../Common/CardBuilder.php';

/**
 * Consulta saldo, límite de crédito y cartera de un cliente del ERP (SQL Server).
 *
 * Consulta la tabla ClientesDrogueria (ERP Droguería); si no existe, hace
 * fallback a la tabla "clientes" del Profit. La identificación acepta RIF,
 * cédula o código de cliente (con y sin separadores). Los nombres de columna
 * se resuelven dinámicamente vía INFORMATION_SCHEMA. La cartera (facturas
 * pendientes) se calcula sobre FacturasDrogueria o "factura" si está disponible.
 *
 * Contrato de retorno (SIEMPRE array serializable, sin excepciones):
 *   ['success' => true,  'data' => [...]]          → éxito
 *   ['success' => false, 'error' => 'mensaje']     → error controlado
 *
 * Configuración (variables de entorno, con defaults):
 *   PROFIT_SQL_HOST/PORT/USER/PASS/NAME/DRIVER  (fallback PROFIT_DB_* y luego
 *   defaults: 192.168.4.20:1433, profit/profit, PRUEB25, "SQL Server").
 *
 * Uso:
 *   $registry->registerTool(new ConsultarSaldoClienteTool());
 */
final class ConsultarSaldoClienteTool implements AgentToolInterface
{
    /** Tablas candidatas de clientes, en orden de preferencia. */
    private const TABLAS = ['ClientesDrogueria', 'clientes'];

    /** Tablas candidatas de facturas para la cartera pendiente. */
    private const TABLAS_FACTURAS = ['FacturasDrogueria', 'factura'];

    /** Sinónimos de columna por campo lógico (primer candidato existente gana). */
    private const CAMPOS = [
        'id'        => ['co_cli', 'cli_id', 'id_cliente', 'cliente_id', 'Id'],
        'rif'       => ['RIF', 'rif', 'cliente_rif', 'no_rif', 'cl_rif'],
        'nombre'    => ['cli_des', 'nombre', 'razon_social', 'descripcion', 'cliente'],
        'limite'    => ['mont_cre', 'limite', 'limite_credito', 'credito_max', 'cupo'],
        'saldo'     => ['saldo', 'saldo_actual', 'deuda', 'deuda_actual'],
        'descuento' => ['descuento_unico', 'descto', 'descuento', 'por_descuento', 'por_desc', 'desc_glob', 'desc_ppago'],
    ];

    /** Sinónimos para la tabla de facturas (cartera). */
    private const CAMPOS_FACTURAS = [
        'cliente' => ['co_cli', 'Id', 'cliente_id', 'id_cliente'],
        'doc'     => ['fact_num', 'num_doc', 'documento', 'doc_num'],
        'fecha'   => ['fecha_emision', 'fec_emis', 'fec_lac', 'fecha', 'fec_ven'],
        'monto'   => ['monto', 'monto_total', 'total_neto', 'total_venta', 'neto'],
        'saldo'   => ['saldo', 'monto_pendiente', 'pendiente', 'saldo_pendiente', 'saldo_restante'],
    ];

    /** Sinónimos para saTasaHistorico (tasa de cambio histórica). */
    private const CAMPOS_TASA = [
        'tasa'   => ['tasa', 'precio', 'valor', 'tasa_venta'],
        'fecha'  => ['fec_visi', 'fecha', 'fec', 'fec_emis'],
        'moneda' => ['co_mone', 'moneda', 'id_moneda'],
    ];

    public function getName(): string
    {
        return 'consultar_saldo_cliente';
    }

    public function getDescription(): string
    {
        return 'Consulta el saldo actual, el límite de crédito y la cartera de '
             . 'facturas pendientes de un cliente del ERP (SQL Server), buscando '
             . 'por RIF, cédula o código de cliente. Úsala cuando el usuario '
             . 'pregunte por el estado de cuenta, deuda, cupo o crédito de un '
             . 'cliente. Parámetro: identificador_cliente (RIF, documento o ID).';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'identificador_cliente' => [
                    'type'        => 'string',
                    'description' => 'RIF, cédula o código de cliente (acepta separadores como guiones y puntos).',
                ],
            ],
            'required' => ['identificador_cliente'],
        ];
    }

    /**
     * {@inheritDoc}
     */
    public function execute(array $arguments, array $contexto): array
    {
        $identificador = trim((string) ($arguments['identificador_cliente'] ?? ''));
        if ($identificador === '') {
            return [
                'success' => false,
                'error'   => 'Parámetro "identificador_cliente" es obligatorio (RIF, cédula o código de cliente).',
            ];
        }

        // ── 1) Conexión protegida (ConnectionWrapper v4.14) y resolución de tabla ──
        $wrapper = new ConnectionWrapper();
        $tabla = $wrapper->resolverTabla(self::TABLAS);
        if ($tabla === null) {
            try {
                $wrapper->queryProfit('SELECT 1');
            } catch (PDOException $e) {
                return ['success' => false, 'error' => 'No se pudo conectar a SQL Server: ' . self::aUtf8($e->getMessage())];
            }
            return [
                'success' => false,
                'error'   => sprintf(
                    'Ninguna tabla de clientes encontrada (buscadas: %s). Verifique la conexión o la configuración PROFIT_SQL_*.',
                    implode(', ', self::TABLAS)
                ),
            ];
        }

        $cols = $wrapper->resolverColumnas($tabla, self::CAMPOS);
        if ($cols['id'] === null) {
            return [
                'success' => false,
                'error'   => sprintf(
                    'La tabla %s no tiene una columna de identificación de cliente (sinónimos esperados: %s).',
                    $tabla,
                    implode(', ', self::CAMPOS['id'])
                ),
            ];
        }

        // ── 2) Búsqueda del cliente (coincidencia normalizada) ────────────────
        $norm = $this->normalizar($identificador);

        $sql = 'SELECT TOP 5 ' . self::q($cols['id']) . ' AS identificador'
             . ($cols['nombre'] !== null ? ', ' . self::q($cols['nombre']) . ' AS nombre' : '')
             . ($cols['rif'] !== null ? ', ' . self::q($cols['rif']) . ' AS rif' : '')
             . ($cols['limite'] !== null ? ', ' . self::q($cols['limite']) . ' AS limite' : '')
             . ($cols['saldo'] !== null ? ', ' . self::q($cols['saldo']) . ' AS saldo' : '')
             . ($cols['descuento'] !== null ? ', ' . self::q($cols['descuento']) . ' AS descuento' : '')
             . ' FROM ' . self::q($tabla) . ' WITH (NOLOCK)'
             . ' WHERE ' . $this->coincidenciaNormalizada($cols['id']) . ' = ?';
        $params = [$norm];

        if ($cols['rif'] !== null) {
            $sql .= ' OR ' . $this->coincidenciaNormalizada($cols['rif']) . ' = ?';
            $params[] = $norm;
        }
        $sql .= ' ORDER BY ' . self::q($cols['id']);

        try {
            $filas = $wrapper->querySafe($sql, $params);
        } catch (\InvalidArgumentException $e) {
            return ['success' => false, 'error' => 'Error al consultar el cliente: ' . $e->getMessage()];
        } catch (PDOException $e) {
            return ['success' => false, 'error' => 'Error al consultar el cliente: ' . self::aUtf8($e->getMessage())];
        }

        if ($filas === []) {
            return [
                'success' => false,
                'error'   => sprintf('No se encontró ningún cliente con el identificador "%s" en %s.', $identificador, $tabla),
            ];
        }

        $fila = $filas[0];
        $cliente = [
            'identificador'    => self::aUtf8($fila['identificador'] ?? ''),
            'nombre'           => self::aUtf8($fila['nombre'] ?? ''),
            'rif'              => self::aUtf8($fila['rif'] ?? ''),
            'limite_credito'   => $fila['limite'] ?? null,
            'saldo_actual'     => $fila['saldo'] ?? null,
            'descuento_unico'  => $fila['descuento'] ?? null,
        ];
        $cliente['limite_credito'] = $cliente['limite_credito'] !== null && $cliente['limite_credito'] !== ''
            ? (float) $cliente['limite_credito'] : null;
        $cliente['saldo_actual'] = $cliente['saldo_actual'] !== null && $cliente['saldo_actual'] !== ''
            ? (float) $cliente['saldo_actual'] : null;
        $cliente['descuento_unico'] = $cliente['descuento_unico'] !== null && $cliente['descuento_unico'] !== ''
            ? (float) $cliente['descuento_unico'] : null;
        $cliente = array_filter($cliente, static fn ($v): bool => $v !== '' && $v !== null);
        // AD5: el campo descuento_unico se documenta SIEMPRE (null si no existe).
        $cliente['descuento_unico'] = $cliente['descuento_unico'] ?? null;

        // ── 3) Cartera de facturas pendientes (best-effort) ───────────────────
        $cartera = $this->calcularCartera($wrapper, (string) $cliente['identificador']);

        return [
            'success' => true,
            'data'    => [
                'tabla_origen'    => $tabla,
                'coincidencias'   => count($filas),
                'cliente'         => $cliente,
                'cartera'         => $cartera,
            ],
            'card'    => $this->cardSaldo($tabla, $cliente, $cartera),
        ];
    }

    /** Tarjeta estándar v4.13 de saldo/crédito (CardBuilder). */
    private function cardSaldo(string $tabla, array $cliente, ?array $cartera): string
    {
        $cb = CardBuilder::iniciar('💰', 'SALDO Y CRÉDITO DE CLIENTE')
            ->campo('IDENTIFICADOR', (string) ($cliente['identificador'] ?? ''))
            ->campo('NOMBRE', (string) ($cliente['nombre'] ?? ''))
            ->campo('RIF', (string) ($cliente['rif'] ?? ''))
            ->campo('LÍMITE CRÉDITO', $cliente['limite_credito'] ?? null, 'moneda')
            ->campo('SALDO ACTUAL', $cliente['saldo_actual'] ?? null, 'moneda');
        if (isset($cliente['descuento_unico']) && $cliente['descuento_unico'] !== null) {
            $cb->campo('DTO ÚNICO', (float) $cliente['descuento_unico'] . '%', 'texto');
        }
        if ($cartera !== null) {
            $cb->seccion('Cartera pendiente')
                ->campo('SALDO PENDIENTE', (float) $cartera['saldo_pendiente'], 'moneda')
                ->campo('SALDO PENDIENTE USD', (float) ($cartera['saldo_pendiente_usd'] ?? 0), 'moneda')
                ->campo('FACTURAS', (int) $cartera['facturas_pendientes'], 'numero')
                ->campo('TABLA', (string) $cartera['tabla_origen']);
            foreach (array_slice((array) ($cartera['facturas'] ?? []), 0, 5) as $fac) {
                $cb->linea('🧾 ' . ($fac['doc_num'] ?? '') . ' · '
                    . ($fac['fecha_factura'] !== '' ? $fac['fecha_factura'] : 's/fecha')
                    . ' · Bs ' . number_format((float) ($fac['saldo_restante_bs'] ?? 0), 2)
                    . ($fac['monto_usd'] !== null ? ' · $' . number_format((float) $fac['monto_usd'], 2) : '')
                    . ($fac['tasa_estimada'] ? ' · tasa est.' : ''));
            }
        }
        return $cb->footer('Fuente: ' . $tabla . ' (SQL Server, NOLOCK)')->tarjeta();
    }

    /**
     * Cartera de facturas pendientes del cliente (AD5 / v4.17).
     *
     * Cada factura incluye: doc_num, fecha_factura, monto_original_bs,
     * tasa_dia (saTasaHistorico, anterior más cercana si no hay exacta),
     * monto_usd (= monto_original_bs / tasa_dia), dias_vencidos y
     * saldo_restante_bs. Devuelve null si no hay tabla de facturas.
     *
     * @return array<string, mixed>|null
     */
    private function calcularCartera(ConnectionWrapper $wrapper, string $identificador): ?array
    {
        $tabla = $wrapper->resolverTabla(self::TABLAS_FACTURAS);
        if ($tabla === null) {
            return null;
        }
        $cols = $wrapper->resolverColumnas($tabla, self::CAMPOS_FACTURAS);
        if ($cols['cliente'] === null || $cols['saldo'] === null) {
            return null;
        }
        if ($cols['doc'] === null || $cols['fecha'] === null) {
            // Sin doc/fecha no se puede detallar la cartera AD5.
            return null;
        }

        $saldoPendiente = 0.0;
        $sumaUsd = 0.0;
        $facturas = [];
        try {
            $sql = 'SELECT TOP 50 ' . self::q($cols['doc']) . ' AS doc'
                 . ', ' . self::q($cols['fecha']) . ' AS fecha'
                 . ($cols['monto'] !== null ? ', ' . self::q($cols['monto']) . ' AS monto' : '')
                 . ', ' . self::q($cols['saldo']) . ' AS saldo'
                 . ' FROM ' . self::q($tabla) . ' WITH (NOLOCK)'
                 . ' WHERE ' . $this->coincidenciaNormalizada($cols['cliente']) . ' = ?'
                 . ' AND ISNULL(' . self::q($cols['saldo']) . ', 0) > 0'
                 . ' ORDER BY ' . self::q($cols['fecha']) . ' DESC';
            $filas = $wrapper->querySafe($sql, [$this->normalizar($identificador)]);
            foreach ($filas as $fila) {
                $docNum   = self::aUtf8((string) ($fila['doc'] ?? ''));
                $fechaFac = (string) ($fila['fecha'] ?? '');
                $saldoBs  = $fila['saldo'] !== null && $fila['saldo'] !== ''
                    ? (float) $fila['saldo'] : 0.0;
                $montoBs  = $cols['monto'] !== null && $fila['monto'] !== null && $fila['monto'] !== ''
                    ? (float) $fila['monto'] : $saldoBs;
                $tasa = $fechaFac !== '' ? $this->tasaDelDia($wrapper, $fechaFac) : null;

                $montoUsd = null;
                if ($tasa !== null && $tasa['tasa'] > 0 && $montoBs > 0) {
                    $montoUsd = round($montoBs / $tasa['tasa'], 2);
                    $sumaUsd += $montoUsd;
                }
                $saldoPendiente += $saldoBs;
                $facturas[] = [
                    'doc_num'          => $docNum,
                    'fecha_factura'    => $fechaFac,
                    'monto_original_bs'=> $montoBs,
                    'tasa_dia'         => $tasa !== null ? $tasa['tasa'] : null,
                    'monto_usd'        => $montoUsd,
                    'dias_vencidos'    => $this->diasVencidos($fechaFac),
                    'saldo_restante_bs'=> $saldoBs,
                    'tasa_estimada'    => $tasa !== null ? $tasa['estimada'] : false,
                ];
            }
        } catch (PDOException $e) {
            error_log('[ConsultarSaldoClienteTool] Cartera no calculable: ' . $e->getMessage());
            return null;
        } catch (\InvalidArgumentException $e) {
            error_log('[ConsultarSaldoClienteTool] Cartera no calculable: ' . $e->getMessage());
            return null;
        }

        return [
            'tabla_origen'         => $tabla,
            'saldo_pendiente'      => $saldoPendiente,
            'facturas_pendientes'  => count($facturas),
            'saldo_pendiente_usd'  => round($sumaUsd, 2),
            'facturas'             => $facturas,
        ];
    }

    /**
     * Tasa de cambio para una fecha (saTasaHistorico). Usa la tasa vigente en
     * esa fecha; si no existe exacta, la anterior más cercana (tasa_estimada).
     *
     * @return array{tasa: float|null, fecha: string, estimada: bool}|null
     */
    private function tasaDelDia(ConnectionWrapper $wrapper, string $fecha): ?array
    {
        $tabla = $wrapper->resolverTabla(['saTasaHistorico', 'tasa_historico', 'tasa_hist', 'saTasaCambio']);
        if ($tabla === null) {
            return null;
        }
        $cols = $wrapper->resolverColumnas($tabla, self::CAMPOS_TASA);
        if ($cols['tasa'] === null || $cols['fecha'] === null) {
            return null;
        }
        try {
            $sql = 'SELECT TOP 1 ' . self::q($cols['tasa']) . ' AS tasa'
                 . ', ' . self::q($cols['fecha']) . ' AS fecha'
                 . ' FROM ' . self::q($tabla) . ' WITH (NOLOCK)'
                 . ' WHERE ' . self::q($cols['fecha']) . ' <= ?'
                 . ' ORDER BY ' . self::q($cols['fecha']) . ' DESC';
            $filas = $wrapper->querySafe($sql, [$fecha]);
            $fila = $filas[0] ?? null;
            if (!is_array($fila)) {
                return null;
            }
            $tasa = $fila['tasa'] ?? null;
            $tasa = $tasa !== null && $tasa !== '' ? (float) $tasa : null;
            $fechaTasa = (string) ($fila['fecha'] ?? '');
            $estimada = $fechaTasa !== '' && $fechaTasa !== $fecha;
            return ['tasa' => $tasa, 'fecha' => $fechaTasa, 'estimada' => $estimada];
        } catch (PDOException $e) {
            error_log('[ConsultarSaldoClienteTool] Tasa no consultable: ' . $e->getMessage());
            return null;
        } catch (\InvalidArgumentException $e) {
            error_log('[ConsultarSaldoClienteTool] Tasa no consultable: ' . $e->getMessage());
            return null;
        }
    }

    /**
     * Días vencidos de una factura respecto a hoy (fecha del servidor SQL).
     * Se calcula en PHP con la fecha del servidor local (UTC) — formato
     * "YYYY-MM-DD" o "YYYY-MM-DD HH:MM:SS". Nunca negativo.
     */
    private function diasVencidos(string $fechaFactura): int
    {
        $f = preg_replace('/[T ]/', ' ', trim($fechaFactura));
        $f = (string) substr((string) $f, 0, 19);
        $ts = strtotime($f);
        if ($ts === false) {
            return 0;
        }
        $hoy = (int) floor(time() / 86400);
        $dia = (int) floor($ts / 86400);
        return max(0, $hoy - $dia);
    }

    /**
     * Expresión SQL que compara una columna ignorando separadores y mayúsculas.
     */
    private function coincidenciaNormalizada(string $columna): string
    {
        $col = self::q($columna);
        return "REPLACE(REPLACE(REPLACE(REPLACE($col,'-',''),'.',''),'/',''),' ','')";
    }

    /**
     * Normaliza un identificador: mayúsculas y sin caracteres no alfanuméricos.
     */
    private function normalizar(string $texto): string
    {
        $limpio = preg_replace('/[^A-Za-z0-9]/', '', strtoupper($texto)) ?? '';
        return $limpio;
    }

    /**
     * Envuelve un identificador validado en corchetes de SQL Server.
     */
    private static function q(string $identificador): string
    {
        if (preg_match('/^[A-Za-z_][A-Za-z0-9_]*$/', $identificador) !== 1) {
            throw new \RuntimeException('Identificador SQL inválido: ' . $identificador);
        }
        return '[' . $identificador . ']';
    }

    /**
     * Normaliza texto del ERP a UTF-8 (los datos vienen en CP1252/Latin-1):
     * recorta espacios de columnas char y convierte si no es UTF-8 válido.
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
}
