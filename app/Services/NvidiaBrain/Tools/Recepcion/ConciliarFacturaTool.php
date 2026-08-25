<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Recepcion;

use App\Services\ConnectionWrapper;
use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;
use PDO;
use PDOException;

require_once __DIR__ . '/../Common/CardBuilder.php';

/**
 * Concilia bancariamente una factura del ERP (SQL Server).
 *
 * Actualiza el estado de una factura en la tabla FacturasDrogueria a
 * CONCILIADO (si el monto conciliado coincide con el total de la factura) o
 * CONCILIADO_PARCIAL (si difiere), y persiste la referencia bancaria. La
 * operación se ejecuta dentro de una transacción PDO (beginTransaction/
 * commit/rollBack): cualquier fallo revierte por completo la operación.
 *
 * Esta herramienta SOLO escribe sobre la tabla FacturasDrogueria (el esquema
 * del ERP Droguería); no hace fallback a tablas de Profit para evitar alterar
 * los estados contables del ERP activo.
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
 *   $registry->registerTool(new ConciliarFacturaTool());
 */
final class ConciliarFacturaTool implements AgentToolInterface
{
    /**
     * Tablas candidatas de facturas en PRUEB25 (primer candidato existente
     * gana). 'FacturasDrogueria' no existe en el ERP real — se conserva como
     * candidato legacy; 'factura' es la tabla verificada en vivo (v4.18).
     */
    private const TABLAS = ['FacturasDrogueria', 'factura'];

    /**
     * Sinónimos de columna por campo lógico (primer candidato existente gana).
     * 'monto'/'referencia' verificados en vivo contra el esquema real de
     * `factura` (v4.18): 'tot_neto' es el total neto real de Profit;
     * 'num_control' es el número de control FISCAL de la factura (campo de
     * negocio real) — se sacó de los candidatos de 'referencia' porque
     * conciliar sobrescribiría ese dato del ERP. 'campo1' es un campo
     * genérico sin uso fiscal (mismo patrón que 'campo7' en `art` para
     * ubicación), es el único candidato seguro para guardar la referencia
     * bancaria.
     */
    private const CAMPOS = [
        'numero'    => ['fact_num', 'NumeroFactura', 'numero_factura', 'id', 'numero'],
        'monto'     => ['tot_neto', 'total_neto', 'monto', 'total', 'importe', 'monto_total'],
        'estado'    => ['estado', 'estatus', 'status', 'estado_conciliacion'],
        'referencia'=> ['referencia', 'referencia_bancaria', 'ref_bancaria', 'campo1'],
        'fecha'     => ['fecha_conciliacion', 'fec_conciliacion', 'conciliado_el', 'conciliado_fecha', 'fecha_conciliado'],
    ];

    private const ESTADO_TOTAL   = 'CONCILIADO';
    private const ESTADO_PARCIAL = 'CONCILIADO_PARCIAL';

    /** Tolerancia en Bs. para considerar el monto igual al total de la factura. */
    private const TOLERANCIA = 0.01;

    public function getName(): string
    {
        return 'conciliar_factura';
    }

    public function getDescription(): string
    {
        return 'Registra la conciliación bancaria de una factura del ERP: actualiza '
             . 'el estado de la factura a CONCILIADO si el monto coincide con su '
             . 'total, o CONCILIADO_PARCIAL si difiere, guardando la referencia '
             . 'bancaria. Úsala cuando el usuario confirme que un pago o voucher '
             . 'cuadra con una factura. Parámetros obligatorios: numero_factura, '
             . 'monto_conciliado y referencia_bancaria; notas (opcional).';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'numero_factura' => [
                    'type'        => 'string',
                    'description' => 'Número de la factura a conciliar.',
                ],
                'monto_conciliado' => [
                    'type'        => 'number',
                    'description' => 'Monto del pago/voucher en Bs. a conciliar.',
                ],
                'referencia_bancaria' => [
                    'type'        => 'string',
                    'description' => 'Referencia bancaria del pago (número de referencia o comprobante).',
                ],
                'notas' => [
                    'type'        => 'string',
                    'description' => 'Notas opcionales de la conciliación.',
                ],
            ],
            'required' => ['numero_factura', 'monto_conciliado', 'referencia_bancaria'],
        ];
    }

    /**
     * {@inheritDoc}
     */
    public function execute(array $arguments, array $contexto): array
    {
        $numero = trim((string) ($arguments['numero_factura'] ?? ''));
        $monto  = $arguments['monto_conciliado'] ?? null;
        $referencia = trim((string) ($arguments['referencia_bancaria'] ?? ''));
        $notas  = trim((string) ($arguments['notas'] ?? ''));

        // ── 1) Validación de argumentos ───────────────────────────────────────
        if ($numero === '') {
            return ['success' => false, 'error' => 'Parámetro "numero_factura" es obligatorio.'];
        }
        if (!is_numeric($monto) || (float) $monto <= 0) {
            return ['success' => false, 'error' => 'Parámetro "monto_conciliado" debe ser un número mayor que 0.'];
        }
        if ($referencia === '') {
            return ['success' => false, 'error' => 'Parámetro "referencia_bancaria" es obligatorio.'];
        }
        $monto = (float) $monto;

        // ── 2) Esquema vía conexión protegida (ConnectionWrapper v4.14). Las
        //       LECTURAS pasan por el wrapper; el UPDATE transaccional del paso
        //       3 usa su propia conexión (las escrituras no van por el wrapper).
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
                    'Ninguna de las tablas candidatas (%s) existe en la base configurada (PROFIT_SQL_NAME). Verifique que apunte a la base del ERP.',
                    implode(', ', self::TABLAS)
                ),
            ];
        }

        $cols = $wrapper->resolverColumnas($tabla, self::CAMPOS);
        $numCol    = $cols['numero'];
        $montoCol  = $cols['monto'];
        $estadoCol = $cols['estado'];
        $refCol    = $cols['referencia'];
        if ($numCol === null || $montoCol === null || $estadoCol === null || $refCol === null) {
            return [
                'success' => false,
                'error'   => sprintf(
                    'La tabla %s no tiene todas las columnas necesarias (número, monto, estado, referencia). Encontradas: %s.',
                    $tabla,
                    implode(', ', array_filter(array_map(
                        static fn (?string $c): string => $c ?? '?',
                        $cols
                    )))
                ),
            ];
        }

        // ── 3) Transacción PDO (todo o nada) — conexión propia de escritura.
        //       finally cierra siempre la conexión (candado anti-zombi). ──────
        $pdo = $this->conectar();
        try {
            $pdo->beginTransaction();
            try {
                $stmt = $pdo->prepare(
                    'SELECT ' . self::q($numCol) . ' AS numero, '
                    . self::q($montoCol) . ' AS monto, '
                    . self::q($estadoCol) . ' AS estado'
                    . ' FROM ' . self::q($tabla)
                    . ' WHERE ' . self::q($numCol) . ' = ?'
                );
                $stmt->execute([$numero]);
                $fila = $stmt->fetch(PDO::FETCH_ASSOC);

                if (!is_array($fila)) {
                    $pdo->rollBack();
                    return [
                        'success' => false,
                        'error'   => sprintf('Factura "%s" no encontrada en %s. No se realizó ningún cambio.', $numero, $tabla),
                    ];
                }

                $montoFactura = (float) ($fila['monto'] ?? 0);
                $estadoAnterior = $fila['estado'] ?? null;
                if (is_string($estadoAnterior)) {
                    $estadoAnterior = self::aUtf8($estadoAnterior);
                }
                $estadoNuevo = (abs($montoFactura - $monto) <= self::TOLERANCIA)
                    ? self::ESTADO_TOTAL
                    : self::ESTADO_PARCIAL;

                $sql = 'UPDATE ' . self::q($tabla)
                     . ' SET ' . self::q($estadoCol) . ' = ?, '
                     . self::q($refCol) . ' = ?'
                     . ($cols['fecha'] !== null ? ', ' . self::q($cols['fecha']) . ' = GETDATE()' : '')
                     . ' WHERE ' . self::q($numCol) . ' = ?';
                $pdo->prepare($sql)->execute([$estadoNuevo, $referencia, $numero]);

                $pdo->commit();
            } catch (PDOException $e) {
                if ($pdo->inTransaction()) {
                    $pdo->rollBack();
                }
                return [
                    'success' => false,
                    'error'   => 'Error al conciliar la factura (operación revertida): ' . self::aUtf8($e->getMessage()),
                ];
            }
        } finally {
            $pdo = null;
            gc_collect_cycles();
        }

        return [
            'success' => true,
            'data'    => [
                'factura' => [
                    'numero'          => $numero,
                    'monto'           => $montoFactura,
                    'estado_anterior' => $estadoAnterior,
                ],
                'conciliacion' => [
                    'monto_conciliado'   => $monto,
                    'referencia_bancaria'=> $referencia,
                    'estado_nuevo'       => $estadoNuevo,
                    'fecha_conciliacion' => date('Y-m-d H:i:s'),
                    'notas'              => $notas !== '' ? $notas : null,
                ],
                'mensaje' => sprintf(
                    'Factura %s marcada como %s (%s).',
                    $numero,
                    $estadoNuevo,
                    $estadoNuevo === self::ESTADO_TOTAL
                        ? 'el monto coincide con el total de la factura'
                        : 'el monto difiere del total de la factura'
                ),
            ],
            'card'    => CardBuilder::iniciar(
                $estadoNuevo === self::ESTADO_TOTAL ? '✅' : '⚠️',
                'CONCILIACIÓN DE FACTURA'
            )
                ->campo('FACTURA', $numero)
                ->campo('MONTO FACTURA', $montoFactura, 'moneda')
                ->campo('MONTO CONCILIADO', $monto, 'moneda')
                ->campo('ESTADO ANTERIOR', (string) ($estadoAnterior ?? 'N/D'))
                ->campo('ESTADO NUEVO', $estadoNuevo)
                ->campo('REFERENCIA', $referencia)
                ->campo('FECHA', date('Y-m-d H:i:s'), 'fecha')
                ->footer('Fuente: ' . $tabla . ' (ERP Droguería)')
                ->tarjeta(),
        ];
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
     * Abre la conexión PDO a SQL Server (sqlsrv si está disponible, si no ODBC).
     */
    private function conectar(): PDO
    {
        $host = self::env('PROFIT_SQL_HOST', self::env('PROFIT_DB_HOST', '192.168.4.20'));
        $port = self::env('PROFIT_SQL_PORT', self::env('PROFIT_DB_PORT', '1433'));
        $user = self::env('PROFIT_SQL_USER', self::env('PROFIT_DB_USER', 'profit'));
        $pass = self::env('PROFIT_SQL_PASS', self::env('PROFIT_DB_PASS', 'profit'));
        $name = self::env('PROFIT_SQL_NAME', self::env('PROFIT_DB_NAME', 'PRUEB25'));

        $drivers = PDO::getAvailableDrivers();
        if (in_array('sqlsrv', $drivers, true)) {
            // ConnectionPooling=0: mismo hotfix v4.17.1 que ConnectionWrapper —
            // sin esto el driver puede dejar un SPID dormido en el servidor
            // aunque PHP ya haya destruido el objeto PDO.
            $dsn = 'sqlsrv:Server=' . $host . ',' . $port . ';Database=' . $name . ';ConnectionPooling=0';
        } else {
            $driver = self::env('PROFIT_SQL_DRIVER', self::env('PROFIT_DB_DRIVER', 'SQL Server'));
            $dsn = 'odbc:Driver={' . $driver . '};Server=' . $host . ',' . $port . ';Database=' . $name;
        }

        $opts = [
            PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_TIMEOUT            => 8,
            PDO::ATTR_PERSISTENT         => false,
        ];
        if (defined('PDO::SQLSRV_ATTR_QUERY_TIMEOUT')) {
            $opts[PDO::SQLSRV_ATTR_QUERY_TIMEOUT] = 8;
        }
        return new PDO($dsn, $user, $pass, $opts);
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

    /**
     * Lee una variable de entorno con fallback y tolera valores vacíos.
     */
    private static function env(string $clave, string $default): string
    {
        $valor = getenv($clave);
        if (is_string($valor) && trim($valor) !== '') {
            return trim($valor);
        }
        return $default;
    }
}
