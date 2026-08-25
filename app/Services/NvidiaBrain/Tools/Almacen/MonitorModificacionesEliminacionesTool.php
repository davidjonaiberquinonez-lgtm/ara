<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Almacen;

use App\Services\ConnectionWrapper;
use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;
use PDO;
use PDOException;

/**
 * Auditoría de cambios en tiempo real sobre el flujo de notas: modificaciones
 * de monto/estado, anulaciones de renglones y eliminaciones.
 *
 * Estrategia de detección (por capas, en orden de fiabilidad):
 *  1. TABLA DE TRAZA: si el MySQL legacy expone una tabla de auditoría
 *     (candidatas: auditoria_notas, log_modificaciones, historial_notas,
 *     traza_notas, log_cambios, auditoria, log_auditoria...), lee los eventos
 *     reales {num_nota, usuario, tipo_evento, timestamp, antes/despues}.
 *  2. INDICIOS INDIRECTOS (si no hay traza, con aviso honesto):
 *     - rep_not con estatus ≠ PENDIENTE y cierre reciente (fec_impr): la nota
 *       fue modificada/cerrada (MODIFICADA).
 *     - reng_nde con anulado = 1: renglones eliminados de la nota
 *       (ELIMINADA parcial → MODIFICADA con detalle antes/despues).
 *  3. La eliminación TOTAL (nota borrada sin traza) es indetectable sin tabla
 *     de auditoría: se reporta explícitamente como limitación.
 *
 * Nunca escribe; nunca lanza excepción por datos ausentes.
 */
final class MonitorModificacionesEliminacionesTool implements AgentToolInterface
{
    use AlmacenDbTrait;

    /** Tablas candidatas de auditoría/traza en el MySQL legacy. */
    private const TABLAS_TRAZA = [
        'auditoria_notas', 'log_modificaciones', 'historial_notas', 'traza_notas',
        'log_cambios', 'auditoria', 'log_auditoria', 'registro_cambios',
    ];

    public function getName(): string
    {
        return 'monitor_modificaciones_eliminaciones';
    }

    public function getDescription(): string
    {
        return 'Captura eventos de modificación o anulación/eliminación de '
             . 'notas en el sistema: cambio de estado/monto, renglones '
             . 'eliminados o nota anulada, con usuario modificador, timestamp '
             . 'y detalle antes vs después. Lee la tabla de traza si existe; '
             . 'si no, detecta indicios indirectos (estatus alterado y '
             . 'renglones anulados) con aviso de limitación. Parámetros: '
             . 'num_nota (opcional, filtra por nota) y desde (ISO opcional).';
    }

    public function getParameters(): array
    {
        return [
            'type'       => 'object',
            'properties' => [
                'num_nota' => [
                    'type'        => 'string',
                    'description' => 'Número de la nota a monitorear. Si se omite, se reportan todos los eventos recientes.',
                ],
                'desde' => [
                    'type'        => 'string',
                    'description' => 'Timestamp ISO (ej. "2026-08-07 00:00:00") desde el que se buscan cambios.',
                ],
                'limite' => [
                    'type'        => 'integer',
                    'description' => 'Máximo de eventos a devolver (default 50).',
                ],
            ],
            'required' => [],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $t0 = self::micro();
        $numNota = trim((string) ($arguments['num_nota'] ?? ''));
        $desde = trim((string) ($arguments['desde'] ?? ''));
        $limite = max(1, min(200, (int) ($arguments['limite'] ?? 50)));
        $advertencias = [];

        try {
            $pdo = $this->conectarMySQL();
        } catch (PDOException $e) {
            return ['success' => false, 'error' => 'MySQL legacy no disponible: ' . self::aUtf8($e->getMessage())];
        }

        // ── Capa 0 (AD4 / v4.17): logs locales app_log_notas y
        //    app_log_eliminaciones — fuente primaria del monitor. ────────────
        $fuente = 'app_logs';
        $app = $this->leerAppLogs($pdo, $numNota, $desde, $limite, $advertencias);
        $modificaciones = $app['modificaciones'];
        $eliminaciones  = $app['eliminaciones'];

        if ($modificaciones === [] && $eliminaciones === []) {
            // ── Capa 1: tabla de traza real (fallback) ─────────────────────
            $fuente = 'indicios_indirectos';
            $eventos = [];
            $tablaTraza = $this->resolverTabla($pdo, self::TABLAS_TRAZA);
            if ($tablaTraza !== null) {
                $cols = $this->resolverColumnas($pdo, $tablaTraza, [
                    'cod'    => ['num_nota', 'cod_nota', 'cd_barr', 'nota'],
                    'usuario'=> ['usuario', 'usuario_modificador', 'user', 'operador', 'responsable'],
                    'tipo'   => ['tipo_evento', 'tipo', 'accion', 'evento'],
                    'fecha'  => ['fecha', 'timestamp', 'fec', 'created_at'],
                    'antes'  => ['antes', 'detalle_antes', 'estado_anterior', 'before'],
                    'despues'=> ['despues', 'detalle_despues', 'estado_nuevo', 'after'],
                ]);
                if ($cols['cod'] !== null) {
                    $condiciones = [];
                    $params = [];
                    if ($numNota !== '') {
                        $condiciones[] = self::q($cols['cod']) . ' = ?';
                        $params[] = $numNota;
                    }
                    if ($desde !== '' && $cols['fecha'] !== null) {
                        $condiciones[] = self::q($cols['fecha']) . ' >= ?';
                        $params[] = $desde;
                    }
                    $sql = 'SELECT ' . self::q($cols['cod']) . ' AS cod'
                         . ($cols['usuario'] !== null ? ', ' . self::q($cols['usuario']) . ' AS usuario' : '')
                         . ($cols['tipo'] !== null ? ', ' . self::q($cols['tipo']) . ' AS tipo' : '')
                         . ($cols['fecha'] !== null ? ', ' . self::q($cols['fecha']) . ' AS fecha' : '')
                         . ($cols['antes'] !== null ? ', ' . self::q($cols['antes']) . ' AS antes' : '')
                         . ($cols['despues'] !== null ? ', ' . self::q($cols['despues']) . ' AS despues' : '')
                         . ' FROM ' . self::q($tablaTraza);
                    if ($condiciones !== []) {
                        $sql .= ' WHERE ' . implode(' AND ', $condiciones);
                    }
                    $sql .= ' ORDER BY ' . self::q($cols['fecha'] ?? $cols['cod']) . ' DESC LIMIT ' . $limite;
                    try {
                        $stmt = $pdo->prepare($sql);
                        $stmt->execute($params);
                        foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $fila) {
                            $eventos[] = [
                                'num_nota'          => self::aUtf8((string) ($fila['cod'] ?? '')),
                                'tipo_evento'       => self::normalizarTipo((string) ($fila['tipo'] ?? 'MODIFICADA')),
                                'timestamp'         => (string) ($fila['fecha'] ?? ''),
                                'usuario_modificador' => self::aUtf8((string) ($fila['usuario'] ?? '')),
                                'antes'             => self::aUtf8((string) ($fila['antes'] ?? '')),
                                'despues'           => self::aUtf8((string) ($fila['despues'] ?? '')),
                            ];
                        }
                        if ($eventos !== []) {
                            $fuente = 'tabla_traza';
                        }
                    } catch (PDOException $e) {
                        $advertencias[] = 'Tabla de traza no consultable: ' . self::aUtf8($e->getMessage());
                    }
                }
            }

            // ── Capa 2: indicios indirectos (si la traza no dio eventos) ────
            if ($eventos === []) {
                $advertencias[] = 'No hay tabla de traza con eventos en el esquema actual; '
                                . 'se usan indicios indirectos (cambio de estatus y renglones anulados). '
                                . 'La eliminación total sin traza no es detectable.';
                $eventos = $this->indiciosIndirectos($pdo, $numNota, $desde, $limite, $advertencias);
            }

            // Mapeo del esquema genérico {tipo_evento, ...} a AD4.
            foreach ($eventos as $e) {
                if (($e['tipo_evento'] ?? '') === 'ELIMINADA') {
                    $eliminaciones[] = [
                        'doc_num'           => self::aUtf8((string) ($e['num_nota'] ?? '')),
                        'fecha_eliminacion' => (string) ($e['timestamp'] ?? ''),
                        'usuario_elimino'   => self::aUtf8((string) ($e['usuario_modificador'] ?? '')),
                        'motivo'            => '',
                        'monto_total'       => null,
                    ];
                } else {
                    $modificaciones[] = [
                        'doc_num'            => self::aUtf8((string) ($e['num_nota'] ?? '')),
                        'fecha_original'     => '',
                        'fecha_modificacion' => (string) ($e['timestamp'] ?? ''),
                        'usuario_modifico'   => self::aUtf8((string) ($e['usuario_modificador'] ?? '')),
                        'campos_cambiados'   => [],
                        'valores_anteriores' => [],
                        'valores_nuevos'     => [],
                        'motivo'             => ((string) ($e['antes'] ?? '') !== '' ? (string) ($e['antes'] ?? '') . ' → ' : '')
                                             . (string) ($e['despues'] ?? ''),
                    ];
                }
            }
            $modificaciones = array_slice($modificaciones, 0, $limite);
            $eliminaciones  = array_slice($eliminaciones, 0, $limite);
        }
        $pdo = null;

        // Vista de compatibilidad (no rompe el contrato previo v4.13):
        // eventos = modificaciones + eliminaciones aplanadas.
        $eventos = [];
        foreach ($modificaciones as $m) {
            $eventos[] = [
                'num_nota'            => $m['doc_num'],
                'tipo_evento'         => 'MODIFICADA',
                'timestamp'           => $m['fecha_modificacion'],
                'usuario_modificador' => $m['usuario_modifico'],
                'antes'               => $m['valores_anteriores'],
                'despues'             => $m['valores_nuevos'],
                'motivo'              => $m['motivo'],
                'fuente'              => $fuente,
            ];
        }
        foreach ($eliminaciones as $el) {
            $eventos[] = [
                'num_nota'            => $el['doc_num'],
                'tipo_evento'         => 'ELIMINADA',
                'timestamp'           => $el['fecha_eliminacion'],
                'usuario_modificador' => $el['usuario_elimino'],
                'antes'               => '',
                'despues'             => '',
                'motivo'              => $el['motivo'],
                'fuente'              => $fuente,
            ];
        }

        $card = $this->tarjetaEventos($fuente, $modificaciones, $eliminaciones, $numNota, $advertencias, (int) ((self::micro() - $t0) * 1_000_000));

        return [
            'success' => true,
            'data'    => [
                'fuente'         => $fuente,
                'modificaciones' => $modificaciones,
                'eliminaciones'  => $eliminaciones,
                'eventos'        => $eventos,
                'total'          => count($modificaciones) + count($eliminaciones),
                'advertencias'   => $advertencias,
                'metric_us'      => (int) ((self::micro() - $t0) * 1_000_000),
            ],
            'card'    => $card,
        ];
    }

    /**
     * Lee los logs locales app_log_notas (acciones MODIFICADA/CREADA) y
     * app_log_eliminaciones (AD4). Best-effort: crea las tablas locales si no
     * existen y degrada con advertencia.
     *
     * @param array<int,string> $advertencias (por referencia)
     *
     * @return array{modificaciones: array<int,array<string,mixed>>, eliminaciones: array<int,array<string,mixed>>}
     */
    private function leerAppLogs(PDO $pdo, string $numNota, string $desde, int $limite, array &$advertencias): array
    {
        $salida = ['modificaciones' => [], 'eliminaciones' => []];
        try {
            $pdo->exec(
                'CREATE TABLE IF NOT EXISTS `app_log_notas` ('
                . '`id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, `nota_id` VARCHAR(40) NOT NULL,'
                . '`accion` VARCHAR(30) NOT NULL, `usuario` VARCHAR(80) NOT NULL DEFAULT "",'
                . '`fecha` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, `detalle` VARCHAR(500) NULL,'
                . 'KEY `idx_app_log_notas_nota` (`nota_id`), KEY `idx_app_log_notas_fecha` (`fecha`))'
                . ' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'
            );
            $pdo->exec(
                'CREATE TABLE IF NOT EXISTS `app_log_eliminaciones` ('
                . '`id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, `nota_id` VARCHAR(40) NOT NULL,'
                . '`doc_num` VARCHAR(40) NOT NULL DEFAULT "", `usuario` VARCHAR(80) NOT NULL DEFAULT "",'
                . '`fecha_eliminacion` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, `motivo` VARCHAR(255) NULL,'
                . '`monto_total` DECIMAL(18,2) NOT NULL DEFAULT 0,'
                . 'KEY `idx_app_log_elim_fecha` (`fecha_eliminacion`), KEY `idx_app_log_elim_nota` (`nota_id`))'
                . ' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'
            );
        } catch (PDOException $e) {
            $advertencias[] = 'No se pudieron garantizar las tablas locales app_log_*: ' . self::aUtf8($e->getMessage());
            return $salida;
        }

        // a) Modificaciones/Creaciones: app_log_notas.
        $cond = ["UPPER(`accion`) IN ('MODIFICADA','CREADA','UPDATE','INSERT','MODIFICAR')"];
        $params = [];
        if ($numNota !== '') {
            $cond[] = '`nota_id` = ?';
            $params[] = $numNota;
        }
        if ($desde !== '') {
            $cond[] = '`fecha` >= ?';
            $params[] = $desde;
        }
        try {
            $stmt = $pdo->prepare(
                'SELECT `nota_id`, `accion`, `usuario`, `fecha`, `detalle` FROM `app_log_notas`'
                . ' WHERE ' . implode(' AND ', $cond) . ' ORDER BY `fecha` DESC LIMIT ' . $limite
            );
            $stmt->execute($params);
            foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $fila) {
                $salida['modificaciones'][] = [
                    'doc_num'            => self::aUtf8((string) ($fila['nota_id'] ?? '')),
                    'fecha_original'     => '',
                    'fecha_modificacion' => (string) ($fila['fecha'] ?? ''),
                    'usuario_modifico'   => self::aUtf8((string) ($fila['usuario'] ?? '')),
                    'campos_cambiados'   => self::camposDelDetalle((string) ($fila['detalle'] ?? '')),
                    'valores_anteriores' => [],
                    'valores_nuevos'     => [],
                    'motivo'             => (string) ($fila['detalle'] ?? ''),
                ];
            }
        } catch (PDOException $e) {
            $advertencias[] = 'app_log_notas no consultable: ' . self::aUtf8($e->getMessage());
        }

        // b) Eliminaciones: app_log_eliminaciones.
        $cond = ['1 = 1'];
        $params = [];
        if ($numNota !== '') {
            $cond[] = '(`doc_num` = ? OR `nota_id` = ?)';
            $params[] = $numNota;
            $params[] = $numNota;
        }
        if ($desde !== '') {
            $cond[] = '`fecha_eliminacion` >= ?';
            $params[] = $desde;
        }
        try {
            $stmt = $pdo->prepare(
                'SELECT `doc_num`, `nota_id`, `usuario`, `fecha_eliminacion`, `motivo`, `monto_total`'
                . ' FROM `app_log_eliminaciones` WHERE ' . implode(' AND ', $cond)
                . ' ORDER BY `fecha_eliminacion` DESC LIMIT ' . $limite
            );
            $stmt->execute($params);
            foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $fila) {
                $salida['eliminaciones'][] = [
                    'doc_num'           => self::aUtf8((string) ($fila['doc_num'] !== '' ? $fila['doc_num'] : ($fila['nota_id'] ?? ''))),
                    'fecha_eliminacion' => (string) ($fila['fecha_eliminacion'] ?? ''),
                    'usuario_elimino'   => self::aUtf8((string) ($fila['usuario'] ?? '')),
                    'motivo'            => self::aUtf8((string) ($fila['motivo'] ?? '')),
                    'monto_total'       => $fila['monto_total'] !== null && $fila['monto_total'] !== ''
                        ? (float) $fila['monto_total'] : null,
                ];
            }
        } catch (PDOException $e) {
            $advertencias[] = 'app_log_eliminaciones no consultable: ' . self::aUtf8($e->getMessage());
        }
        return $salida;
    }

    /**
     * Campos alterados a partir del detalle de app_log_notas (formato
     * "campo1:valor; campo2:valor" o texto libre).
     *
     * @return array<int,string>
     */
    private static function camposDelDetalle(string $detalle): array
    {
        $campos = [];
        foreach (preg_split('/[;,]/', $detalle) ?: [] as $parte) {
            $parte = trim($parte);
            if ($parte === '') {
                continue;
            }
            $clave = trim((string) preg_split('/[:=]/', $parte, 2)[0]);
            if ($clave !== '') {
                $campos[] = self::aUtf8($clave);
            }
        }
        return array_values(array_unique($campos));
    }

    /** Tarjeta estándar v4.13 de eventos de modificación (CardBuilder). */
    private function tarjetaEventos(string $fuente, array $modificaciones, array $eliminaciones, string $numNota, array $advertencias, int $us): string
    {
        $titulo = 'MONITOR DE MODIFICACIONES' . ($numNota !== '' ? ': ' . $numNota : '');
        $card = CardBuilder::iniciar('🔍', $titulo)
            ->campo('MODIFICADAS', count($modificaciones), 'numero')
            ->campo('ELIMINADAS', count($eliminaciones), 'numero');
        if ($modificaciones === [] && $eliminaciones === []) {
            $card->linea('Sin eventos de modificación/eliminación detectados.');
        } else {
            $card->seccion('Eventos (fuente: ' . $fuente . ')');
            foreach (array_slice($modificaciones, 0, 8) as $m) {
                $card->linea('✏️ ' . $m['doc_num'] . ' MODIFICADA @ ' . ($m['fecha_modificacion'] !== '' ? $m['fecha_modificacion'] : 's/timestamp')
                    . ($m['usuario_modifico'] !== '' ? ' · ' . $m['usuario_modifico'] : ''));
                if (($m['motivo'] ?? '') !== '') {
                    $card->linea('    ' . $m['motivo']);
                }
            }
            foreach (array_slice($eliminaciones, 0, 4) as $el) {
                $card->linea('🗑️ ' . $el['doc_num'] . ' ELIMINADA @ ' . ($el['fecha_eliminacion'] !== '' ? $el['fecha_eliminacion'] : 's/timestamp')
                    . ($el['usuario_elimino'] !== '' ? ' · ' . $el['usuario_elimino'] : ''));
                if (($el['motivo'] ?? '') !== '') {
                    $card->linea('    motivo: ' . $el['motivo']);
                }
            }
        }
        foreach (array_slice($advertencias, 0, 2) as $aviso) {
            $card->linea('⚠️ ' . $aviso);
        }
        return $card
            ->footer('Fuente: MySQL legacy + Profit PRUEB25 (NOLOCK) · ' . round($us / 1000) . ' ms')
            ->tarjeta();
    }

    /**
     * Indicios indirectos de modificación/eliminación:
     *  - rep_not con estatus ≠ PENDIENTE y cierre (fec_impr) → MODIFICADA.
     *  - reng_nde con anulado=1 → ELIMINADA (renglones borrados), detalle
     *    antes/despues con conteo.
     *
     * @param array<int,string> $advertencias (por referencia)
     *
     * @return array<int,array<string,mixed>>
     */
    private function indiciosIndirectos(PDO $pdo, string $numNota, string $desde, int $limite, array &$advertencias): array
    {
        $eventos = [];

        // a) Cabeceras alteradas (rep_not).
        $tabla = $this->resolverTabla($pdo, ['rep_not', 'notas', 'nota']);
        if ($tabla !== null) {
            $cols = $this->resolverColumnas($pdo, $tabla, [
                'cod'    => ['cod_nota', 'num_nota', 'cd_barr'],
                'estado' => ['estatus', 'estado', 'status'],
                'fec_imp'=> ['fec_impr', 'fec_profit'],
                'verif'  => ['verificacion'],
            ]);
            if ($cols['cod'] !== null && $cols['estado'] !== null) {
                $condiciones = ["UPPER(" . self::q($cols['estado']) . ") <> 'PENDIENTE'"];
                $params = [];
                if ($numNota !== '') {
                    $condiciones[] = self::q($cols['cod']) . ' = ?';
                    $params[] = $numNota;
                }
                if ($desde !== '' && $cols['fec_imp'] !== null) {
                    $condiciones[] = self::q($cols['fec_imp']) . ' >= ?';
                    $params[] = $desde;
                }
                $sql = 'SELECT ' . self::q($cols['cod']) . ' AS cod'
                     . ', ' . self::q($cols['estado']) . ' AS estado'
                     . ($cols['fec_imp'] !== null ? ', ' . self::q($cols['fec_imp']) . ' AS fec_imp' : '')
                     . ($cols['verif'] !== null ? ', ' . self::q($cols['verif']) . ' AS verif' : '')
                     . ' FROM ' . self::q($tabla) . ' WHERE ' . implode(' AND ', $condiciones)
                     . ' ORDER BY ' . self::q($cols['fec_imp'] ?? $cols['cod']) . ' DESC LIMIT ' . $limite;
                try {
                    $stmt = $pdo->prepare($sql);
                    $stmt->execute($params);
                    foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $fila) {
                        $eventos[] = [
                            'num_nota'          => self::aUtf8((string) ($fila['cod'] ?? '')),
                            'tipo_evento'       => 'MODIFICADA',
                            'timestamp'         => (string) ($fila['fec_imp'] ?? ''),
                            'usuario_modificador' => '',
                            'antes'             => 'estatus PENDIENTE',
                            'despues'           => 'estatus ' . self::aUtf8((string) ($fila['estado'] ?? '')),
                            'fuente'            => 'indicio_cabecera',
                        ];
                    }
                } catch (PDOException $e) {
                    $advertencias[] = 'Cabeceras no consultables: ' . self::aUtf8($e->getMessage());
                }
            }
        }

        // b) Renglones anulados (reng_nde, PRUEB25).
        $eventos = array_merge($eventos, $this->anulacionesProfit($numNota, $desde, $advertencias));

        return array_slice($eventos, 0, $limite);
    }

    /**
     * Renglones anulados (anulado=1) en reng_nde como indicio de eliminación
     * parcial (MODIFICADA con detalle antes/después por renglón).
     *
     * @param array<int,string> $advertencias (por referencia)
     *
     * @return array<int,array<string,mixed>>
     */
    private function anulacionesProfit(string $numNota, string $desde, array &$advertencias): array
    {
        $wrapper = new ConnectionWrapper();
        $tabla = $wrapper->resolverTabla(['reng_nde', 'reng_ndd']);
        if ($tabla === null) {
            try {
                $wrapper->queryProfit('SELECT 1');
            } catch (PDOException $e) {
                $advertencias[] = 'Profit PRUEB25 no disponible: ' . self::aUtf8($e->getMessage());
                return [];
            }
            return [];
        }
        $cols = $wrapper->resolverColumnas($tabla, [
            'num'  => ['num_doc', 'fact_num'],
            'reng' => ['reng_num', 'num_reng'],
            'art'  => ['co_art', 'cod_art'],
            'anul' => ['anulado'],
            'sol'  => ['total_art', 'cant_sol'],
        ]);
        if ($cols['num'] === null || $cols['anul'] === null) {
            return [];
        }
        // anulado en reng_nde es BIT: solo se compara contra '0' (comparar con
        // 'N'/'' dispara SQLSTATE 22018 — bug pre-existente del legado).
        $condiciones = [$wrapper->qPara($cols['anul']) . " NOT IN ('0')"];
        $params = [];
        if ($numNota !== '') {
            $condiciones[] = $wrapper->qPara($cols['num']) . ' = ?';
            $params[] = $numNota;
        }
        // Sin columna de fecha en reng_nde: el orden es por documento. TOP en
        // vez de LIMIT (SQL Server; LIMIT era un bug pre-existente del legado).
        $sql = 'SELECT TOP 200 ' . $wrapper->qPara($cols['num']) . ' AS num'
             . ($cols['reng'] !== null ? ', ' . $wrapper->qPara($cols['reng']) . ' AS reng' : '')
             . ', ' . $wrapper->qPara($cols['art']) . ' AS art'
             . ($cols['sol'] !== null ? ', ' . $wrapper->qPara($cols['sol']) . ' AS sol' : '')
             . ' FROM ' . $wrapper->qPara($tabla) . ' WITH (NOLOCK) WHERE ' . implode(' AND ', $condiciones)
             . ' ORDER BY ' . $wrapper->qPara($cols['num']) . ' DESC';
        try {
            $eventos = [];
            foreach ($wrapper->querySafe($sql, $params) as $fila) {
                $eventos[] = [
                    'num_nota'          => self::aUtf8((string) ($fila['num'] ?? '')),
                    'tipo_evento'       => 'ELIMINADA',
                    'timestamp'         => '',
                    'usuario_modificador' => '',
                    'antes'             => 'renglón ' . (string) ($fila['reng'] ?? '') . ' (SKU '
                        . self::aUtf8((string) ($fila['art'] ?? '')) . ', cant '
                        . (string) ($fila['sol'] ?? 0) . ')',
                    'despues'           => 'renglón anulado',
                    'fuente'            => 'indicio_renglon_anulado',
                ];
            }
            return $eventos;
        } catch (PDOException $e) {
            $advertencias[] = 'Renglones anulados no consultables: ' . self::aUtf8($e->getMessage());
            return [];
        }
    }

    /**
     * Normaliza el tipo de evento a la convención MODIFICADA/ELIMINADA.
     */
    private static function normalizarTipo(string $tipo): string
    {
        $t = strtoupper(trim($tipo));
        if (str_contains($t, 'ELIMIN') || str_contains($t, 'BORRAD') || str_contains($t, 'ANUL')
            || str_contains($t, 'DELETE')) {
            return 'ELIMINADA';
        }
        if (str_contains($t, 'CREAD') || str_contains($t, 'INSERT')) {
            return 'CREADA';
        }
        return 'MODIFICADA';
    }

    /** Definition consolidada (nombre + descripción + esquema) para el LLM. */
    public function getDefinition(): array
    {
        return [
            'name'        => $this->getName(),
            'description' => $this->getDescription(),
            'parameters'  => $this->getParameters(),
        ];
    }
}
