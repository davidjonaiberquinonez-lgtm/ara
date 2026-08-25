<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Almacen;

use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use PDO;
use PDOException;

/**
 * Skill "gestion_super_esteroide_search": procesa búsquedas en texto plano
 * de notas individuales (traza completa de tiempos por estado) o genera un
 * informe global de productividad y tiempos cuando el texto trae la flag
 * 'global' (directiva v4.11).
 *
 * Entrada (texto plano libre, v4.10):
 *   - "72160754"            → traza individual de la nota.
 *   - "global" / "[global]" → dashboard global de productividad.
 *
 * Fuente: MySQL legacy de gestión (rep_not + gestion + usuarios).
 *   - rep_not: cabecera (cod_nota, estatus, fec_creacion/fec_impr/fec_profit).
 *   - gestion: trazabilidad por fase con DATETIME completo:
 *       hora  (preparación) / hora2 (chequeo) / hora3 (embalaje),
 *       num_prep / num_cheq / num_emb (IDs de usuario por fase) y
 *       verifi_pre / verifi_cheq / verifi_emb ('VERIFICADA' cuando aplica).
 *   - usuarios: id / numero / nombre (JOIN por numero).
 *
 * Deltas: Registro → Preparación → Chequeo → Embalaje en minutos + tiempo de
 * vida total. Si una fase no está verificada su delta se reporta como "—".
 *
 * Metas SLA (constantes privadas, editables): cada fase tiene un objetivo en
 * minutos y la eficiencia global mide % de notas dentro del tiempo de vida
 * objetivo.
 *
 * Nunca escribe; nunca lanza excepción por nota inexistente (degrada con
 * diagnóstico honesto). Devuelve SIEMPRE ['success' => bool, 'data' => ...,
 * 'card' => <tarjeta Markdown>] para el chat (patrón v4.10).
 */
final class GestionSuperEsteroideSearchTool implements AgentToolInterface
{
    use AlmacenDbTrait;

    /** Metas SLA por fase (minutos) y vida total (min). Editables. */
    private const META_PREP_MIN  = 15;  // Registro → Preparación
    private const META_CHEQ_MIN  = 10;  // Preparación → Chequeo
    private const META_EMB_MIN   = 10;  // Chequeo → Embalaje
    private const META_VIDA_MIN  = 40;  // Tiempo de vida total

    public function getName(): string
    {
        return 'gestion_super_esteroide_search';
    }

    public function getDescription(): string
    {
        return 'Traza completa de tiempos de una nota (Registro → Preparación → '
             . 'Chequeo → Embalaje, con responsables y deltas en minutos) o '
             . 'dashboard global de productividad del día (notas completas, AVG '
             . 'de vida, eficiencia vs SLA, Top 5 clientes, líderes por proceso '
             . 'y gráficas ASCII). Parámetro: texto plano con el número de la nota '
             . '(7-8 dígitos) o la palabra "global".';
    }

    public function getParameters(): array
    {
        return [
            'type'       => 'object',
            'properties' => [
                'num_nota' => [
                    'type'        => 'string',
                    'description' => 'Número de nota (7-8 dígitos) para la traza individual, o la palabra "global" / "[global]" para el dashboard de productividad.',
                ],
            ],
            'required'   => ['num_nota'],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $t0 = self::micro();

        // Captura flexible (texto plano libre): 'num_nota' → posición 0 →
        // 'query' → 'text' → texto reunido de cualquier clave.
        $texto = trim((string) ($arguments['num_nota'] ?? $arguments[0] ?? $arguments['query'] ?? ''));
        if ($texto === '') {
            $texto = trim(self::reunirTexto($arguments));
        }
        if ($texto === '') {
            return [
                'success' => false,
                'error'   => 'Parámetro "num_nota" obligatorio: número de nota (7-8 dígitos) o "global".',
                'card'    => '❌ Escribe un número de nota (ej. 72160754) o "global" para el dashboard.',
            ];
        }

        $bajo = mb_strtolower($texto, 'UTF-8');
        if (str_contains($bajo, 'global')) {
            return $this->ejecucionGlobal($t0);
        }
        if (preg_match('/([0-9]{7,8})/', $texto, $m)) {
            return $this->ejecucionNotaIndividual($m[1], $t0);
        }

        return [
            'success' => false,
            'error'   => 'Entrada no reconocida: se esperaba un número de nota de 7-8 dígitos o la flag "global".',
            'card'    => '❌ Entrada no reconocida: usa un número de nota (7-8 dígitos) o la flag "global".',
        ];
    }

    // =========================================================================
    // MODO 1: TRAZA INDIVIDUAL
    // =========================================================================

    /**
     * Traza completa de tiempos de una nota: rep_not (cabecera/registro) +
     * gestion (preparación/chequeo/embalaje con responsables) + usuarios
     * (nombres). Calcula deltas en minutos y el tiempo de vida total.
     */
    private function ejecucionNotaIndividual(string $numNota, float $t0): array
    {
        $advertencias = [];
        try {
            $pdo = $this->conectarMySQL();
        } catch (PDOException $e) {
            $msg = 'MySQL legacy no disponible: ' . self::aUtf8($e->getMessage());
            return [
                'success' => true,
                'data'    => [
                    'num_nota'     => $numNota,
                    'encontrada'   => false,
                    'advertencias' => [$msg],
                    'metric_us'    => (int) ((self::micro() - $t0) * 1_000_000),
                ],
                'card' => '⚠️ ' . $msg . ' (no se pudo consultar la traza de la nota ' . $numNota . ').',
            ];
        }

        $cabecera = $this->cabeceraNota($pdo, $numNota, $advertencias);
        $gestion  = $this->gestionNota($pdo, $numNota, $advertencias);

        if ($cabecera === null && $gestion === null) {
            $msg = 'La nota "' . $numNota . '" no existe en las fuentes de gestión (rep_not/gestion).';
            return [
                'success' => true,
                'data'    => [
                    'num_nota'     => $numNota,
                    'encontrada'   => false,
                    'advertencias' => $advertencias,
                    'metric_us'    => (int) ((self::micro() - $t0) * 1_000_000),
                ],
                'card' => '❌ ' . $msg,
            ];
        }

        // ── Hitos de tiempo (solo fases realmente verificadas) ─────────────
        $registro = self::fechaHito((string) ($cabecera['fec_creacion'] ?? ''));
        if ($registro === null) {
            $registro = self::fechaHito((string) ($cabecera['fec_impr'] ?? ''));
        }
        if ($registro === null) {
            $registro = self::fechaHito((string) ($cabecera['fec_profit'] ?? ''));
        }

        $fases = [
            [
                'clave'    => 'preparacion',
                'titulo'   => 'Preparación',
                'emoji'    => '🟡',
                'hora'     => (string) ($gestion['hora'] ?? ''),
                'verif'    => (string) ($gestion['verifi_pre'] ?? ''),
                'operador' => (string) ($gestion['num_prep'] ?? ''),
                'nombre'   => (string) ($gestion['nom_prep'] ?? ''),
            ],
            [
                'clave'    => 'chequeo',
                'titulo'   => 'Chequeo',
                'emoji'    => '🔵',
                'hora'     => (string) ($gestion['hora2'] ?? ''),
                'verif'    => (string) ($gestion['verifi_cheq'] ?? ''),
                'operador' => (string) ($gestion['num_cheq'] ?? ''),
                'nombre'   => (string) ($gestion['nom_chq'] ?? ''),
            ],
            [
                'clave'    => 'embalaje',
                'titulo'   => 'Embalaje',
                'emoji'    => '🟣',
                'hora'     => (string) ($gestion['hora3'] ?? ''),
                'verif'    => (string) ($gestion['verifi_emb'] ?? ''),
                'operador' => (string) ($gestion['num_emb'] ?? ''),
                'nombre'   => (string) ($gestion['nom_emb'] ?? ''),
            ],
        ];

        $hitos = [];
        foreach ($fases as $f) {
            $ts = self::fechaHito($f['hora']);
            $hitos[$f['clave']] = ($ts !== null) ? $ts : null;
        }

        // ── Deltas en minutos ──────────────────────────────────────────────
        $deltaRegistroPrep = $hitos['preparacion'] !== null && $registro !== null
            ? (int) round(($hitos['preparacion'] - $registro) / 60)
            : null;
        $deltaPrepCheq = $hitos['chequeo'] !== null && $hitos['preparacion'] !== null
            ? (int) round(($hitos['chequeo'] - $hitos['preparacion']) / 60)
            : null;
        $deltaCheqEmb = $hitos['embalaje'] !== null && $hitos['chequeo'] !== null
            ? (int) round(($hitos['embalaje'] - $hitos['chequeo']) / 60)
            : null;

        $ultimoHito = $hitos['embalaje'] ?? $hitos['chequeo'] ?? $hitos['preparacion'] ?? null;
        $vidaTotal = ($ultimoHito !== null && $registro !== null)
            ? (int) round(($ultimoHito - $registro) / 60)
            : null;

        // ── Datos estructurados para el LLM ────────────────────────────────
        $traza = [];
        $traza[] = [
            'fase'      => 'REGISTRO',
            'timestamp' => $registro !== null ? date('Y-m-d H:i:s', $registro) : null,
            'usuario'   => '',
            'minutos'   => null,
        ];
        foreach ($fases as $i => $f) {
            $traza[] = [
                'fase'      => strtoupper($f['clave']),
                'timestamp' => $hitos[$f['clave']] !== null ? date('Y-m-d H:i:s', $hitos[$f['clave']]) : null,
                'usuario'   => $f['operador'] !== '' && $f['operador'] !== '0'
                    ? trim($f['nombre'] . ' (' . $f['operador'] . ')')
                    : '',
                'verificada' => $f['verif'] === 'VERIFICADA',
            ];
        }

        $data = [
            'num_nota'      => $numNota,
            'encontrada'    => true,
            'co_cliente'    => self::aUtf8((string) ($gestion['co_cli'] ?? 'N/A')),
            'cliente'       => self::aUtf8((string) ($gestion['cli_des'] ?? 'N/A')),
            'cant_items'    => (int) ($gestion['cant_items'] ?? 0),
            'estatus'       => self::aUtf8((string) ($cabecera['estatus'] ?? 'DESCONOCIDO')),
            'traza'         => $traza,
            'deltas_min'    => [
                'registro_a_preparacion' => $deltaRegistroPrep,
                'preparacion_a_chequeo'  => $deltaPrepCheq,
                'chequeo_a_embalaje'     => $deltaCheqEmb,
            ],
            'vida_total_min' => $vidaTotal,
            'advertencias'  => $advertencias,
            'metric_us'     => (int) ((self::micro() - $t0) * 1_000_000),
        ];

        return [
            'success' => true,
            'data'    => $data,
            'card'    => $this->tarjetaNotaIndividual($data),
        ];
    }

    /** Cabecera de la nota (rep_not) → estatus y fechas de registro. */
    private function cabeceraNota(PDO $pdo, string $numNota, array &$advertencias): ?array
    {
        $tabla = $this->resolverTabla($pdo, ['rep_not', 'notas', 'nota']);
        if ($tabla === null) {
            return null;
        }
        $cols = $this->resolverColumnas($pdo, $tabla, [
            'cod'    => ['cod_nota', 'num_nota', 'cd_barr'],
            'estado' => ['estatus', 'estado', 'status'],
            'fec_cre'=> ['fec_creacion', 'fecha', 'fec_emis'],
            'fec_imp'=> ['fec_impr', 'fec_profit'],
        ]);
        if ($cols['cod'] === null) {
            return null;
        }
        $sql = 'SELECT ' . implode(', ', array_filter([
            $cols['estado'] !== null ? self::q($cols['estado']) . ' AS estatus' : null,
            $cols['fec_cre'] !== null ? self::q($cols['fec_cre']) . ' AS fec_creacion' : null,
            $cols['fec_imp'] !== null ? self::q($cols['fec_imp']) . ' AS fec_impr' : null,
        ])) . ' FROM ' . self::q($tabla) . ' WHERE ' . self::q($cols['cod']) . ' = ? LIMIT 1';
        try {
            $stmt = $pdo->prepare($sql);
            $stmt->execute([$numNota]);
            $fila = $stmt->fetch(PDO::FETCH_ASSOC);
            return is_array($fila) ? $fila : null;
        } catch (PDOException $e) {
            $advertencias[] = 'Cabecera no consultable: ' . self::aUtf8($e->getMessage());
            return null;
        }
    }

    /** Trazabilidad por fases (gestion) con nombres de operadores resueltos. */
    private function gestionNota(PDO $pdo, string $numNota, array &$advertencias): ?array
    {
        $tabla = $this->resolverTabla($pdo, ['gestion', 'gestion_log', 'log_gestion']);
        if ($tabla === null) {
            return null;
        }
        $cols = $this->resolverColumnas($pdo, $tabla, [
            'cod'     => ['cd_barr', 'cod_nota', 'num_nota'],
            'cli'     => ['co_cli', 'cod_cliente'],
            'cli_des' => ['cli_des', 'cliente', 'nombre_cliente'],
            'prep'    => ['num_prep', 'preparador'],
            'hora1'   => ['hora', 'fecha_prep'],
            'chq'     => ['num_cheq', 'chequeador'],
            'hora2'   => ['hora2', 'fecha_cheq'],
            'emb'     => ['num_emb', 'embalador'],
            'hora3'   => ['hora3', 'fecha_emb'],
            'vpre'    => ['verifi_pre', 'verificado_prep'],
            'vchq'    => ['verifi_cheq', 'verificado_cheq'],
            'vemb'    => ['verifi_emb', 'verificado_emb'],
            'cant'    => ['cant_items', 'total_items'],
        ]);
        if ($cols['cod'] === null) {
            return null;
        }
        // Resolución de nombres: usuarios.numero = num_prep / num_cheq / num_emb.
        $sql = 'SELECT '
             . implode(', ', array_filter([
                 $cols['cli'] !== null ? self::q($cols['cli']) . ' AS co_cli' : null,
                 $cols['cli_des'] !== null ? self::q($cols['cli_des']) . ' AS cli_des' : null,
                 $cols['prep'] !== null ? self::q($cols['prep']) . ' AS num_prep' : null,
                 $cols['hora1'] !== null ? self::q($cols['hora1']) . ' AS hora' : null,
                 $cols['chq'] !== null ? self::q($cols['chq']) . ' AS num_cheq' : null,
                 $cols['hora2'] !== null ? self::q($cols['hora2']) . ' AS hora2' : null,
                 $cols['emb'] !== null ? self::q($cols['emb']) . ' AS num_emb' : null,
                 $cols['hora3'] !== null ? self::q($cols['hora3']) . ' AS hora3' : null,
                 $cols['vpre'] !== null ? self::q($cols['vpre']) . ' AS verifi_pre' : null,
                 $cols['vchq'] !== null ? self::q($cols['vchq']) . ' AS verifi_cheq' : null,
                 $cols['vemb'] !== null ? self::q($cols['vemb']) . ' AS verifi_emb' : null,
                 $cols['cant'] !== null ? self::q($cols['cant']) . ' AS cant_items' : null,
                 'u1.nombre AS nom_prep',
                 'u2.nombre AS nom_chq',
                 'u3.nombre AS nom_emb',
             ]))
             . ' FROM ' . self::q($tabla) . ' g'
             . ' LEFT JOIN ' . self::q('usuarios') . ' u1 ON u1.' . self::q('numero') . ' = g.' . self::q($cols['prep'])
             . ' LEFT JOIN ' . self::q('usuarios') . ' u2 ON u2.' . self::q('numero') . ' = g.' . self::q($cols['chq'])
             . ' LEFT JOIN ' . self::q('usuarios') . ' u3 ON u3.' . self::q('numero') . ' = g.' . self::q($cols['emb'])
             . ' WHERE g.' . self::q($cols['cod']) . ' = ? LIMIT 1';
        try {
            $stmt = $pdo->prepare($sql);
            $stmt->execute([$numNota]);
            $fila = $stmt->fetch(PDO::FETCH_ASSOC);
            return is_array($fila) ? $fila : null;
        } catch (PDOException $e) {
            $advertencias[] = 'gestion no consultable: ' . self::aUtf8($e->getMessage());
            return null;
        }
    }

    /** Tarjeta (Muestra 1): línea de tiempo + responsables + deltas. */
    private function tarjetaNotaIndividual(array $d): string
    {
        $lineas = [
            '🧪 *TRAZA DE TIEMPOS — NOTA #' . $d['num_nota'] . '*',
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━',
            '📋 *Cliente:* ' . $d['cliente'] . ' (' . $d['co_cliente'] . ')',
            '📦 *Items:* ' . $d['cant_items'] . ' | 📌 *Estatus:* ' . $d['estatus'],
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━',
            '⏱ *LÍNEA DE TIEMPO*',
        ];

        $traza = $d['traza'] ?? [];
        $i = 1; // el paso 1 siempre es el Registro
        foreach ($traza as $hito) {
            $fase = (string) ($hito['fase'] ?? '');
            $ts   = (string) ($hito['timestamp'] ?? '');
            $usr  = (string) ($hito['usuario'] ?? '');
            $ver  = (bool) ($hito['verificada'] ?? false);
            if ($fase === 'REGISTRO') {
                $lineas[] = '  1️⃣ 🟢 *Registro:* ' . ($ts !== '' ? $ts : '— (sin timestamp)');
                continue;
            }
            $i++;
            $emoji = ['PREPARACION' => '🟡', 'CHEQUEO' => '🔵', 'EMBALAJE' => '🟣'][$fase] ?? '⚪';
            $estadoFase = ($ts === '' || $ts === '0000-00-00 00:00:00')
                ? '— (no registrado' . ($ver ? ')' : ', pendiente)')
                : $ts . ($usr !== '' ? ' — ' . $usr : '');
            $lineas[] = '  ' . $this->numeroCirculo($i) . ' ' . $emoji . ' *' . ucfirst(strtolower($fase)) . ':* ' . $estadoFase;
        }

        $lineas[] = '━━━━━━━━━━━━━━━━━━━━━━━━━━━━';
        $lineas[] = '⏱ *DELTAS DE TIEMPO*';
        $deltas = $d['deltas_min'] ?? [];
        $lineas[] = '  • Registro → Preparación: ' . $this->fmtDelta($deltas['registro_a_preparacion'] ?? null);
        $lineas[] = '  • Preparación → Chequeo: ' . $this->fmtDelta($deltas['preparacion_a_chequeo'] ?? null);
        $lineas[] = '  • Chequeo → Embalaje: ' . $this->fmtDelta($deltas['chequeo_a_embalaje'] ?? null);
        $vida = $d['vida_total_min'] ?? null;
        $lineas[] = '  • 💠 *Tiempo de Vida Total:* ' . ($vida !== null ? $vida . 'm' : '—');
        $lineas[] = '━━━━━━━━━━━━━━━━━━━━━━━━━━━━';

        return implode("\n", $lineas);
    }

    /** 1️⃣2️⃣3️⃣4️⃣ para los pasos de la línea de tiempo. */
    private function numeroCirculo(int $n): string
    {
        $mapa = [1 => '1️⃣', 2 => '2️⃣', 3 => '3️⃣', 4 => '4️⃣'];
        return $mapa[$n] ?? (string) $n . '️⃣';
    }

    /** Delta en minutos con sufijo, o "—" si la fase no aplica. */
    private function fmtDelta(?int $min): string
    {
        return $min !== null ? $min . 'm' : '—';
    }

    // =========================================================================
    // MODO 2: DASHBOARD GLOBAL
    // =========================================================================

    /**
     * Dashboard global del día: métricas generales (notas que completaron los
     * 3 procesos, AVG de vida, eficiencia vs SLA), Top 5 clientes, líderes por
     * proceso con % del total del día y gráficas ASCII por fase.
     */
    private function ejecucionGlobal(float $t0): array
    {
        $advertencias = [];
        try {
            $pdo = $this->conectarMySQL();
        } catch (PDOException $e) {
            $msg = 'MySQL legacy no disponible: ' . self::aUtf8($e->getMessage());
            return [
                'success' => true,
                'data'    => ['advertencias' => [$msg], 'metric_us' => (int) ((self::micro() - $t0) * 1_000_000)],
                'card'    => '⚠️ ' . $msg . ' (no se pudo generar el dashboard global).',
            ];
        }

        // ── 1) Métricas generales (notas 3P del día + tiempos promedio) ─────
        $metricas = $this->metricasGlobales($pdo, $advertencias);

        // ── 2) Top 5 clientes del día ───────────────────────────────────────
        $topClientes = $this->topClientesDia($pdo, $advertencias);

        // ── 3) Líderes por proceso (preparación/chequeo/embalaje) ───────────
        $lideres = [];
        foreach ([
            ['proceso' => 'preparacion', 'titulo' => 'Preparación', 'col' => 'num_prep', 'verif' => 'verifi_pre', 'hito' => 'hora'],
            ['proceso' => 'chequeo',     'titulo' => 'Chequeo',     'col' => 'num_cheq', 'verif' => 'verifi_cheq', 'hito' => 'hora2'],
            ['proceso' => 'embalaje',    'titulo' => 'Embalaje',    'col' => 'num_emb',  'verif' => 'verifi_emb',  'hito' => 'hora3'],
        ] as $cfg) {
            $lideres[$cfg['proceso']] = $this->liderProceso($pdo, $cfg, $advertencias);
        }

        // ── 4) Gráficas ASCII de rendimiento por fase vs meta ───────────────
        $avgPrep = $metricas['avg_registro_a_prep'] ?? null;
        $avgCheq = $metricas['avg_prep_a_cheq'] ?? null;
        $avgEmb  = $metricas['avg_cheq_a_emb'] ?? null;
        $avgVida = $metricas['avg_vida_total'] ?? null;

        $data = [
            'fecha'             => date('Y-m-d'),
            'total_notas_dia'   => $metricas['total_dia'] ?? 0,
            'completas_3p'      => $metricas['completas_3p'] ?? 0,
            'avg_vida_total'    => $avgVida,
            'eficiencia_sla'    => $metricas['eficiencia_sla'] ?? null,
            'notas_dentro_sla'  => $metricas['notas_dentro_sla'] ?? 0,
            'sla_objetivo_min'  => self::META_VIDA_MIN,
            'tiempos_avg_min'   => [
                'registro_a_preparacion' => $avgPrep,
                'preparacion_a_chequeo'  => $avgCheq,
                'chequeo_a_embalaje'     => $avgEmb,
            ],
            'metas_min'         => [
                'registro_a_preparacion' => self::META_PREP_MIN,
                'preparacion_a_chequeo'  => self::META_CHEQ_MIN,
                'chequeo_a_embalaje'     => self::META_EMB_MIN,
                'vida_total'             => self::META_VIDA_MIN,
            ],
            'top_clientes'      => $topClientes,
            'lideres_proceso'   => $lideres,
            'advertencias'      => $advertencias,
            'metric_us'         => (int) ((self::micro() - $t0) * 1_000_000),
        ];

        return [
            'success' => true,
            'data'    => $data,
            'card'    => $this->tarjetaDashboardGlobal($data),
        ];
    }

    /** Métricas agregadas del día (una sola consulta 3P + total del día). */
    private function metricasGlobales(PDO $pdo, array &$advertencias): array
    {
        $tablaG = $this->resolverTabla($pdo, ['gestion', 'gestion_log', 'log_gestion']);
        $tablaR = $this->resolverTabla($pdo, ['rep_not', 'notas', 'nota']);
        if ($tablaG === null) {
            $advertencias[] = 'Sin tabla de gestión para métricas globales.';
            return ['total_dia' => 0, 'completas_3p' => 0];
        }
        $cols = $this->resolverColumnas($pdo, $tablaG, [
            'cod'     => ['cd_barr', 'cod_nota', 'num_nota'],
            'hora'    => ['hora', 'fecha_prep'],
            'hora2'   => ['hora2', 'fecha_cheq'],
            'hora3'   => ['hora3', 'fecha_emb'],
            'vpre'    => ['verifi_pre', 'verificado_prep'],
            'vchq'    => ['verifi_cheq', 'verificado_cheq'],
            'vemb'    => ['verifi_emb', 'verificado_emb'],
        ]);
        if ($cols['cod'] === null || $cols['hora'] === null
            || $cols['hora2'] === null || $cols['hora3'] === null
            || $cols['vpre'] === null || $cols['vchq'] === null || $cols['vemb'] === null) {
            $advertencias[] = 'gestion sin columnas de trazabilidad completas (hora/hora2/hora3/verifi_*).';
            return ['total_dia' => 0, 'completas_3p' => 0];
        }
        $g = 'g';

        // Total de notas iniciadas en el día (preparación del día).
        try {
            $sql = 'SELECT COUNT(*) FROM ' . self::q($tablaG) . ' ' . $g
                 . ' WHERE ' . $g . '.' . self::q($cols['hora'])
                 . ' >= CURDATE() AND ' . $g . '.' . self::q($cols['hora']) . ' < CURDATE() + INTERVAL 1 DAY';
            $totalDia = (int) $pdo->query($sql)->fetchColumn();
        } catch (PDOException $e) {
            $advertencias[] = 'Total del día no consultable: ' . self::aUtf8($e->getMessage());
            $totalDia = 0;
        }

        // Notas 3P del día (JOIN rep_not para el registro real de la nota).
        $registroExpr = 'NULL';
        if ($tablaR !== null) {
            $colsR = $this->resolverColumnas($pdo, $tablaR, [
                'cod'  => ['cod_nota', 'num_nota', 'cd_barr'],
                'fec_cre' => ['fec_creacion', 'fecha', 'fec_emis'],
                'fec_imp' => ['fec_impr', 'fec_profit'],
            ]);
            if ($colsR['cod'] !== null) {
                $cre = $colsR['fec_cre'] !== null ? self::q($colsR['fec_cre']) : 'NULL';
                $imp = $colsR['fec_imp'] !== null ? self::q($colsR['fec_imp']) : 'NULL';
                $registroExpr = 'COALESCE(NULLIF(r.' . $cre . ', \'0000-00-00 00:00:00\'), '
                              . ($imp !== 'NULL' ? 'NULLIF(r.' . $imp . ', \'0000-00-00 00:00:00\')' : 'NULL')
                              . ')';
            }
        }

        $sql3p = 'SELECT COUNT(*) AS c, '
               . ($registroExpr !== 'NULL'
                    ? 'AVG(TIMESTAMPDIFF(MINUTE, ' . $registroExpr . ', g.' . self::q($cols['hora3']) . ')) AS vida, '
                      . 'AVG(TIMESTAMPDIFF(MINUTE, ' . $registroExpr . ', g.' . self::q($cols['hora']) . ')) AS f1, '
                      . 'AVG(TIMESTAMPDIFF(MINUTE, g.' . self::q($cols['hora']) . ', g.' . self::q($cols['hora2']) . ')) AS f2, '
                      . 'AVG(TIMESTAMPDIFF(MINUTE, g.' . self::q($cols['hora2']) . ', g.' . self::q($cols['hora3']) . ')) AS f3, '
                      . 'SUM(CASE WHEN TIMESTAMPDIFF(MINUTE, ' . $registroExpr . ', g.' . self::q($cols['hora3']) . ') <= ' . self::META_VIDA_MIN . ' THEN 1 ELSE 0 END) AS sla_ok'
                    : '0 AS vida, 0 AS f1, 0 AS f2, 0 AS f3, 0 AS sla_ok')
               . ' FROM ' . self::q($tablaG) . ' ' . $g
               . ($tablaR !== null ? ' LEFT JOIN ' . self::q($tablaR) . ' r ON r.' . self::q($colsR['cod'] ?? 'cod_nota') . ' = BINARY CAST(' . $g . '.' . self::q($cols['cod']) . ' AS CHAR)' : '')
               . ' WHERE ' . $g . '.' . self::q($cols['vpre']) . ' = \'VERIFICADA\''
               . ' AND ' . $g . '.' . self::q($cols['vchq']) . ' = \'VERIFICADA\''
               . ' AND ' . $g . '.' . self::q($cols['vemb']) . ' = \'VERIFICADA\''
               . ' AND ' . $g . '.' . self::q($cols['hora3'])
               . ' >= CURDATE() AND ' . $g . '.' . self::q($cols['hora3']) . ' < CURDATE() + INTERVAL 1 DAY';

        $completas = 0;
        $avgVida = $avgF1 = $avgF2 = $avgF3 = $slaOk = null;
        try {
            $fila = $pdo->query($sql3p)->fetch(PDO::FETCH_ASSOC);
            if (is_array($fila)) {
                $completas = (int) ($fila['c'] ?? 0);
                $avgVida = $fila['vida'] !== null ? round((float) $fila['vida'], 2) : null;
                $avgF1 = $fila['f1'] !== null ? round((float) $fila['f1'], 2) : null;
                $avgF2 = $fila['f2'] !== null ? round((float) $fila['f2'], 2) : null;
                $avgF3 = $fila['f3'] !== null ? round((float) $fila['f3'], 2) : null;
                $slaOk = (int) ($fila['sla_ok'] ?? 0);
            }
        } catch (PDOException $e) {
            $advertencias[] = 'Métricas 3P no consultables: ' . self::aUtf8($e->getMessage());
        }

        $eficiencia = ($completas > 0 && $slaOk !== null) ? round($slaOk / $completas * 100, 1) : null;

        return [
            'total_dia'         => $totalDia,
            'completas_3p'      => $completas,
            'avg_vida_total'    => $avgVida,
            'avg_registro_a_prep' => $avgF1,
            'avg_prep_a_cheq'   => $avgF2,
            'avg_cheq_a_emb'    => $avgF3,
            'notas_dentro_sla'  => $slaOk,
            'eficiencia_sla'    => $eficiencia,
        ];
    }

    /** Top 5 clientes del día por volumen de notas. */
    private function topClientesDia(PDO $pdo, array &$advertencias): array
    {
        $tabla = $this->resolverTabla($pdo, ['gestion', 'gestion_log', 'log_gestion']);
        if ($tabla === null) {
            return [];
        }
        $cols = $this->resolverColumnas($pdo, $tabla, [
            'hora'    => ['hora', 'fecha_prep'],
            'co_cli'  => ['co_cli', 'cod_cliente'],
            'cli_des' => ['cli_des', 'cliente', 'nombre_cliente'],
        ]);
        if ($cols['hora'] === null || $cols['co_cli'] === null) {
            return [];
        }
        $sql = 'SELECT ' . self::q($cols['co_cli']) . ' AS co_cli, '
             . ($cols['cli_des'] !== null ? 'TRIM(' . self::q($cols['cli_des']) . ') AS cli_des, ' : 'NULL AS cli_des, ')
             . 'COUNT(*) AS total FROM ' . self::q($tabla)
             . ' WHERE ' . self::q($cols['hora']) . ' >= CURDATE() AND ' . self::q($cols['hora'])
             . ' < CURDATE() + INTERVAL 1 DAY'
             . ' GROUP BY ' . self::q($cols['co_cli'])
             . ($cols['cli_des'] !== null ? ', ' . self::q($cols['cli_des']) : '')
             . ' ORDER BY total DESC LIMIT 5';
        try {
            $filas = $pdo->query($sql)->fetchAll(PDO::FETCH_ASSOC);
            $top = [];
            foreach ($filas as $f) {
                $top[] = [
                    'co_cli'  => self::aUtf8((string) ($f['co_cli'] ?? '')),
                    'cliente' => self::aUtf8((string) ($f['cli_des'] ?? '')),
                    'total'   => (int) ($f['total'] ?? 0),
                ];
            }
            return $top;
        } catch (PDOException $e) {
            $advertencias[] = 'Top clientes no consultable: ' . self::aUtf8($e->getMessage());
            return [];
        }
    }

    /**
     * Líder del día en un proceso: operador con más notas verificadas en el
     * hito del proceso + porcentaje sobre el total del proceso del día.
     */
    private function liderProceso(PDO $pdo, array $cfg, array &$advertencias): array
    {
        $tabla = $this->resolverTabla($pdo, ['gestion', 'gestion_log', 'log_gestion']);
        if ($tabla === null) {
            return ['operador' => null, 'nombre' => '', 'total' => 0, 'porcentaje' => null];
        }
        $cols = $this->resolverColumnas($pdo, $tabla, [
            'col'  => [$cfg['col']],
            'verif'=> [$cfg['verif']],
            'hito' => [$cfg['hito']],
        ]);
        if ($cols['col'] === null || $cols['verif'] === null || $cols['hito'] === null) {
            return ['operador' => null, 'nombre' => '', 'total' => 0, 'porcentaje' => null];
        }
        $sql = 'SELECT ' . self::q($cols['col']) . ' AS operador, COUNT(*) AS total'
             . ' FROM ' . self::q($tabla)
             . ' WHERE ' . self::q($cols['hito']) . ' >= CURDATE() AND ' . self::q($cols['hito'])
             . ' < CURDATE() + INTERVAL 1 DAY'
             . ' AND ' . self::q($cols['verif']) . ' = \'VERIFICADA\''
             . ' AND ' . self::q($cols['col']) . ' > 0'
             . ' GROUP BY ' . self::q($cols['col'])
             . ' ORDER BY total DESC';
        try {
            $filas = $pdo->query($sql)->fetchAll(PDO::FETCH_ASSOC);
            if ($filas === []) {
                return ['operador' => null, 'nombre' => '', 'total' => 0, 'porcentaje' => null];
            }
            $totalProceso = 0;
            foreach ($filas as $f) {
                $totalProceso += (int) ($f['total'] ?? 0);
            }
            $lider = $filas[0];
            $op = (int) ($lider['operador'] ?? 0);
            $totalLider = (int) ($lider['total'] ?? 0);

            // Nombre del operador desde usuarios.
            $nombre = '';
            try {
                $stmt = $pdo->prepare('SELECT nombre FROM ' . self::q('usuarios') . ' WHERE ' . self::q('numero') . ' = ? LIMIT 1');
                $stmt->execute([(string) $op]);
                $n = $stmt->fetchColumn();
                $nombre = is_string($n) ? self::aUtf8($n) : '';
            } catch (PDOException $e) {
                $advertencias[] = 'Nombre de operador no resuelto: ' . self::aUtf8($e->getMessage());
            }

            return [
                'operador'   => $op > 0 ? $op : null,
                'nombre'     => $nombre,
                'total'      => $totalLider,
                'porcentaje' => $totalProceso > 0 ? round($totalLider / $totalProceso * 100, 1) : null,
            ];
        } catch (PDOException $e) {
            $advertencias[] = 'Líder de ' . $cfg['proceso'] . ' no consultable: ' . self::aUtf8($e->getMessage());
            return ['operador' => null, 'nombre' => '', 'total' => 0, 'porcentaje' => null];
        }
    }

    /** Tarjeta (Muestra 2): dashboard global completo. */
    private function tarjetaDashboardGlobal(array $d): string
    {
        $lineas = [
            '📊 *DASHBOARD GLOBAL DE GESTIÓN* (SUPER ESTEROIDE)',
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━',
            '📅 *Día:* ' . $d['fecha'] . ' | 🎯 *SLA objetivo:* ' . $d['sla_objetivo_min'] . 'm de vida',
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━',
            '⚙️ *MÉTRICAS GENERALES*',
            '  • Notas iniciadas del día: ' . number_format((int) ($d['total_notas_dia'] ?? 0), 0, ',', '.'),
            '  • Notas completas (3 procesos): ' . number_format((int) ($d['completas_3p'] ?? 0), 0, ',', '.'),
            '  • ⏱ *Tiempo de vida promedio:* ' . $this->fmtAvg($d['avg_vida_total'] ?? null) . 'm',
            '  • ✅ *Eficiencia vs SLA:* ' . $this->fmtPct($d['eficiencia_sla'] ?? null)
                . ' (' . number_format((int) ($d['notas_dentro_sla'] ?? 0), 0, ',', '.') . '/' . number_format((int) ($d['completas_3p'] ?? 0), 0, ',', '.') . ' dentro de ' . $d['sla_objetivo_min'] . 'm)',
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━',
            '🏆 *TOP 5 CLIENTES DEL DÍA*',
        ];

        $top = $d['top_clientes'] ?? [];
        if ($top === []) {
            $lineas[] = '  _(Sin notas en el día.)_';
        } else {
            $pos = 0;
            foreach ($top as $c) {
                $pos++;
                $lineas[] = '  ' . $pos . '. ' . $c['co_cli'] . ' — ' . $c['cliente']
                    . ' (' . number_format((int) $c['total'], 0, ',', '.') . ' notas)';
            }
        }

        $lineas[] = '━━━━━━━━━━━━━━━━━━━━━━━━━━━━';
        $lineas[] = '👥 *ÍNDICE DE PRODUCTIVIDAD POR OPERADOR*';
        $lideres = $d['lideres_proceso'] ?? [];
        foreach ([
            ['clave' => 'preparacion', 'titulo' => 'Preparación', 'emoji' => '🟡'],
            ['clave' => 'chequeo',     'titulo' => 'Chequeo',     'emoji' => '🔵'],
            ['clave' => 'embalaje',    'titulo' => 'Embalaje',    'emoji' => '🟣'],
        ] as $cfg) {
            $l = $lideres[$cfg['clave']] ?? [];
            $op = $l['operador'] ?? null;
            if ($op === null) {
                $lineas[] = '  ' . $cfg['emoji'] . ' *' . $cfg['titulo'] . ':* — (sin datos del día)';
                continue;
            }
            $lineas[] = '  ' . $cfg['emoji'] . ' *' . $cfg['titulo'] . ':* ' . ($l['nombre'] !== '' ? $l['nombre'] : 'Op. ' . $op)
                . ' (' . $op . ') — ' . number_format((int) $l['total'], 0, ',', '.') . ' notas'
                . ' (' . $this->fmtPct($l['porcentaje'] ?? null) . ')';
        }

        $lineas[] = '━━━━━━━━━━━━━━━━━━━━━━━━━━━━';
        $lineas[] = '📈 *RENDIMIENTO POR FASE (vs META)*';
        $metas = $d['metas_min'] ?? [];
        $avgs = $d['tiempos_avg_min'] ?? [];
        foreach ([
            ['titulo' => 'Registro → Preparación', 'avg' => $avgs['registro_a_preparacion'] ?? null, 'meta' => $metas['registro_a_preparacion'] ?? self::META_PREP_MIN],
            ['titulo' => 'Preparación → Chequeo',  'avg' => $avgs['preparacion_a_chequeo'] ?? null,  'meta' => $metas['preparacion_a_chequeo'] ?? self::META_CHEQ_MIN],
            ['titulo' => 'Chequeo → Embalaje',     'avg' => $avgs['chequeo_a_embalaje'] ?? null,     'meta' => $metas['chequeo_a_embalaje'] ?? self::META_EMB_MIN],
            ['titulo' => 'Tiempo de Vida Total',   'avg' => $d['avg_vida_total'] ?? null,            'meta' => $metas['vida_total'] ?? self::META_VIDA_MIN],
        ] as $f) {
            if ($f['avg'] === null) {
                $lineas[] = '  • *' . $f['titulo'] . ':* AVG — / ' . $f['meta'] . 'm → ' . $this->barra(0) . ' sin datos';
                continue;
            }
            $eficiencia = min(100.0, round((float) $f['meta'] / max(0.01, (float) $f['avg']) * 100, 1));
            $lineas[] = '  • *' . $f['titulo'] . ':* AVG ' . $f['avg'] . 'm / meta ' . $f['meta'] . 'm → '
                . $this->barra($eficiencia) . ' ' . number_format($eficiencia, 0, ',', '.') . '%';
        }
        $lineas[] = '━━━━━━━━━━━━━━━━━━━━━━━━━━━━';

        return implode("\n", $lineas);
    }

    /** Barra de progreso ASCII de 10 celdas (█ = cumplimiento). */
    private function barra(float $eficiencia): string
    {
        $llenado = (int) round(max(0.0, min(100.0, $eficiencia)) / 10);
        return str_repeat('█', $llenado) . str_repeat('░', 10 - $llenado);
    }

    /** Promedio en minutos: número redondeado o "—". */
    private function fmtAvg(mixed $v): string
    {
        return $v !== null ? (string) $v : '—';
    }

    /** Porcentaje con 1 decimal o "—". */
    private function fmtPct(mixed $v): string
    {
        return $v !== null ? number_format((float) $v, 1, ',', '.') . '%' : '—';
    }

    // =========================================================================
    // UTILIDADES COMUNES
    // =========================================================================

    /**
     * Reúne el texto plano de cualquier forma de argumentos: array posicional,
     * claves de texto directo (text/raw_input/raw/query...), valores
     * string/númericos sueltos y arrays anidados de 1 nivel.
     */
    private static function reunirTexto(array $arguments): string
    {
        $partes = [];
        foreach ($arguments as $k => $v) {
            if (is_array($v)) {
                foreach ($v as $sub) {
                    if (is_string($sub) || is_numeric($sub)) {
                        $partes[] = trim((string) $sub);
                    }
                }
            } elseif (is_string($v) || is_numeric($v)) {
                $partes[] = trim((string) $v);
            } elseif (is_bool($v)) {
                continue;
            }
        }
        return implode(' ', array_values(array_filter($partes, static fn ($p) => $p !== '')));
    }

    /**
     * Normaliza un timestamp de BD a int (epoch) o null.
     */
    private static function fechaHito(string $valor): ?int
    {
        $valor = trim($valor);
        if ($valor === '' || $valor === '0000-00-00 00:00:00' || $valor === '0') {
            return null;
        }
        if (preg_match('/^\d{1,2}:\d{2}(:\d{2})?$/', $valor) === 1) {
            $valor = date('Y-m-d') . ' ' . $valor;
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

