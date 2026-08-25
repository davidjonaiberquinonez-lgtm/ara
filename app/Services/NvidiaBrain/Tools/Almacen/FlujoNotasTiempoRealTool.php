<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Almacen;

use App\Services\ConnectionWrapper;
use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;
use PDO;
use PDOException;

/**
 * Monitoreo en tiempo real del flujo de notas activas, agrupadas por almacén
 * y por volumen de ítems (histograma de filas) para el balanceo de carga.
 *
 * Fuentes:
 *   - MySQL legacy: rep_not (cabecera) — notas en estatus de cola
 *     (PENDIENTE / EN_PREPARACION / EN_CHEQUEO / EN_PROCESO...).
 *   - SQL Server PRUEB25: reng_nde — renglones y unidades físicas por nota.
 *
 * Rangos de complejidad (buckets):
 *   B1: 1 ítem        → Surtido Ultra-Rápido / Bulto Cerrado
 *   B2: 2 a 5 ítems   → Media Carga
 *   B3: 6 a 10 ítems  → Carga Estándar
 *   B4: 11 a 20 ítems → Carga Alta
 *   B5: 21+ ítems     → Super Carga / Requerimiento Especial
 *
 * Si la cabecera tiene columna de sede (p.ej. sistema_operaciones), se
 * separa S/C vs BQTO; si no existe, el filtro se omite con aviso honesto.
 * Nunca escribe; nunca lanza excepción por datos ausentes.
 */
final class FlujoNotasTiempoRealTool implements AgentToolInterface
{
    use AlmacenDbTrait;

    /**
     * Estatus que cuentan como nota activa en cola (valores REALES de
     * rep_not.estatus, verificados en vivo contra MySQL legacy — no son
     * los nombres genéricos EN_PREPARACION/EN_CHEQUEO que nunca existían
     * en la tabla y dejaban esta skill siempre vacía).
     */
    private const ESTATUS_ACTIVOS = [
        'PREPARACION', 'IMPRESA', 'CHEQUEO', 'EMBALADA',
    ];

    private const BUCKETS = [
        ['id' => 'B1', 'nombre' => 'Surtido Ultra-Rapido / Bulto Cerrado',  'min' => 1,  'max' => 1],
        ['id' => 'B2', 'nombre' => 'Media Carga',                          'min' => 2,  'max' => 5],
        ['id' => 'B3', 'nombre' => 'Carga Estandar',                       'min' => 6,  'max' => 10],
        ['id' => 'B4', 'nombre' => 'Carga Alta',                           'min' => 11, 'max' => 20],
        ['id' => 'B5', 'nombre' => 'Super Carga / Requerimiento Especial', 'min' => 21, 'max' => null],
    ];

    public function getName(): string
    {
        return 'flujo_notas_tiempo_real';
    }

    public function getDescription(): string
    {
        return 'Analiza en tiempo real las notas activas del sistema (estatus '
             . 'en cola) agrupadas por almacén y por volumen de ítems '
             . '(histograma de filas): Surtido Ultra-Rápido (1 ítem), Media '
             . 'Carga (2-5), Carga Estándar (6-10), Carga Alta (11-20) y Super '
             . 'Carga (21+). Devuelve totales de notas, renglones y unidades '
             . 'físicas por bucket para balancear la asignación física. '
             . 'Parámetros: sede (sc|bqto|todas, default todas) y limite '
             . '(default 200).';
    }

    public function getParameters(): array
    {
        return [
            'type'       => 'object',
            'properties' => [
                'sede' => [
                    'type'        => 'string',
                    'enum'        => ['sc', 'bqto', 'todas'],
                    'description' => 'Sede operativa: "sc" (San Cristóbal), "bqto" (Barquisimeto) o "todas".',
                ],
                'limite' => [
                    'type'        => 'integer',
                    'description' => 'Máximo de notas a analizar (default 200).',
                ],
            ],
            'required' => [],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $t0 = self::micro();
        $sede = strtolower(trim((string) ($arguments['sede'] ?? 'todas')));
        if (!in_array($sede, ['sc', 'bqto', 'todas'], true)) {
            return ['success' => false, 'error' => 'Parámetro "sede" debe ser "sc", "bqto" o "todas".'];
        }
        $limite = max(1, min(1000, (int) ($arguments['limite'] ?? 200)));

        $advertencias = [];

        // ── 0) Flujo del día (AD8 / v4.17): notas_del_dia, despachos,
        //       tendencia por hora, última nota y actualización. ────────────
        $flujoDelDia = $this->flujoDelDia($advertencias);

        // ── 1) Notas activas desde MySQL legacy (cabecera rep_not) ─────────
        try {
            $pdoMy = $this->conectarMySQL();
        } catch (PDOException $e) {
            return ['success' => false, 'error' => 'MySQL legacy no disponible: ' . self::aUtf8($e->getMessage())];
        }
        $tablaNotas = $this->resolverTabla($pdoMy, ['rep_not', 'notas', 'nota']);
        if ($tablaNotas === null) {
            return ['success' => false, 'error' => 'Ninguna tabla de cabecera de notas en MySQL legacy (candidatas: rep_not, notas, nota).'];
        }
        $cols = $this->resolverColumnas($pdoMy, $tablaNotas, [
            'cod'    => ['cod_nota', 'num_nota', 'cd_barr', 'numero_nota'],
            'estado' => ['estatus', 'estado', 'status'],
            'sede'   => ['sede', 'sucursal', 'co_alma', 'almacen'],
        ]);
        if ($cols['cod'] === null || $cols['estado'] === null) {
            return ['success' => false, 'error' => 'La cabecera de notas no expone columna de código o de estatus.'];
        }

        $sql = 'SELECT ' . self::q($cols['cod']) . ' AS cod'
             . ', ' . self::q($cols['estado']) . ' AS estado'
             . ($cols['sede'] !== null ? ', ' . self::q($cols['sede']) . ' AS sede' : '')
             . ' FROM ' . self::q($tablaNotas)
             . ' WHERE ' . self::q($cols['estado']) . ' IN ('
             . implode(',', array_fill(0, count(self::ESTATUS_ACTIVOS), '?'))
             . ')';
        if ($cols['sede'] !== null && $sede !== 'todas') {
            $sql .= ' AND LOWER(' . self::q($cols['sede']) . ') = ?';
        }
        $sql .= ' LIMIT ' . $limite;

        try {
            $stmt = $pdoMy->prepare($sql);
            $params = self::ESTATUS_ACTIVOS;
            if ($cols['sede'] !== null && $sede !== 'todas') {
                $params[] = $sede;
            }
            $stmt->execute($params);
            $notas = $stmt->fetchAll(PDO::FETCH_ASSOC);
        } catch (PDOException $e) {
            return ['success' => false, 'error' => 'Error consultando notas activas: ' . self::aUtf8($e->getMessage())];
        }

        if ($notas === []) {
            return [
                'success' => true,
                'data'    => [
                    'sede'                    => $sede,
                    'notas_activas'           => 0,
                    'buckets'                 => [],
                    'totales'                 => ['notas' => 0, 'renglones' => 0, 'unidades' => 0],
                    'flujo_del_dia'           => $flujoDelDia,
                    'advertencias'            => $advertencias,
                    'metric_us'               => (int) ((self::micro() - $t0) * 1_000_000),
                ],
                'card'    => CardBuilder::iniciar('✅', 'SIN NOTAS ACTIVAS EN COLA')
                    ->linea('No hay notas en estatus de cola (sede: ' . $sede . ').')
                    ->seccion('Panel del día')
                    ->campo('SIN PROCESAR', $flujoDelDia['sin_procesar'], 'numero')
                    ->campo('VERIFICADAS', $flujoDelDia['verificados'], 'numero')
                    ->campo('TARDÍAS (2h+)', $flujoDelDia['tardios'], 'numero')
                    ->footer('Fuente: MySQL rep_not + Profit PRUEB25 (NOLOCK)')
                    ->tarjeta(),
            ];
        }

        // ── 2) Renglones y unidades desde Profit (reng_nde) ─────────────────
        $porNota = $this->renglonesPorNota(array_column($notas, 'cod'), $advertencias);

        // ── 3) Clasificación por buckets ────────────────────────────────────
        $buckets = [];
        foreach (self::BUCKETS as $b) {
            $buckets[$b['id']] = [
                'id'       => $b['id'],
                'nombre'   => $b['nombre'],
                'rango'    => $b['max'] === null ? $b['min'] . '+' : $b['min'] . '-' . $b['max'],
                'notas'    => 0,
                'renglones' => 0,
                'unidades' => 0,
                'notas_lista' => [],
            ];
        }
        $totales = ['notas' => 0, 'renglones' => 0, 'unidades' => 0];
        $porSede = ['sc' => 0, 'bqto' => 0, 'sin_sede' => 0];

        foreach ($notas as $nota) {
            $cod = (string) $nota['cod'];
            $nRenglones = (int) ($porNota[$cod]['renglones'] ?? 0);
            $unidades   = (float) ($porNota[$cod]['unidades'] ?? 0);
            $nRenglones = max(1, $nRenglones); // nota sin detalle cruzado → 1 (columna de cabecera)

            $idBucket = 'B5';
            foreach (self::BUCKETS as $b) {
                if ($nRenglones >= $b['min'] && ($b['max'] === null || $nRenglones <= $b['max'])) {
                    $idBucket = $b['id'];
                    break;
                }
            }
            $buckets[$idBucket]['notas']++;
            $buckets[$idBucket]['renglones'] += $nRenglones;
            $buckets[$idBucket]['unidades'] += $unidades;
            $buckets[$idBucket]['notas_lista'][] = [
                'num_nota'  => $cod,
                'estatus'   => self::aUtf8((string) ($nota['estado'] ?? '')),
                'renglones' => $nRenglones,
                'unidades'  => $unidades,
            ];

            $totales['notas']++;
            $totales['renglones'] += $nRenglones;
            $totales['unidades'] += $unidades;

            $sedeNota = $cols['sede'] !== null
                ? strtolower(trim((string) ($nota['sede'] ?? '')))
                : '';
            if (in_array($sedeNota, ['sc', 'san cristobal', 'san_cristobal', 'sc cristobal'], true)) {
                $porSede['sc']++;
            } elseif (in_array($sedeNota, ['bqto', 'barquisimeto'], true)) {
                $porSede['bqto']++;
            } else {
                $porSede['sin_sede']++;
            }
        }

        // Solo buckets con notas; lista limitada a 10 por bucket (resumen).
        $bucketResumen = [];
        foreach ($buckets as $b) {
            if ($b['notas'] > 0) {
                $b['notas_lista'] = array_slice($b['notas_lista'], 0, 10);
                $bucketResumen[] = $b;
            }
        }

        if ($cols['sede'] === null) {
            $advertencias[] = 'La cabecera de notas no expone columna de sede; la separación S/C vs BQTO queda sin datos.'
                            . ' (disponible en la BD sistema_operaciones del mismo servidor).';
        }
        $pdoMy = null;

        $card = $this->tarjetaFlujo($sede, $bucketResumen, $totales, $porSede, $flujoDelDia, (int) ((self::micro() - $t0) * 1_000_000));

        return [
            'success' => true,
            'data'    => [
                'sede'                    => $sede,
                'notas_activas'           => count($notas),
                'buckets'                 => $bucketResumen,
                'totales'                 => $totales,
                'por_sede'                => $porSede,
                'flujo_del_dia'           => $flujoDelDia,
                'advertencias'            => $advertencias,
                'metric_us'               => (int) ((self::micro() - $t0) * 1_000_000),
            ],
            'card'    => $card,
        ];
    }

    /**
     * Flujo de notas del día (AD8 / v4.17) desde rep_not (MySQL legacy):
     *  - fecha_consulta / hora_ultima_actualizacion / ultima_nota
     *  - notas_del_dia, total_items_despachados, total_monto_despachado_bs
     *  - por_hora[] (hora, cantidad) y tendencia (subiendo/bajando) contra la
     *    hora anterior.
     *
     * @param array<int,string> $advertencias (por referencia)
     *
     * @return array<string,mixed>
     */
    private function flujoDelDia(array &$advertencias): array
    {
        $base = [
            'fecha_consulta'            => date('c'),
            'hora_ultima_actualizacion' => null,
            'notas_del_dia'             => 0,
            'total_items_despachados'   => 0,
            'total_monto_despachado_bs' => 0.0,
            'ultima_nota'               => null,
            'tendencia'                 => 'estable',
            'por_hora'                  => [],
            // Contadores estilo panel legacy "Recepción - Notas" (conexion.php):
            // sin_procesar, verificados y tardíos, calculados sobre rep_not.
            'sin_procesar'              => 0,
            'verificados'               => 0,
            'tardios'                   => 0,
        ];
        try {
            $pdo = $this->conectarMySQL();
        } catch (PDOException $e) {
            $advertencias[] = 'MySQL legacy no disponible para flujo del día: ' . self::aUtf8($e->getMessage());
            return $base;
        }
        $tabla = $this->resolverTabla($pdo, ['rep_not', 'notas', 'nota']);
        if ($tabla === null) {
            $pdo = null;
            $advertencias[] = 'Sin cabecera de notas en MySQL para flujo del día.';
            return $base;
        }
        $cols = $this->resolverColumnas($pdo, $tabla, [
            'cod'   => ['cod_nota', 'num_nota', 'cd_barr'],
            // fec_creacion va PRIMERO en rep_not solo de nombre: su valor real
            // es SIEMPRE '0000-00-00 00:00:00' (columna no usada por el
            // sistema legacy). fec_profit trae solo fecha (hora 00:00:00),
            // así que para el histograma por hora se prioriza fec_impr, que
            // sí trae la hora real del movimiento.
            'fecha' => ['fec_impr', 'fec_profit', 'fecha', 'fec_emis', 'fec_creacion'],
            'fecImpr'=> ['fec_impr'],
            'estado'=> ['estatus', 'estado', 'status'],
            'verif' => ['verificacion', 'verifi'],
            'items' => ['total_art', 'total_items', 'cant_items'],
            'monto' => ['total_monto', 'monto', 'total_neto', 'neto'],
        ]);
        if ($cols['cod'] === null || $cols['fecha'] === null) {
            $pdo = null;
            $advertencias[] = 'La cabecera de notas no expone código/fecha para el flujo del día.';
            return $base;
        }

        $selFecha = self::q($cols['fecha']);
        try {
            $stmt = $pdo->prepare(
                'SELECT ' . self::q($cols['cod']) . ' AS cod'
                . ', ' . $selFecha . ' AS fecha'
                . ($cols['estado'] !== null ? ', ' . self::q($cols['estado']) . ' AS estado' : '')
                . ($cols['verif'] !== null ? ', ' . self::q($cols['verif']) . ' AS verif' : '')
                . ($cols['fecImpr'] !== null ? ', ' . self::q($cols['fecImpr']) . ' AS fec_impr' : '')
                . ($cols['items'] !== null ? ', ' . self::q($cols['items']) . ' AS items' : '')
                . ($cols['monto'] !== null ? ', ' . self::q($cols['monto']) . ' AS monto' : '')
                . ' FROM ' . self::q($tabla)
                . ' WHERE CAST(' . $selFecha . ' AS DATE) = CURDATE()'
                . ' ORDER BY ' . $selFecha . ' DESC LIMIT 3000'
            );
            $stmt->execute();
            $notas = $stmt->fetchAll(PDO::FETCH_ASSOC);
        } catch (PDOException $e) {
            $pdo = null;
            $advertencias[] = 'Flujo del día no consultable: ' . self::aUtf8($e->getMessage());
            return $base;
        }
        $pdo = null;

        $base['notas_del_dia'] = count($notas);
        if ($notas === []) {
            return $base;
        }
        $base['ultima_nota'] = self::aUtf8((string) ($notas[0]['cod'] ?? ''));

        $porHora = [];
        $itemsDespachados = 0;
        $montoDespachadoTotal = 0.0;
        $horaMax = '';
        $sinProcesar = 0;
        $verificados = 0;
        $tardios = 0;
        $ahora = time();
        foreach ($notas as $nota) {
            $fecha = (string) ($nota['fecha'] ?? '');
            $hora = (int) date('G', (int) strtotime((string) substr($fecha, 0, 19)));
            $porHora[$hora] = ($porHora[$hora] ?? 0) + 1;
            if ($fecha > $horaMax) {
                $horaMax = $fecha;
            }
            $estadoNota = strtoupper((string) ($nota['estado'] ?? ''));
            // Despachado = ya llegó a EMBALADA (última etapa que rep_not registra).
            $procesada = $cols['estado'] === null || $estadoNota === 'EMBALADA';
            if ($procesada) {
                $itemsDespachados += $cols['items'] !== null ? (int) ($nota['items'] ?? 0) : 0;
                $montoDespachadoTotal += $cols['monto'] !== null ? (float) ($nota['monto'] ?? 0) : 0.0;
            }

            // "Sin procesar" = aún en la primera etapa (recién creada, sin
            // pasar por chequeo/embalaje todavía).
            if ($estadoNota === 'PREPARACION') {
                $sinProcesar++;
            }
            if ($cols['verif'] !== null && strtoupper((string) ($nota['verif'] ?? '')) === 'VERIFICADA') {
                $verificados++;
            }
            // "Tardía" = ya impresa (o etapa posterior) hace 2+ horas, réplica
            // de la regla del panel legacy (estatus IMPRESA y ≥2h desde fec_impr).
            $fecImpr = (string) ($nota['fec_impr'] ?? '');
            if ($fecImpr !== '' && in_array($estadoNota, ['IMPRESA', 'CHEQUEO', 'EMBALADA'], true)) {
                $tsImpr = strtotime(substr($fecImpr, 0, 19));
                if ($tsImpr !== false && ($ahora - $tsImpr) >= 7200) {
                    $tardios++;
                }
            }
        }
        $base['total_items_despachados'] = $itemsDespachados;
        $base['total_monto_despachado_bs'] = $montoDespachadoTotal;
        $base['hora_ultima_actualizacion'] = $horaMax !== '' ? (string) substr($horaMax, 11, 8) : null;
        $base['sin_procesar'] = $sinProcesar;
        $base['verificados'] = $verificados;
        $base['tardios'] = $tardios;

        ksort($porHora);
        $base['por_hora'] = [];
        foreach ($porHora as $h => $cantidad) {
            $base['por_hora'][] = ['hora' => $h, 'cantidad' => $cantidad];
        }

        // Tendencia: comparar la hora más reciente con la anterior.
        $horas = array_keys($porHora);
        $n = count($horas);
        if ($n >= 2) {
            $actual = $porHora[$horas[$n - 1]];
            $anterior = $porHora[$horas[$n - 2]];
            if ($actual > $anterior) {
                $base['tendencia'] = 'subiendo';
            } elseif ($actual < $anterior) {
                $base['tendencia'] = 'bajando';
            }
        }
        return $base;
    }

    /** Tarjeta estándar v4.13 del flujo de notas (CardBuilder). */
    private function tarjetaFlujo(string $sede, array $buckets, array $totales, array $porSede, array $flujoDelDia, int $us): string
    {
        $card = CardBuilder::iniciar('📊', 'FLUJO DE NOTAS EN TIEMPO REAL (sede: ' . $sede . ')')
            ->seccion('Resumen')
            ->campo('NOTAS ACTIVAS', $totales['notas'], 'numero')
            ->campo('RENGLONES', $totales['renglones'], 'numero')
            ->campo('UNIDADES', $totales['unidades'], 'numero')
            ->campo('POR SEDE', sprintf('SC %d · BQTO %d · sin_sede %d', $porSede['sc'], $porSede['bqto'], $porSede['sin_sede']))
            ->seccion('Panel del día')
            ->campo('NOTAS HOY', $flujoDelDia['notas_del_dia'], 'numero')
            ->campo('SIN PROCESAR', $flujoDelDia['sin_procesar'], 'numero')
            ->campo('VERIFICADAS', $flujoDelDia['verificados'], 'numero')
            ->campo('TARDÍAS (2h+)', $flujoDelDia['tardios'], 'numero')
            ->seccion('Cargas por volumen');
        $maxNotas = 1;
        foreach ($buckets as $b) {
            $maxNotas = max($maxNotas, (int) $b['notas']);
        }
        foreach (array_slice($buckets, 0, 6) as $b) {
            $card->linea($b['id'] . ' ' . $b['rango'] . ' · ' . $b['notas'] . ' nota(s) · '
                . $b['renglones'] . ' reng · ' . number_format((float) $b['unidades'], 0, ',', '.') . ' und');
            $card->barra('', (float) $b['notas'], (float) $maxNotas);
        }
        return $card
            ->footer('Fuente: MySQL rep_not + Profit PRUEB25 (NOLOCK) · ' . round($us / 1000) . ' ms')
            ->tarjeta();
    }

    /**
     * Conteo de renglones y unidades por número de documento desde reng_nde
     * (PRUEB25). Best-effort: [] si Profit no responde.
     *
     * @param array<int,string> $codigos
     * @param array<int,string> $advertencias (por referencia)
     *
     * @return array<string,array{renglones:int,unidades:float}>
     */
    private function renglonesPorNota(array $codigos, array &$advertencias): array
    {
        $codigos = array_values(array_filter(array_map(
            static fn ($c): string => trim((string) $c),
            $codigos
        )));
        if ($codigos === []) {
            return [];
        }
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
            'num' => ['num_doc', 'fact_num'],
            'art' => ['total_art', 'cant_sol'],
        ]);
        if ($cols['num'] === null || $cols['art'] === null) {
            $advertencias[] = 'reng_nde no tiene las columnas de documento/cantidad requeridas.';
            return [];
        }
        $chunks = array_chunk($codigos, 200);
        $porNota = [];
        foreach ($chunks as $chunk) {
            $marks = implode(',', array_fill(0, count($chunk), '?'));
            $sql = 'SELECT ' . $wrapper->qPara($cols['num']) . ' AS num'
                 . ', COUNT(*) AS renglones, SUM(' . $wrapper->qPara($cols['art']) . ') AS unidades'
                 . ' FROM ' . $wrapper->qPara($tablaReng) . ' WITH (NOLOCK)'
                 . ' WHERE ' . $wrapper->qPara($cols['num']) . ' IN (' . $marks . ')'
                 . ' GROUP BY ' . $wrapper->qPara($cols['num']);
            try {
                foreach ($wrapper->querySafe($sql, $chunk) as $fila) {
                    $porNota[(string) ($fila['num'] ?? '')] = [
                        'renglones' => (int) ($fila['renglones'] ?? 0),
                        'unidades'  => (float) ($fila['unidades'] ?? 0),
                    ];
                }
            } catch (PDOException $e) {
                $advertencias[] = 'reng_nde no consultable: ' . self::aUtf8($e->getMessage());
                break;
            }
        }
        return $porNota;
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
