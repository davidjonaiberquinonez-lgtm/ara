<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Almacen;

use App\Services\ConnectionWrapper;
use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;
use PDO;
use PDOException;

/**
 * Smart Allocation & Performance Local: mide el desempeño en tiempo real de
 * los operarios de almacén y recomienda a qué preparador asignar cada nota
 * según su volumen.
 *
 * Fuentes (MySQL legacy):
 *   - asignaciones_preparacion: asignado_en/completado_en + tamano_nota +
 *     numero_trabajador → velocidad real por nota (renglones/minuto), marcas
 *     personales por volumen (1-5 / 6-20 / 21+ ítems) y carga activa
 *     (asignaciones sin completar).
 *   - gestion: cruce del preparador final (num_prep) por nota cuando hay
 *     nota sin asignación registrada.
 *
 * Recomendación: al pedir por una nota de X renglones, responde el preparador
 * ideal DISPONIBLE (mayor rpm en ese rango de volumen y menor carga activa)
 * para maximizar la velocidad global de salida.
 *
 * Lógica de ranking/recomendación en métodos puros (testeables sin BD).
 * Nunca escribe; nunca lanza excepción por datos ausentes.
 */
final class RendimientoPreparadoresSmartAssignTool implements AgentToolInterface
{
    use AlmacenDbTrait;

    /** Rangos de volumen para marcas personales. */
    private const RANGOS_VOLUMEN = [
        ['id' => '1-5',   'min' => 1,  'max' => 5],
        ['id' => '6-20',  'min' => 6,  'max' => 20],
        ['id' => '21+',   'min' => 21, 'max' => null],
    ];

    public function getName(): string
    {
        return 'rendimiento_preparadores_smart_assign';
    }

    public function getDescription(): string
    {
        return 'Mide el desempeño real de los preparadores de almacén '
             . '(renglones/minuto, marcas personales por volumen 1-5/6-20/21+ '
             . 'ítems y carga activa actual) y recomienda a cuál operario '
             . 'asignar una nota según su volumen para maximizar la velocidad '
             . 'de salida. Parámetros: num_nota (opcional; con él devuelve el '
             . 'preparador ideal), fecha (YYYY-MM-DD opcional, default hoy) y '
             . 'limite (default 50).';
    }

    public function getParameters(): array
    {
        return [
            'type'       => 'object',
            'properties' => [
                'num_nota' => [
                    'type'        => 'string',
                    'description' => 'Número de la nota a asignar (opcional; devuelve recomendación de preparador).',
                ],
                'fecha' => [
                    'type'        => 'string',
                    'description' => 'Día del ranking en formato YYYY-MM-DD (default: hoy).',
                ],
                'limite' => [
                    'type'        => 'integer',
                    'description' => 'Máximo de preparadores en el ranking (default 50).',
                ],
            ],
            'required' => [],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $t0 = self::micro();
        $numNota = trim((string) ($arguments['num_nota'] ?? ''));
        $fecha = trim((string) ($arguments['fecha'] ?? date('Y-m-d')));
        $limite = max(1, min(200, (int) ($arguments['limite'] ?? 50)));
        $advertencias = [];

        try {
            $pdo = $this->conectarMySQL();
        } catch (PDOException $e) {
            return ['success' => false, 'error' => 'MySQL legacy no disponible: ' . self::aUtf8($e->getMessage())];
        }

        $registros = $this->registrosDesempeno($pdo, $fecha, $advertencias);
        if ($registros === [] && $advertencias === []) {
            return ['success' => true, 'data' => [
                'ranking'           => [],
                'sugerencia'        => null,
                'operadores_del_dia'=> [],
                'cuellos_de_botella'=> [],
                'alerta_rendimiento'=> [],
                'fecha_consulta'    => date('c'),
                'advertencias'      => ['Sin registros de desempeño para la fecha ' . $fecha . '.'],
                'metric_us'         => (int) ((self::micro() - $t0) * 1_000_000),
            ], 'card' => CardBuilder::iniciar('📭', 'SIN REGISTROS DE DESEMPEÑO')
                ->linea('No hay asignaciones registradas para el día ' . $fecha . '.')
                ->footer('Fuente: MySQL asignaciones_preparacion')
                ->tarjeta()];
        }

        // Ranking puro + carga activa.
        $ranking = self::calcularRanking($registros);
        $cargaActiva = self::cargaActiva($registros);
        foreach ($ranking as &$fila) {
            $fila['carga_activa_renglones'] = $cargaActiva[$fila['usuario']] ?? 0;
            $fila['disponible'] = ($cargaActiva[$fila['usuario']] ?? 0) < $fila['rpm'] * 15;
        }
        unset($fila);
        usort($ranking, static fn (array $a, array $b): int =>
            ($b['notas_completadas'] <=> $a['notas_completadas'])
            ?: ($b['rpm'] <=> $a['rpm'])
        );

        // Sugerencia para una nota concreta.
        $sugerencia = null;
        if ($numNota !== '') {
            $renglones = $this->renglonesDeNota($pdo, $numNota, $advertencias);
            $sugerencia = self::recomendarPreparador($ranking, $renglones, $numNota);
        }

        // ── AD10 / v4.17: operadores del día + cuellos de botella ────────────
        $operadores = self::operadoresDelDia($registros);
        $pdo = null;

        $rankingFiltrado = array_slice($ranking, 0, $limite);
        $card = $this->tarjetaRendimiento($fecha, $rankingFiltrado, $sugerencia, $operadores, (int) ((self::micro() - $t0) * 1_000_000));

        return [
            'success' => true,
            'data'    => [
                'fecha'             => $fecha,
                'ranking'           => $rankingFiltrado,
                'sugerencia'        => $sugerencia,
                'operadores_del_dia'=> $operadores['operadores_del_dia'],
                'cuellos_de_botella'=> $operadores['cuellos_de_botella'],
                'alerta_rendimiento'=> $operadores['alerta_rendimiento'],
                'fecha_consulta'    => date('c'),
                'advertencias'      => $advertencias,
                'metric_us'         => (int) ((self::micro() - $t0) * 1_000_000),
            ],
            'card'    => $card,
        ];
    }

    /**
     * Operadores del día (AD10 / v4.17): por operador {id, nombre, area,
     * depto, ops, ops_pendientes, tiempo_promedio, tipo}; cuellos de botella
     * cuando ops_pendientes > 20 (con recomendación) y alertas de rendimiento
     * cuando tiempo_promedio > umbral (default 30 min).
     *
     * @param array<int,array<string,mixed>> $registros
     *
     * @return array<string,mixed>
     */
    public static function operadoresDelDia(array $registros): array
    {
        $umbralMin = 30.0;
        $porOperador = [];
        foreach ($registros as $r) {
            $u = trim((string) ($r['usuario'] ?? ''));
            if ($u === '') {
                continue;
            }
            if (!isset($porOperador[$u])) {
                $porOperador[$u] = ['ops' => 0, 'ops_pendientes' => 0, 'sum_minutos' => 0.0, 'ops_completadas' => 0];
            }
            $porOperador[$u]['ops']++;
            $minutos = max(0.0, (float) ($r['minutos'] ?? 0));
            if (!empty($r['completado'])) {
                $porOperador[$u]['sum_minutos'] += $minutos;
                $porOperador[$u]['ops_completadas']++;
            } else {
                $porOperador[$u]['ops_pendientes']++;
            }
        }

        $operadores = [];
        $cuellos = [];
        $alertas = [];
        foreach ($porOperador as $usuario => $p) {
            $tiempoPromedio = $p['ops_completadas'] > 0
                ? round($p['sum_minutos'] / $p['ops_completadas'], 1)
                : 0.0;
            $operadores[] = [
                'id'              => $usuario,
                'nombre'          => $usuario,
                'area'            => 'Almacén',
                'depto'           => 'Preparación',
                'ops'             => $p['ops'],
                'ops_pendientes'  => $p['ops_pendientes'],
                'tiempo_promedio' => $tiempoPromedio,
                'tipo'            => 'Preparación',
            ];
            if ($p['ops_pendientes'] > 20) {
                $cuellos[] = [
                    'id'              => $usuario,
                    'nombre'          => $usuario,
                    'ops_pendientes'  => $p['ops_pendientes'],
                    'recomendacion'   => 'Redistribuir ' . ($p['ops_pendientes'] - 20)
                        . ' operación(es) hacia otro preparador disponible.',
                ];
            }
            if ($tiempoPromedio > $umbralMin) {
                $alertas[] = [
                    'id'              => $usuario,
                    'nombre'          => $usuario,
                    'tiempo_promedio' => $tiempoPromedio,
                    'umbral_min'      => $umbralMin,
                    'tipo'            => 'RENDIMIENTO',
                    'mensaje'         => 'El preparador ' . $usuario . ' promedia '
                        . $tiempoPromedio . ' min por operación (umbral ' . $umbralMin . ' min).',
                ];
            }
        }
        usort($operadores, static fn (array $a, array $b): int => $b['ops'] <=> $a['ops']);
        usort($cuellos, static fn (array $a, array $b): int => $b['ops_pendientes'] <=> $a['ops_pendientes']);

        return [
            'operadores_del_dia' => $operadores,
            'cuellos_de_botella' => $cuellos,
            'alerta_rendimiento' => $alertas,
        ];
    }

    /** Tarjeta estándar v4.13 del ranking de preparadores (CardBuilder). */
    private function tarjetaRendimiento(string $fecha, array $ranking, ?array $sugerencia, array $operadores, int $us): string
    {
        $card = CardBuilder::iniciar('🏆', 'RENDIMIENTO DE PREPARADORES (' . $fecha . ')');
        if ($ranking === []) {
            $card->linea('Sin preparadores con registros para la fecha.');
        } else {
            $card->seccion('Ranking (rpm = renglones/minuto)');
            foreach (array_slice($ranking, 0, 6) as $i => $p) {
                $disponible = (bool) ($p['disponible'] ?? true);
                $card->linea(($i + 1) . '. ' . $p['usuario'] . ' · rpm ' . $p['rpm']
                    . ' · ' . $p['notas_completadas'] . '/' . $p['notas'] . ' notas · carga '
                    . $p['carga_activa_renglones'] . ' reng · ' . ($disponible ? '✅ disponible' : '⏳ ocupado'));
            }
            $ocultos = count($ranking) - 6;
            if ($ocultos > 0) {
                $card->linea('⏩ +' . $ocultos . ' preparador(es) más en el ranking.');
            }
        }
        if ($sugerencia !== null) {
            $card->seccion('Sugerencia de asignación')
                ->campo('NOTA', $sugerencia['num_nota'])
                ->campo('PREPARADOR IDEAL', $sugerencia['preparador'])
                ->campo('RPM EN RANGO ' . $sugerencia['rango_volumen'], $sugerencia['rpm_rango'], 'decimal')
                ->campo('RPM GLOBAL', $sugerencia['rpm_global'], 'decimal');
        }
        $cuellos = (array) ($operadores['cuellos_de_botella'] ?? []);
        if ($cuellos !== []) {
            $card->seccion('Cuellos de botella (>20 ops pendientes)');
            foreach (array_slice($cuellos, 0, 3) as $cb) {
                $card->linea('🚨 ' . $cb['nombre'] . ' · ' . $cb['ops_pendientes'] . ' ops pendientes → ' . $cb['recomendacion']);
            }
        }
        $alertas = (array) ($operadores['alerta_rendimiento'] ?? []);
        if ($alertas !== []) {
            $card->seccion('Alertas de rendimiento');
            foreach (array_slice($alertas, 0, 2) as $al) {
                $card->linea('⚠️ ' . $al['mensaje']);
            }
        }
        return $card
            ->footer('Fuente: MySQL asignaciones_preparacion + Profit PRUEB25 (NOLOCK) · ' . round($us / 1000) . ' ms')
            ->tarjeta();
    }

    /**
     * RANKING PURO (sin I/O): a partir de registros crudos calcula velocidad
     * promedio (renglones/minuto) y marcas personales por rango de volumen.
     *
     * @param array<int,array<string,mixed>> $registros [{usuario, tamano, minutos, completado}]
     *
     * @return array<int,array<string,mixed>> Ranking ordenado por rpm desc
     */
    public static function calcularRanking(array $registros): array
    {
        $porUsuario = [];
        foreach ($registros as $r) {
            $u = (string) ($r['usuario'] ?? '');
            if ($u === '') {
                continue;
            }
            $tamano = (float) ($r['tamano'] ?? 0);
            $minutos = max(0.001, (float) ($r['minutos'] ?? 0));
            $completado = (bool) ($r['completado'] ?? true);
            $porUsuario[$u][] = [
                'tamano' => $tamano,
                'minutos' => $minutos,
                'completado' => $completado,
                'rango' => self::rangoVolumen((int) $tamano),
            ];
        }
        $ranking = [];
        foreach ($porUsuario as $usuario => $notas) {
            $completadas = array_values(array_filter($notas, static fn (array $n): bool => $n['completado']));
            $renglones = array_sum(array_map(static fn (array $n): float => $n['tamano'], $completadas));
            $minutos = array_sum(array_map(static fn (array $n): float => $n['minutos'], $completadas));
            $rpm = $minutos > 0 ? round($renglones / $minutos, 2) : 0.0;
            $marcas = [];
            foreach (self::RANGOS_VOLUMEN as $rango) {
                $delRango = array_values(array_filter($notas, static fn (array $n): bool => $n['rango'] === $rango['id']));
                $completasRango = array_values(array_filter($delRango, static fn (array $n): bool => $n['completado']));
                $sumReng = array_sum(array_map(static fn (array $n): float => $n['tamano'], $completasRango));
                $sumMin = array_sum(array_map(static fn (array $n): float => $n['minutos'], $completasRango));
                $marcas[$rango['id']] = [
                    'notas'  => count($delRango),
                    'completadas' => count($completasRango),
                    'rpm'    => $sumMin > 0 ? round($sumReng / $sumMin, 2) : 0.0,
                ];
            }
            $ranking[] = [
                'usuario'          => $usuario,
                'notas'            => count($notas),
                'notas_completadas'=> count($completadas),
                'renglones'        => (int) $renglones,
                'minutos'          => round($minutos, 1),
                'rpm'              => $rpm,
                'marcas_por_volumen' => $marcas,
            ];
        }
        usort($ranking, static fn (array $a, array $b): int => $b['rpm'] <=> $a['rpm']);
        return $ranking;
    }

    /**
     * CARGA ACTIVA PURO: renglones en notas asignadas sin completar, por
     * operario.
     *
     * @param array<int,array<string,mixed>> $registros
     *
     * @return array<string,int>
     */
    public static function cargaActiva(array $registros): array
    {
        $carga = [];
        foreach ($registros as $r) {
            if (!empty($r['completado'])) {
                continue;
            }
            $u = (string) ($r['usuario'] ?? '');
            if ($u === '') {
                continue;
            }
            $carga[$u] = ($carga[$u] ?? 0) + (int) ($r['tamano'] ?? 0);
        }
        return $carga;
    }

    /**
     * RECOMENDACIÓN PURA: preparador ideal para una nota de N renglones.
     * Prioriza rpm en el rango de volumen de la nota, desempata por rpm
     * global y solo considera operarios con carga activa manejable.
     *
     * @param array<int,array<string,mixed>> $ranking
     *
     * @return array<string,mixed>|null
     */
    public static function recomendarPreparador(array $ranking, int $renglonesNota, string $numNota): ?array
    {
        if ($ranking === []) {
            return null;
        }
        $rangoNota = self::rangoVolumen(max(1, $renglonesNota));
        $candidatos = array_values(array_filter(
            $ranking,
            static fn (array $p): bool => (bool) ($p['disponible'] ?? true)
        ));
        if ($candidatos === []) {
            $candidatos = $ranking; // todos ocupados: elegir el de mayor rpm igual
        }
        $mejor = null;
        $mejorClave = null;
        foreach ($candidatos as $c) {
            $rpmRango = (float) ($c['marcas_por_volumen'][$rangoNota]['rpm'] ?? 0);
            $rpmGlobal = (float) $c['rpm'];
            $carga = (int) ($c['carga_activa_renglones'] ?? 0);
            $clave = [$rpmRango, $rpmGlobal, -$carga];
            if ($mejorClave === null || $clave > $mejorClave) {
                $mejorClave = $clave;
                $mejor = $c;
            }
        }
        if ($mejor === null) {
            return null;
        }
        return [
            'num_nota'      => $numNota,
            'renglones_nota'=> $renglonesNota,
            'rango_volumen' => $rangoNota,
            'preparador'    => $mejor['usuario'],
            'rpm_rango'     => (float) ($mejor['marcas_por_volumen'][$rangoNota]['rpm'] ?? 0),
            'rpm_global'    => (float) $mejor['rpm'],
            'carga_activa_renglones' => (int) ($mejor['carga_activa_renglones'] ?? 0),
            'justificacion' => 'Mejor velocidad (' . ($mejor['marcas_por_volumen'][$rangoNota]['rpm'] ?? 0)
                . ' renglones/min en notas de ' . $rangoNota . ' ítems) '
                . 'considerando su carga activa actual.',
        ];
    }

    /**
     * Rango de volumen para una cantidad de ítems.
     */
    private static function rangoVolumen(int $items): string
    {
        foreach (self::RANGOS_VOLUMEN as $rango) {
            if ($items >= $rango['min'] && ($rango['max'] === null || $items <= $rango['max'])) {
                return $rango['id'];
            }
        }
        return '21+';
    }

    /**
     * Registros crudos de desempeño del día desde asignaciones_preparacion
     * (con cruce a gestion si falta el número de trabajador).
     *
     * @param array<int,string> $advertencias (por referencia)
     *
     * @return array<int,array<string,mixed>>
     */
    private function registrosDesempeno(PDO $pdo, string $fecha, array &$advertencias): array
    {
        $tablaAsig = $this->resolverTabla($pdo, ['asignaciones_preparacion', 'asignacion_preparacion', 'asignaciones']);
        if ($tablaAsig === null) {
            $advertencias[] = 'Sin tabla de asignaciones en MySQL legacy — no hay datos de desempeño.';
            return [];
        }
        $cols = $this->resolverColumnas($pdo, $tablaAsig, [
            'trab'  => ['numero_trabajador', 'num_trab', 'trabajador'],
            'tam'   => ['tamano_nota', 'items', 'cant_items'],
            'asign' => ['asignado_en', 'asignado', 'fecha_asignacion'],
            'comp'  => ['completado_en', 'completado', 'fecha_completado'],
        ]);
        if ($cols['trab'] === null || $cols['tam'] === null) {
            $advertencias[] = 'La tabla de asignaciones no expone operario o tamaño de nota.';
            return [];
        }
        $sql = 'SELECT ' . self::q($cols['trab']) . ' AS trab'
             . ', ' . self::q($cols['tam']) . ' AS tam'
             . ($cols['asign'] !== null ? ', ' . self::q($cols['asign']) . ' AS asign' : '')
             . ($cols['comp'] !== null ? ', ' . self::q($cols['comp']) . ' AS comp' : '')
             . ' FROM ' . self::q($tablaAsig)
             . ' WHERE ' . self::q($cols['asign'] ?? $cols['comp'] ?? $cols['trab']) . ' >= ?'
             . ' AND ' . self::q($cols['asign'] ?? $cols['comp'] ?? $cols['trab']) . ' < DATE_ADD(?, INTERVAL 1 DAY)';
        try {
            $stmt = $pdo->prepare($sql);
            $stmt->execute([$fecha . ' 00:00:00', $fecha]);
            $filas = $stmt->fetchAll(PDO::FETCH_ASSOC);
        } catch (PDOException $e) {
            $advertencias[] = 'Asignaciones no consultables: ' . self::aUtf8($e->getMessage());
            return [];
        }

        $registros = [];
        foreach ($filas as $fila) {
            $asign = self::fechaEpoch((string) ($fila['asign'] ?? ''));
            $comp = self::fechaEpoch((string) ($fila['comp'] ?? ''));
            $minutos = ($asign !== null && $comp !== null && $comp >= $asign)
                ? ($comp - $asign) / 60.0
                : 0.0;
            $registros[] = [
                'usuario'   => self::aUtf8((string) ($fila['trab'] ?? '')),
                'tamano'    => (int) ($fila['tam'] ?? 0),
                'minutos'   => round($minutos, 1),
                'completado'=> $comp !== null,
            ];
        }
        return $registros;
    }

    /**
     * Renglones de una nota desde reng_nde (PRUEB25) o tamano_nota de la
     * asignación (fallback). Best-effort → mínimo 1.
     */
    private function renglonesDeNota(PDO $pdo, string $numNota, array &$advertencias): int
    {
        $wrapper = new ConnectionWrapper();
        $tabla = $wrapper->resolverTabla(['reng_nde', 'reng_ndd']);
        if ($tabla !== null) {
            $cols = $wrapper->resolverColumnas($tabla, ['num' => ['num_doc', 'fact_num']]);
            if ($cols['num'] !== null) {
                try {
                    $n = (int) ($wrapper->querySafe(
                        'SELECT COUNT(*) AS n FROM ' . $wrapper->qPara($tabla) . ' WITH (NOLOCK) WHERE ' . $wrapper->qPara($cols['num']) . ' = ?',
                        [$numNota]
                    )[0]['n'] ?? 0);
                    if ($n > 0) {
                        return $n;
                    }
                } catch (PDOException $e) {
                    $advertencias[] = 'reng_nde no consultable: ' . self::aUtf8($e->getMessage());
                } catch (\InvalidArgumentException $e) {
                    $advertencias[] = 'reng_nde no consultable: ' . self::aUtf8($e->getMessage());
                }
            }
        }
        return $this->tamanoAsignacion($pdo, $numNota);
    }

    /**
     * Tamaño de nota desde la tabla de asignaciones (fallback).
     */
    private function tamanoAsignacion(PDO $pdo, string $numNota): int
    {
        $tabla = $this->resolverTabla($pdo, ['asignaciones_preparacion', 'asignacion_preparacion', 'asignaciones']);
        if ($tabla === null) {
            return 1;
        }
        $cols = $this->resolverColumnas($pdo, $tabla, [
            'cod' => ['cod_nota', 'num_nota', 'cd_barr'],
            'tam' => ['tamano_nota', 'items', 'cant_items'],
        ]);
        if ($cols['cod'] === null || $cols['tam'] === null) {
            return 1;
        }
        try {
            $stmt = $pdo->prepare(
                'SELECT ' . self::q($cols['tam']) . ' FROM ' . self::q($tabla)
                . ' WHERE ' . self::q($cols['cod']) . ' = ? ORDER BY ' . self::q($cols['tam']) . ' DESC LIMIT 1'
            );
            $stmt->execute([$numNota]);
            $v = (int) $stmt->fetchColumn();
            return $v > 0 ? $v : 1;
        } catch (PDOException $e) {
            return 1;
        }
    }

    /**
     * Epoch de un timestamp de BD o null.
     */
    private static function fechaEpoch(string $valor): ?int
    {
        $valor = trim($valor);
        if ($valor === '' || $valor === '0000-00-00 00:00:00' || $valor === '0') {
            return null;
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
