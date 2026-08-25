<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Almacen;

use App\Services\ConnectionWrapper;
use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;
use PDO;
use PDOException;

/**
 * Trazabilidad 360° y tiempos de vida de una nota de pedido.
 *
 * Reconstruye el historial cronológico completo desde la creación hasta la
 * entrega final, cruzando las dos fuentes reales:
 *   - MySQL legacy (BD funcional del flujo gestion.php): rep_not (cabecera:
 *     creación T0, estatus), asignaciones_preparacion (asignación a mesa T1),
 *     gestion (preparación T2 / chequeo T3 / embalaje T4 con usuario y
 *     estación), devoluciones (entrega o devolución T6).
 *   - SQL Server PRUEB25 (Profit): reng_nde (detalle por ítem: solicitada vs
 *     despachada vs pendiente) y reng_dev (cantidad devuelta por artículo).
 *
 * El timeline se emite con deltas (Δ segundos) entre hitos y nombres de
 * usuario reales (mapeo best-effort contra la tabla de usuarios si existe).
 * Nunca escribe; nunca lanza excepción por nota inexistente: degrada con
 * diagnóstico honesto de requisitos.
 *
 * Contrato de retorno (SIEMPRE array serializable):
 *   ['success' => true,  'data' => [...]]      → éxito
 *   ['success' => false, 'error' => 'mensaje'] → error controlado
 */
final class TrazabilidadVidaUtilNotaTool implements AgentToolInterface
{
    use AlmacenDbTrait;

    /** Umbrales de demora por estado (minutos) — AD6 / v4.17. */
    private const UMBRALES_MIN = ['Pendiente' => 60, 'Procesado' => 90, 'Modificado' => 30];

    public function getName(): string
    {
        return 'trazabilidad_vida_util_nota';
    }

    public function getDescription(): string
    {
        return 'Reconstruye el historial cronológico completo (timeline 360°) de '
             . 'una nota de pedido: creación, asignación a mesa, preparación, '
             . 'chequeo, embalaje, despacho y entrega/devolución, con deltas de '
             . 'tiempo entre hitos y usuarios responsables. Incluye el detalle '
             . 'por ítem (solicitada/despachada/pendiente/devuelta). Úsala para '
             . 'auditar el ciclo de vida de una nota. Parámetro: num_nota '
             . '(número o código de barras de la nota, obligatorio).';
    }

    public function getParameters(): array
    {
        return [
            'type'       => 'object',
            'properties' => [
                'num_nota' => [
                    'type'        => 'string',
                    'description' => 'Número o código de barras de la nota de pedido (ej. "72161167" o el cod_nota del legacy).',
                ],
            ],
            'required'   => ['num_nota'],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $t0 = self::micro();
        $numNota = trim((string) ($arguments['num_nota'] ?? ''));
        if ($numNota === '') {
            return ['success' => false, 'error' => 'El parámetro "num_nota" es obligatorio.'];
        }

        $advertencias = [];
        $timeline     = [];
        $deltas       = [];

        // ── Fase A: MySQL legacy (cabecera + trazabilidad física) ──────────
        $my = $this->cabeceraYTrazabilidadMySQL($numNota, $advertencias);
        foreach ($my['hitos'] as $hito) {
            $timeline[] = $hito;
        }
        $estadoActual = $my['estado'];

        // ── Fase B: SQL Server PRUEB25 (detalle por ítem) ─────────────────
        $items = $this->itemsDesdeProfit($numNota, $advertencias);

        // ── Fase C: deltas entre hitos con timestamps parseables ───────────
        $deltas = $this->calcularDeltas($timeline);

        // ── Fase D: ciclo de vida + alertas de demora (AD6 / v4.17) ────────
        $ciclo = $this->construirCicloVida($timeline);

        // Estado final: devolución registrada > en tránsito/entregada.
        $estadoFinal = 'EN_TRANSITO';
        if (($my['devolucion'] ?? null) !== null) {
            $estadoFinal = 'DEVUELTA';
        } elseif (in_array($estadoActual, ['PROCESADA', 'CHEQUEADA', 'COMPLETADA', 'ENTREGADA'], true)) {
            $estadoFinal = $estadoActual === 'DEVUELTA' ? 'DEVUELTA' : 'DESPACHADA_ENTREGADA';
        } elseif (($my['hitos'][3]['timestamp'] ?? null) !== null) {
            $estadoFinal = 'EN_TRANSITO';
        } elseif (($my['hitos'][2]['timestamp'] ?? null) !== null) {
            $estadoFinal = 'EN_EMBALAJE';
        } elseif (($my['hitos'][1]['timestamp'] ?? null) !== null) {
            $estadoFinal = 'EN_CHEQUEO';
        } elseif (($my['hitos'][0]['timestamp'] ?? null) !== null) {
            $estadoFinal = 'EN_PREPARACION';
        }

        $card = $this->tarjetaTrazabilidad($numNota, $estadoActual, $estadoFinal, $timeline, $deltas, $items, $ciclo, (int) ((self::micro() - $t0) * 1_000_000));

        return [
            'success' => true,
            'data'    => [
                'num_nota'               => $numNota,
                'estado_actual'          => $estadoActual,
                'estado_final'           => $estadoFinal,
                'timeline'               => $timeline,
                'deltas'                 => $deltas,
                'items'                  => $items,
                'devolucion'             => $my['devolucion'],
                'ciclo_vida'             => $ciclo['ciclo_vida'],
                'tiempo_total_ciclo_min' => $ciclo['tiempo_total_ciclo_min'],
                'alertas'                => $ciclo['alertas'],
                'advertencias'           => $advertencias,
                'metric_us'              => (int) ((self::micro() - $t0) * 1_000_000),
            ],
            'card'    => $card,
        ];
    }

    /**
     * Ciclo de vida de la nota (AD6 / v4.17): cada fase del timeline como
     * estado con fecha, usuario y tiempo en estado (min). Incluye el tiempo
     * total del ciclo y alertas de demora contra umbrales por estado.
     *
     * @return array{ciclo_vida:array<int,array<string,mixed>>,tiempo_total_ciclo_min:float,alertas:array<int,array<string,mixed>>}
     */
    private function construirCicloVida(array $timeline): array
    {
        $estados = [];
        foreach ($timeline as $hito) {
            $ts = self::fechaHito((string) ($hito['timestamp'] ?? ''));
            if ($ts === null) {
                continue;
            }
            $estados[] = [
                'fase'   => (string) ($hito['fase'] ?? ''),
                'hito'   => (string) ($hito['hito'] ?? ''),
                'estado' => self::estadoDeFase((string) ($hito['fase'] ?? '')),
                'fecha'  => date('c', $ts),
                'usuario'=> (string) ($hito['usuario'] ?? ''),
                'ts'     => $ts,
            ];
        }
        usort($estados, static fn (array $a, array $b): int => $a['ts'] <=> $b['ts']);

        $ahora = time();
        $n = count($estados);
        $ciclo = [];
        for ($i = 0; $i < $n; ++$i) {
            $fin = $i + 1 < $n ? $estados[$i + 1]['ts'] : $ahora;
            $min = max(0.0, ($fin - $estados[$i]['ts']) / 60);
            $ciclo[] = [
                'estado'              => $estados[$i]['estado'],
                'fase'                => $estados[$i]['fase'],
                'fecha'               => $estados[$i]['fecha'],
                'usuario'             => $estados[$i]['usuario'],
                'tiempo_en_estado_min'=> round($min, 1),
            ];
        }
        $totalMin = $n > 0 ? round(max(0.0, ($estados[$n - 1]['ts'] - $estados[0]['ts']) / 60), 1) : 0.0;

        $alertas = [];
        foreach ($ciclo as $c) {
            $umbral = self::UMBRALES_MIN[$c['estado']] ?? null;
            if ($umbral !== null && $c['tiempo_en_estado_min'] > (float) $umbral) {
                $alertas[] = [
                    'estado'              => $c['estado'],
                    'fase'                => $c['fase'],
                    'tiempo_en_estado_min'=> $c['tiempo_en_estado_min'],
                    'umbral_min'          => $umbral,
                    'tipo'                => 'DEMORA',
                    'mensaje'             => sprintf(
                        'Fase %s (%s) lleva %.0f min en estado %s; umbral %d min.',
                        $c['fase'],
                        date('d/m H:i', (int) strtotime($c['fecha'])),
                        $c['tiempo_en_estado_min'],
                        $c['estado'],
                        $umbral
                    ),
                ];
            }
        }
        return [
            'ciclo_vida'             => $ciclo,
            'tiempo_total_ciclo_min' => $totalMin,
            'alertas'                => $alertas,
        ];
    }

    /**
     * Mapea una fase del timeline al estado de negocio (Pendiente/Procesado)
     * usado por los umbrales de demora (AD6).
     */
    private static function estadoDeFase(string $fase): string
    {
        return in_array($fase, ['CREACION_NOTA', 'ASIGNACION_MESA'], true) ? 'Pendiente' : 'Procesado';
    }

    /** Tarjeta estándar v4.13 del timeline 360° (CardBuilder). */
    private function tarjetaTrazabilidad(string $numNota, string $estadoActual, string $estadoFinal, array $timeline, array $deltas, array $items, array $ciclo, int $us): string
    {
        $mapaFase = [
            'CREACION_NOTA' => '🆕', 'ASIGNACION_MESA' => '👷', 'PREPARACION' => '📋',
            'CHEQUEO' => '🔍', 'EMBALAJE' => '📦', 'CIERRE_IMPRESION' => '🖨️',
            'ENTREGA_DEVOLUCION' => '🚚',
        ];
        $card = CardBuilder::iniciar('🛰️', 'TRAZABILIDAD 360° NOTA ' . $numNota)
            ->campo('ESTADO ACTUAL', $estadoActual, 'estado')
            ->campo('ESTADO FINAL', $estadoFinal)
            ->campo('TIEMPO CICLO', round((float) ($ciclo['tiempo_total_ciclo_min'] ?? 0)) . ' min', 'texto');
        foreach (array_slice((array) ($ciclo['alertas'] ?? []), 0, 3) as $alerta) {
            $card->linea('🚨 ' . $alerta['mensaje']);
        }
        $card->seccion('Timeline');
        foreach (array_slice($timeline, 0, 8) as $hito) {
            $emoji = $mapaFase[(string) ($hito['fase'] ?? '')] ?? '•';
            $card->linea($emoji . ' ' . $hito['hito'] . ' ' . $hito['fase']
                . ($hito['timestamp'] !== null ? ' @ ' . date('d/m H:i', (int) $hito['timestamp']) : ' @ s/TS')
                . ($hito['usuario'] !== null && $hito['usuario'] !== '' ? ' · ' . $hito['usuario'] : ''));
            $card->linea('    ' . (string) ($hito['detalle'] ?? ''));
        }
        if ($deltas !== []) {
            $card->seccion('Deltas entre hitos');
            foreach (array_slice($deltas, 0, 6) as $d) {
                $card->linea($d['hitos'] . ' · ' . $d['delta_min'] . ' min');
            }
        }
        if ($items !== []) {
            $card->seccion('Detalle por ítem')
                ->campo('ÍTEMS', count($items), 'numero');
        }
        return $card
            ->footer('Fuente: MySQL rep_not/gestion + Profit PRUEB25 (NOLOCK) · ' . round($us / 1000) . ' ms')
            ->tarjeta();
    }

    /**
     * Cabecera (rep_not), asignación (asignaciones_preparacion), trazabilidad
     * física (gestion) y devolución (devoluciones) desde el MySQL legacy.
     *
     * @param array<int,string> $advertencias (por referencia)
     *
     * @return array{hitos:array<int,array<string,mixed>>,estado:string,devolucion:array|null}
     */
    private function cabeceraYTrazabilidadMySQL(string $numNota, array &$advertencias): array
    {
        $hitos = [];
        try {
            $pdo = $this->conectarMySQL();
        } catch (PDOException $e) {
            $advertencias[] = 'MySQL legacy no disponible: ' . self::aUtf8($e->getMessage())
                            . ' — timeline limitado a Profit PRUEB25.';
            return ['hitos' => $hitos, 'estado' => 'DESCONOCIDO', 'devolucion' => null];
        }

        $hitos = [];
        $estado = 'DESCONOCIDO';
        $devolucion = null;
        $sedeCol = null;

        // 1) Cabecera rep_not.
        $tablaNotas = $this->resolverTabla($pdo, ['rep_not', 'notas', 'nota']);
        if ($tablaNotas !== null) {
            $cols = $this->resolverColumnas($pdo, $tablaNotas, [
                'cod'      => ['cod_nota', 'num_nota', 'cd_barr', 'numero_nota'],
                'estado'   => ['estatus', 'estado', 'status'],
                'fec_cre'  => ['fec_creacion', 'fecha', 'fec_emis', 'fecha_creacion'],
                'fec_imp'  => ['fec_impr', 'fec_profit'],
                'ruta'     => ['ruta'],
                'verif'    => ['verificacion', 'verifi'],
            ]);
            if ($cols['cod'] !== null) {
                $sql = 'SELECT ' . implode(', ', array_filter([
                    self::q($cols['cod']) . ' AS cod',
                    $cols['estado'] !== null ? self::q($cols['estado']) . ' AS estado' : null,
                    $cols['fec_cre'] !== null ? self::q($cols['fec_cre']) . ' AS fec_cre' : null,
                    $cols['fec_imp'] !== null ? self::q($cols['fec_imp']) . ' AS fec_imp' : null,
                    $cols['ruta'] !== null ? self::q($cols['ruta']) . ' AS ruta' : null,
                    $cols['verif'] !== null ? self::q($cols['verif']) . ' AS verif' : null,
                ]))
                    . ' FROM ' . self::q($tablaNotas)
                    . ' WHERE ' . self::q($cols['cod']) . ' = ? LIMIT 1';
                try {
                    $stmt = $pdo->prepare($sql);
                    $stmt->execute([$numNota]);
                    $cab = $stmt->fetch(PDO::FETCH_ASSOC);
                    if (is_array($cab)) {
                        $estado = self::primerValor($cab, ['estado'], 'DESCONOCIDO');
                        $fecCre = self::fechaHito((string) ($cab['fec_cre'] ?? ''));
                        $fecImp = self::fechaHito((string) ($cab['fec_imp'] ?? ''));
                        $hitos[] = [
                            'fase'      => 'CREACION_NOTA',
                            'hito'      => 'T0',
                            'timestamp' => $fecCre,
                            'delta_s'   => null,
                            'usuario'   => null,
                            'detalle'   => 'Nota creada en el sistema de gestión',
                        ];
                        $hitos[] = [
                            'fase'      => 'CIERRE_IMPRESION',
                            'hito'      => 'T5',
                            'timestamp' => $fecImp,
                            'delta_s'   => null,
                            'usuario'   => null,
                            'detalle'   => 'Cierre/impresión de la nota' .
                                ($cab['ruta'] !== null && trim((string) $cab['ruta']) !== ''
                                    ? ' | Ruta: ' . self::aUtf8((string) $cab['ruta'])
                                    : ''),
                        ];
                    }
                } catch (PDOException $e) {
                    $advertencias[] = 'rep_not no consultable: ' . self::aUtf8($e->getMessage());
                }
            } else {
                $advertencias[] = 'La cabecera de notas (' . $tablaNotas . ') no tiene columna de código reconocible.';
            }
        } else {
            $advertencias[] = 'Ninguna tabla de cabecera de nota encontrada en MySQL (candidatas: rep_not, notas, nota).';
        }

        // 2) Asignación a mesa.
        $tablaAsig = $this->resolverTabla($pdo, ['asignaciones_preparacion', 'asignacion_preparacion', 'asignaciones']);
        if ($tablaAsig !== null) {
            $cols = $this->resolverColumnas($pdo, $tablaAsig, [
                'cod'    => ['cod_nota', 'num_nota', 'cd_barr'],
                'trab'   => ['numero_trabajador', 'num_trab', 'trabajador'],
                'asign'  => ['asignado_en', 'asignado', 'fecha_asignacion'],
                'comp'   => ['completado_en', 'completado', 'fecha_completado'],
                'tam'    => ['tamano_nota', 'items', 'cant_items'],
            ]);
            if ($cols['cod'] !== null && $cols['asign'] !== null) {
                $sql = 'SELECT ' . self::q($cols['cod']) . ' AS cod'
                     . ($cols['trab'] !== null ? ', ' . self::q($cols['trab']) . ' AS trab' : '')
                     . ', ' . self::q($cols['asign']) . ' AS asign'
                     . ($cols['comp'] !== null ? ', ' . self::q($cols['comp']) . ' AS comp' : '')
                     . ($cols['tam'] !== null ? ', ' . self::q($cols['tam']) . ' AS tam' : '')
                     . ' FROM ' . self::q($tablaAsig)
                     . ' WHERE ' . self::q($cols['cod']) . ' = ? ORDER BY ' . self::q($cols['asign'])
                     . ' DESC LIMIT 1';
                try {
                    $stmt = $pdo->prepare($sql);
                    $stmt->execute([$numNota]);
                    $asig = $stmt->fetch(PDO::FETCH_ASSOC);
                    if (is_array($asig)) {
                        $hitos[] = [
                            'fase'      => 'ASIGNACION_MESA',
                            'hito'      => 'T1',
                            'timestamp' => self::fechaHito((string) ($asig['asign'] ?? '')),
                            'delta_s'   => null,
                            'usuario'   => self::aUtf8((string) ($asig['trab'] ?? '')),
                            'detalle'   => 'Asignada a mesa | ítems: ' . ($asig['tam'] ?? '?')
                                . (($asig['comp'] ?? null) !== null && trim((string) $asig['comp']) !== ''
                                    ? ' | completada: ' . self::fechaHito((string) $asig['comp'])
                                    : ' | en proceso'),
                        ];
                    }
                } catch (PDOException $e) {
                    $advertencias[] = 'asignaciones no consultable: ' . self::aUtf8($e->getMessage());
                }
            }
        }

        // 3) Trazabilidad física gestion (preparación/chequeo/embalaje).
        $tablaGest = $this->resolverTabla($pdo, ['gestion', 'gestion_log', 'log_gestion']);
        if ($tablaGest !== null) {
            $cols = $this->resolverColumnas($pdo, $tablaGest, [
                'cod'     => ['cd_barr', 'cod_nota', 'num_nota'],
                'prep'    => ['num_prep', 'preparador', 'preparador_id'],
                'hora1'   => ['hora', 'hora_preparacion', 'fecha_prep'],
                'ub1'     => ['ubicacion', 'estacion_prep', 'mesa_prep'],
                'chq'     => ['num_cheq', 'chequeador', 'chequeador_id'],
                'hora2'   => ['hora2', 'hora_chequeo', 'fecha_cheq'],
                'ub2'     => ['ubicacion2', 'estacion_cheq'],
                'mesa'    => ['numeroMesa', 'num_mesa'],
                'emb'     => ['num_emb', 'embalador', 'embalador_id'],
                'hora3'   => ['hora3', 'hora_embalaje', 'fecha_emb'],
                'ub3'     => ['ubicacion3', 'estacion_emb'],
                'mesaEmb' => ['num_mesa_emba', 'num_mesa_embalaje'],
            ]);
            if ($cols['cod'] !== null) {
                $sql = 'SELECT ' . self::q($cols['cod']) . ' AS cod'
                     . ($cols['prep'] !== null ? ', ' . self::q($cols['prep']) . ' AS prep' : '')
                     . ($cols['hora1'] !== null ? ', ' . self::q($cols['hora1']) . ' AS hora1' : '')
                     . ($cols['ub1'] !== null ? ', ' . self::q($cols['ub1']) . ' AS ub1' : '')
                     . ($cols['chq'] !== null ? ', ' . self::q($cols['chq']) . ' AS chq' : '')
                     . ($cols['hora2'] !== null ? ', ' . self::q($cols['hora2']) . ' AS hora2' : '')
                     . ($cols['ub2'] !== null ? ', ' . self::q($cols['ub2']) . ' AS ub2' : '')
                     . ($cols['mesa'] !== null ? ', ' . self::q($cols['mesa']) . ' AS mesa' : '')
                     . ($cols['emb'] !== null ? ', ' . self::q($cols['emb']) . ' AS emb' : '')
                     . ($cols['hora3'] !== null ? ', ' . self::q($cols['hora3']) . ' AS hora3' : '')
                     . ($cols['ub3'] !== null ? ', ' . self::q($cols['ub3']) . ' AS ub3' : '')
                     . ($cols['mesaEmb'] !== null ? ', ' . self::q($cols['mesaEmb']) . ' AS mesaEmb' : '')
                     . ' FROM ' . self::q($tablaGest)
                     . ' WHERE ' . self::q($cols['cod']) . ' = ? LIMIT 1';
                try {
                    $stmt = $pdo->prepare($sql);
                    $stmt->execute([$numNota]);
                    $gest = $stmt->fetch(PDO::FETCH_ASSOC);
                    if (is_array($gest)) {
                        $hitos[] = [
                            'fase'      => 'PREPARACION',
                            'hito'      => 'T2',
                            'timestamp' => self::fechaHito((string) ($gest['hora1'] ?? '')),
                            'delta_s'   => null,
                            'usuario'   => self::aUtf8((string) ($gest['prep'] ?? '')),
                            'detalle'   => 'Preparación en ' . self::primerValor($gest, ['ub1'], 'Mesa de Picking'),
                        ];
                        $hitos[] = [
                            'fase'      => 'CHEQUEO',
                            'hito'      => 'T3',
                            'timestamp' => self::fechaHito((string) ($gest['hora2'] ?? '')),
                            'delta_s'   => null,
                            'usuario'   => self::aUtf8((string) ($gest['chq'] ?? '')),
                            'detalle'   => 'Chequeo | mesa: ' . self::primerValor($gest, ['mesa'], '—')
                                . ($cols['ub2'] !== null ? ' | ' . self::primerValor($gest, ['ub2'], '') : ''),
                        ];
                        $hitos[] = [
                            'fase'      => 'EMBALAJE',
                            'hito'      => 'T4',
                            'timestamp' => self::fechaHito((string) ($gest['hora3'] ?? '')),
                            'delta_s'   => null,
                            'usuario'   => self::aUtf8((string) ($gest['emb'] ?? '')),
                            'detalle'   => 'Embalaje/bulto | mesa: ' . self::primerValor($gest, ['mesaEmb'], '—')
                                . ($cols['ub3'] !== null ? ' | ' . self::primerValor($gest, ['ub3'], '') : ''),
                        ];
                    }
                } catch (PDOException $e) {
                    $advertencias[] = 'gestion no consultable: ' . self::aUtf8($e->getMessage());
                }
            }
        }

        // 4) Devolución posentrega.
        $tablaDev = $this->resolverTabla($pdo, ['devoluciones', 'devolucion']);
        if ($tablaDev !== null) {
            $cols = $this->resolverColumnas($pdo, $tablaDev, [
                'cod'    => ['num_devo', 'num_nota', 'cod_nota', 'cd_barr'],
                'fecha'  => ['fecha', 'fecha_devolucion'],
                'trab'   => ['num_trab', 'numero_trabajador'],
                'puntos' => ['cant_puntos', 'puntos'],
            ]);
            if ($cols['cod'] !== null) {
                $sql = 'SELECT ' . implode(', ', array_filter([
                    self::q($cols['cod']) . ' AS cod',
                    $cols['fecha'] !== null ? self::q($cols['fecha']) . ' AS fecha' : null,
                    $cols['trab'] !== null ? self::q($cols['trab']) . ' AS trab' : null,
                    $cols['puntos'] !== null ? self::q($cols['puntos']) . ' AS puntos' : null,
                ])) . ' FROM ' . self::q($tablaDev)
                    . ' WHERE ' . self::q($cols['cod']) . ' = ? LIMIT 1';
                try {
                    $stmt = $pdo->prepare($sql);
                    $stmt->execute([$numNota]);
                    $dev = $stmt->fetch(PDO::FETCH_ASSOC);
                    if (is_array($dev)) {
                        $devolucion = [
                            'num_devolucion' => self::aUtf8((string) ($dev['cod'] ?? '')),
                            'fecha'          => self::fechaHito((string) ($dev['fecha'] ?? '')),
                            'trabajador'     => self::aUtf8((string) ($dev['trab'] ?? '')),
                            'cant_puntos'    => self::fNum($dev['puntos'] ?? 0),
                        ];
                        $hitos[] = [
                            'fase'      => 'ENTREGA_DEVOLUCION',
                            'hito'      => 'T6',
                            'timestamp' => $devolucion['fecha'],
                            'delta_s'   => null,
                            'usuario'   => $devolucion['trabajador'],
                            'detalle'   => 'Devolución registrada (' . $devolucion['num_devolucion'] . ')',
                        ];
                    }
                } catch (PDOException $e) {
                    $advertencias[] = 'devoluciones no consultable: ' . self::aUtf8($e->getMessage());
                }
            }
        }

        return ['hitos' => $hitos, 'estado' => (string) $estado, 'devolucion' => $devolucion];
    }

    /**
     * Detalle por ítem desde Profit (reng_nde) + devolución por artículo
     * (reng_dev). Best-effort: si PRUEB25 no responde, retorna [].
     *
     * @param array<int,string> $advertencias (por referencia)
     *
     * @return array<int,array<string,mixed>>
     */
    private function itemsDesdeProfit(string $numNota, array &$advertencias): array
    {
        $wrapper = new ConnectionWrapper();
        $tablaReng = $wrapper->resolverTabla(['reng_nde', 'reng_ndd']);
        if ($tablaReng === null) {
            try {
                $wrapper->queryProfit('SELECT 1');
            } catch (PDOException $e) {
                $advertencias[] = 'Profit PRUEB25 no disponible: ' . self::aUtf8($e->getMessage());
                return [];
            }
            $advertencias[] = 'No existe reng_nde/reng_ndd en PRUEB25.';
            return [];
        }
        $cols = $wrapper->resolverColumnas($tablaReng, [
            'num'    => ['num_doc', 'fact_num'],
            'art'    => ['co_art', 'cod_art'],
            'sol'    => ['total_art', 'cant_sol', 'cant_solicitada'],
            'desp'   => ['stotal_art', 'cant_desp', 'cant_despachada'],
            'pend'   => ['pendiente'],
            'reng'   => ['reng_num', 'num_reng'],
        ]);
        if ($cols['num'] === null || $cols['art'] === null) {
            $advertencias[] = 'reng_nde no tiene las columnas de documento/artículo requeridas.';
            return [];
        }

        $items = [];
        $sql = 'SELECT ' . $wrapper->qPara($cols['art']) . ' AS co_art'
             . ($cols['sol'] !== null ? ', ' . $wrapper->qPara($cols['sol']) . ' AS sol' : '')
             . ($cols['desp'] !== null ? ', ' . $wrapper->qPara($cols['desp']) . ' AS desp' : '')
             . ($cols['pend'] !== null ? ', ' . $wrapper->qPara($cols['pend']) . ' AS pend' : '')
             . ($cols['reng'] !== null ? ', ' . $wrapper->qPara($cols['reng']) . ' AS reng' : '')
             . ' FROM ' . $wrapper->qPara($tablaReng) . ' WITH (NOLOCK)'
             . ' WHERE ' . $wrapper->qPara($cols['num']) . ' = ? ORDER BY ' . $wrapper->qPara($cols['reng']);
        try {
            $filas = $wrapper->querySafe($sql, [$numNota]);
        } catch (PDOException $e) {
            $advertencias[] = 'reng_nde no consultable: ' . self::aUtf8($e->getMessage());
            return [];
        }

        $devueltas = $this->devueltasPorArticulo($wrapper);
        foreach ($filas as $fila) {
            $co = self::aUtf8((string) ($fila['co_art'] ?? ''));
            $sol = self::fNum($fila['sol'] ?? 0);
            $desp = isset($fila['desp']) ? self::fNum($fila['desp']) : $sol;
            $items[] = [
                'reng'          => (int) ($fila['reng'] ?? 0),
                'co_art'        => $co,
                'cant_solicitada' => $sol,
                'cant_despachada' => $desp,
                'cant_pendiente'  => isset($fila['pend']) && $fila['pend'] !== null ? self::fNum($fila['pend']) : max(0.0, $sol - $desp),
                'cant_devuelta'   => $devueltas[$co] ?? 0.0,
            ];
        }
        return $items;
    }

    /**
     * Cantidad devuelta por artículo (reng_dev) en PRUEB25, best-effort.
     *
     * @return array<string,float> co_art => suma cant_dev
     */
    private function devueltasPorArticulo(ConnectionWrapper $wrapper): array
    {
        $tablaDev = $wrapper->resolverTabla(['reng_dev', 'reng_dev_CT']);
        if ($tablaDev === null) {
            return [];
        }
        $cols = $wrapper->resolverColumnas($tablaDev, [
            'art' => ['co_art', 'cod_art'],
            'dev' => ['cant_dev', 'cant_xdev'],
        ]);
        if ($cols['art'] === null || $cols['dev'] === null) {
            return [];
        }
        try {
            $filas = $wrapper->querySafe(
                'SELECT ' . $wrapper->qPara($cols['art']) . ' AS co_art, SUM(' . $wrapper->qPara($cols['dev']) . ') AS total '
                . 'FROM ' . $wrapper->qPara($tablaDev) . ' WITH (NOLOCK) GROUP BY ' . $wrapper->qPara($cols['art'])
            );
            $mapa = [];
            foreach ($filas as $fila) {
                $mapa[(string) ($fila['co_art'] ?? '')] = (float) ($fila['total'] ?? 0);
            }
            return $mapa;
        } catch (PDOException $e) {
            return [];
        }
    }

    /**
     * Calcula los deltas (Δ segundos) entre hitos cronológicos consecutivos
     * con timestamp parseable.
     *
     * @param array<int,array<string,mixed>> $timeline
     *
     * @return array<int,array<string,mixed>>
     */
    private function calcularDeltas(array $timeline): array
    {
        $conTS = [];
        foreach ($timeline as $hito) {
            $ts = self::fechaHito((string) ($hito['timestamp'] ?? ''));
            if ($ts !== null) {
                $conTS[] = ['hito' => (string) $hito['hito'], 'fase' => (string) $hito['fase'], 'ts' => $ts];
            }
        }
        usort($conTS, static fn (array $a, array $b): int => $a['ts'] <=> $b['ts']);
        $deltas = [];
        for ($i = 1, $n = count($conTS); $i < $n; ++$i) {
            $deltas[] = [
                'fase'   => $conTS[$i - 1]['fase'] . ' → ' . $conTS[$i]['fase'],
                'hitos'  => $conTS[$i - 1]['hito'] . ' → ' . $conTS[$i]['hito'],
                'delta_s' => $conTS[$i]['ts'] - $conTS[$i - 1]['ts'],
                'delta_min' => round(($conTS[$i]['ts'] - $conTS[$i - 1]['ts']) / 60, 1),
            ];
        }
        return $deltas;
    }

    /**
     * Normaliza un timestamp de BD a int (epoch) o null si no es parseable.
     * Acepta datetime ISO/MySQL y horas cortas HH:MM.
     */
    private static function fechaHito(string $valor): ?int
    {
        $valor = trim($valor);
        if ($valor === '' || $valor === '0000-00-00 00:00:00' || $valor === '0') {
            return null;
        }
        if (preg_match('/^\d{1,2}:\d{2}(:\d{2})?$/', $valor) === 1) {
            // Hora corta sin fecha: se ancla a hoy (contextual).
            $hoy = date('Y-m-d');
            $valor = $hoy . ' ' . $valor;
        }
        if (is_numeric($valor) && (int) $valor > 1000000000) {
            return (int) $valor;
        }
        $ts = strtotime($valor);
        return $ts === false ? null : $ts;
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
