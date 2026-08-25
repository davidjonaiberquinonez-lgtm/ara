<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Almacen;

use App\Services\ConnectionWrapper;
use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;
use PDO;
use PDOException;

/**
 * Detección preventiva de fallas de origen en las notas en cola, ANTES de que
 * el preparador las tome físicamente.
 *
 * Reglas de negocio:
 *  1. RENGLON_DUPLICADO: el mismo co_art repetido en 2+ renglones de la misma
 *     nota.
 *  2. CANTIDAD_CERO / CANTIDAD_NEGATIVA: renglones con cant_sol == 0 o < 0.
 *  3. CODIGO_INCOMPATIBLE: artículos sin equivalente en el catálogo
 *     (tabla articulos/productos del MySQL legacy, resuelta dinámicamente).
 *
 * La skill NUNCA autocorrige la nota en BD: emite la alerta preventiva
 * estandarizada "ALERTA PREVENTIVA: Nota #X contiene un renglón duplicado
 * para el SKU Y. Notificar a Administración antes de surtir."
 *
 * Fuentes: reng_nde (PRUEB25) para renglones; rep_not (MySQL legacy) para
 * el alcance de notas activas; articulos (MySQL legacy) para el catálogo.
 * Sin catálogo disponible, el chequeo 3 se omite con aviso honesto.
 * Nunca escribe; nunca lanza excepción por datos ausentes.
 */
final class DetectorErroresNotaTool implements AgentToolInterface
{
    use AlmacenDbTrait;

    private const ESTATUS_ACTIVOS = [
        'PENDIENTE', 'EN_PREPARACION', 'EN_CHEQUEO', 'EN_PROCESO',
        'EN_EMBALAJE', 'ACTIVA', 'CARGADA',
    ];

    public function getName(): string
    {
        return 'detector_errores_nota';
    }

    public function getDescription(): string
    {
        return 'Audita notas en cola para detectar fallas de origen antes de '
             . 'surtir: renglones duplicados del mismo SKU, cantidades en cero '
             . 'o negativas, y artículos descatalogados. Emite alertas '
             . 'preventivas (NUNCA autocorrige la BD). Parámetros: num_nota '
             . '(opcional; sin él audita todas las notas activas) y limite '
             . '(default 100).';
    }

    public function getParameters(): array
    {
        return [
            'type'       => 'object',
            'properties' => [
                'num_nota' => [
                    'type'        => 'string',
                    'description' => 'Número de la nota a auditar. Si se omite, se auditan todas las notas activas en cola.',
                ],
                'limite' => [
                    'type'        => 'integer',
                    'description' => 'Máximo de notas a auditar (default 100).',
                ],
            ],
            'required' => [],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $t0 = self::micro();
        $numNota = trim((string) ($arguments['num_nota'] ?? ''));
        $limite = max(1, min(500, (int) ($arguments['limite'] ?? 100)));
        $advertencias = [];

        // ── 0) Métricas consolidadas del día (AD3 / v4.17) ──────────────────
        $metricas = $this->metricasDelDia($advertencias);

        // ── 1) Renglones en alcance (reng_nde, PRUEB25) — conexión protegida ──
        $wrapper = new ConnectionWrapper();
        $tablaReng = $wrapper->resolverTabla(['reng_nde', 'reng_ndd']);
        if ($tablaReng === null) {
            try {
                $wrapper->queryProfit('SELECT 1');
            } catch (PDOException $e) {
                return ['success' => false, 'error' => 'Profit PRUEB25 no disponible: ' . self::aUtf8($e->getMessage())];
            }
            return ['success' => false, 'error' => 'No existe reng_nde/reng_ndd en PRUEB25.'];
        }
        $cols = $wrapper->resolverColumnas($tablaReng, [
            'num'  => ['num_doc', 'fact_num'],
            'reng' => ['reng_num', 'num_reng'],
            'art'  => ['co_art', 'cod_art'],
            'sol'  => ['total_art', 'cant_sol', 'cant_solicitada'],
            'anul' => ['anulado'],
        ]);
        if ($cols['num'] === null || $cols['art'] === null) {
            return ['success' => false, 'error' => 'reng_nde no expone columna de documento o de artículo.'];
        }

        $documentos = [];
        if ($numNota !== '') {
            $documentos = [$numNota];
        } else {
            $documentos = $this->notasActivas($advertencias, $limite);
            if ($documentos === [] && $advertencias === []) {
                return ['success' => true, 'data' => [
                    'total_analizadas' => 0,
                    'en_conflicto'     => [],
                    'mensajes_alerta'  => [],
                    'advertencias'     => [],
                    'metricas_del_dia' => $metricas,
                    'metric_us'        => (int) ((self::micro() - $t0) * 1_000_000),
                ], 'card' => CardBuilder::iniciar('✅', 'NOTAS EN COLA SIN ERRORES')
                    ->linea('No hay notas activas que auditar en este momento.')
                    ->footer('Fuente: MySQL rep_not + Profit PRUEB25 (NOLOCK)')
                    ->tarjeta()];
            }
        }

        $renglones = [];
        foreach (array_chunk($documentos, 200) as $chunk) {
            $marks = implode(',', array_fill(0, count($chunk), '?'));
            $sql = 'SELECT ' . $wrapper->qPara($cols['num']) . ' AS num'
                 . ($cols['reng'] !== null ? ', ' . $wrapper->qPara($cols['reng']) . ' AS reng' : '')
                 . ', ' . $wrapper->qPara($cols['art']) . ' AS art'
                 . ($cols['sol'] !== null ? ', ' . $wrapper->qPara($cols['sol']) . ' AS sol' : '')
                 . ($cols['anul'] !== null ? ', ' . $wrapper->qPara($cols['anul']) . ' AS anul' : '')
                 . ' FROM ' . $wrapper->qPara($tablaReng) . ' WITH (NOLOCK)'
                 . ' WHERE ' . $wrapper->qPara($cols['num']) . ' IN (' . $marks . ')'
                 . ' ORDER BY ' . $wrapper->qPara($cols['num']) . ', ' . $wrapper->qPara($cols['reng']);
            try {
                $filas = $wrapper->querySafe($sql, $chunk);
                $renglones = array_merge($renglones, $filas);
            } catch (PDOException $e) {
                $advertencias[] = 'reng_nde no consultable: ' . self::aUtf8($e->getMessage());
                break;
            }
        }

        // ── 2) Catálogo de artículos (MySQL legacy, best-effort) ────────────
        $catalogo = $this->catalogoArticulos($renglones, $advertencias);

        // ── 3) Detección (lógica pura, testeable) ───────────────────────────
        $hallazgos = self::detectarErroresRenglones($renglones, $catalogo);

        // ── 4) Empaquetado de salida ────────────────────────────────────────
        $enConflicto = [];
        $mensajes = [];
        $porNota = [];
        foreach ($hallazgos as $h) {
            $porNota[(string) $h['num_nota']][] = $h;
        }
        foreach ($porNota as $nota => $errores) {
            $enConflicto[] = [
                'num_nota' => $nota,
                'errores'  => $errores,
            ];
            foreach ($errores as $e) {
                $mensajes[] = 'ALERTA PREVENTIVA: Nota #' . $nota . ' contiene un '
                    . $e['codigo_error'] . ' para el SKU ' . $e['co_art']
                    . '. Notificar a Administración antes de surtir.';
            }
        }

        $card = $this->tarjetaDetector($documentos, $enConflicto, $mensajes, $advertencias, (int) ((self::micro() - $t0) * 1_000_000));

        return [
            'success' => true,
            'data'    => [
                'total_analizadas' => count($documentos),
                'en_conflicto'     => $enConflicto,
                'mensajes_alerta'  => $mensajes,
                'advertencias'     => $advertencias,
                'metricas_del_dia' => $metricas,
                'metric_us'        => (int) ((self::micro() - $t0) * 1_000_000),
            ],
            'card'    => $card,
        ];
    }

    /** Tarjeta estándar v4.13 del detector de errores (CardBuilder). */
    private function tarjetaDetector(array $documentos, array $enConflicto, array $mensajes, array $advertencias, int $us): string
    {
        $card = CardBuilder::iniciar('🛡️', 'DETECTOR DE ERRORES DE NOTA')
            ->campo('NOTAS ANALIZADAS', count($documentos), 'numero')
            ->campo('NOTAS CON CONFLICTO', count($enConflicto), 'numero');
        if ($mensajes === []) {
            $card->linea('✅ Sin errores de origen detectados en el alcance.');
        } else {
            $card->seccion('Alertas preventivas (no autocorregir)');
            foreach (array_slice($mensajes, 0, 6) as $m) {
                $card->linea('🚨 ' . $m);
            }
            $ocultos = count($mensajes) - 6;
            if ($ocultos > 0) {
                $card->linea('⏩ +' . $ocultos . ' alerta(s) más en la consulta.');
            }
        }
        foreach (array_slice($advertencias, 0, 2) as $aviso) {
            $card->linea('⚠️ ' . $aviso);
        }
        return $card
            ->footer('Fuente: Profit PRUEB25 (NOLOCK) + MySQL rep_not · ' . round($us / 1000) . ' ms')
            ->tarjeta();
    }

    /**
     * DETECCIÓN PURA (sin I/O): aplica las 3 reglas sobre renglones crudos.
     *
     * @param array<int,array<string,mixed>> $renglones Filas {num, reng, art, sol, anul}
     * @param array<string,true>|null        $catalogo  Mapa co_art => true (null = chequeo 3 omitido)
     *
     * @return array<int,array<string,mixed>> Errores {num_nota, codigo_error, reng_num, co_art, mensaje}
     */
    public static function detectarErroresRenglones(array $renglones, ?array $catalogo): array
    {
        $errores = [];

        // Agrupar por nota.
        $porNota = [];
        foreach ($renglones as $r) {
            $nota = (string) ($r['num'] ?? '');
            if ($nota === '') {
                continue;
            }
            $porNota[$nota][] = [
                'reng' => (int) ($r['reng'] ?? 0),
                'art'  => strtoupper(trim((string) ($r['art'] ?? ''))),
                'sol'  => (float) ($r['sol'] ?? 0),
                'anul' => (string) ($r['anul'] ?? ''),
            ];
        }

        foreach ($porNota as $nota => $reng) {
            // Regla 1: renglón duplicado (mismo SKU en 2+ renglones, no anulado).
            $porArt = [];
            foreach ($reng as $r) {
                if ($r['anul'] !== '' && $r['anul'] !== '0' && $r['anul'] !== 'N') {
                    continue; // renglón anulado no genera alerta
                }
                $porArt[$r['art']][] = $r;
            }
            foreach ($porArt as $art => $lista) {
                if ($art !== '' && count($lista) > 1) {
                    $errores[] = [
                        'num_nota'    => $nota,
                        'codigo_error'=> 'RENGLON_DUPLICADO',
                        'reng_num'    => array_column($lista, 'reng'),
                        'co_art'      => $art,
                        'mensaje'     => 'Nota #' . $nota . ' tiene el SKU ' . $art
                            . ' repetido en ' . count($lista) . ' renglones ('
                            . implode(', ', array_column($lista, 'reng')) . ').',
                    ];
                }
            }

            // Regla 2: cantidades en cero o negativas.
            foreach ($reng as $r) {
                if ($r['anul'] !== '' && $r['anul'] !== '0' && $r['anul'] !== 'N') {
                    continue;
                }
                if ($r['sol'] == 0) {
                    $errores[] = [
                        'num_nota'    => $nota,
                        'codigo_error'=> 'CANTIDAD_CERO',
                        'reng_num'    => $r['reng'],
                        'co_art'      => $r['art'],
                        'mensaje'     => 'Nota #' . $nota . ' renglón ' . $r['reng']
                            . ' (SKU ' . $r['art'] . ') tiene cantidad solicitada 0.',
                    ];
                } elseif ($r['sol'] < 0) {
                    $errores[] = [
                        'num_nota'    => $nota,
                        'codigo_error'=> 'CANTIDAD_NEGATIVA',
                        'reng_num'    => $r['reng'],
                        'co_art'      => $r['art'],
                        'mensaje'     => 'Nota #' . $nota . ' renglón ' . $r['reng']
                            . ' (SKU ' . $r['art'] . ') tiene cantidad solicitada negativa (' . $r['sol'] . ').',
                    ];
                }
            }

            // Regla 3: código sin equivalente en catálogo (si el catálogo está).
            if ($catalogo !== null) {
                foreach ($reng as $r) {
                    if ($r['anul'] !== '' && $r['anul'] !== '0' && $r['anul'] !== 'N') {
                        continue;
                    }
                    if ($r['art'] !== '' && !isset($catalogo[$r['art']])) {
                        $errores[] = [
                            'num_nota'    => $nota,
                            'codigo_error'=> 'CODIGO_INCOMPATIBLE',
                            'reng_num'    => $r['reng'],
                            'co_art'      => $r['art'],
                            'mensaje'     => 'Nota #' . $nota . ' renglón ' . $r['reng']
                                . ' referencia el SKU ' . $r['art'] . ' sin equivalente en el catálogo de artículos.',
                        ];
                    }
                }
            }
        }

        // Orden estable: nota → tipo de error.
        usort($errores, static fn (array $a, array $b): int =>
            [$a['num_nota'], $a['codigo_error'], (int) (is_array($a['reng_num']) ? ($a['reng_num'][0] ?? 0) : $a['reng_num'])]
            <=>
            [$b['num_nota'], $b['codigo_error'], (int) (is_array($b['reng_num']) ? ($b['reng_num'][0] ?? 0) : $b['reng_num'])]
        );
        return $errores;
    }

    /**
     * Métricas consolidadas del día actual (AD3 / v4.17):
     *  - total_notas_hasta_momento: notas del día en rep_not (fecha = hoy).
     *  - errores_duplicados: mismo doc_num (y co_cli si existe) repetido.
     *  - errores_impresion: notas Procesadas sin registro en app_log_impresiones.
     *  - ultima_nota_procesada y timestamp_escaneo.
     *
     * @param array<int,string> $advertencias (por referencia)
     *
     * @return array<string,mixed>
     */
    private function metricasDelDia(array &$advertencias): array
    {
        $base = [
            'total_notas_hasta_momento' => 0,
            'errores_duplicados'        => 0,
            'errores_impresion'         => 0,
            'estado_escaneo'            => 'OK',
            'ultima_nota_procesada'     => null,
            'timestamp_escaneo'         => date('c'),
            'duplicados'                => [],
            'pendientes_impresion'      => [],
        ];
        try {
            $pdo = $this->conectarMySQL();
        } catch (PDOException $e) {
            $advertencias[] = 'MySQL legacy no disponible para métricas del día: ' . self::aUtf8($e->getMessage());
            $base['estado_escaneo'] = 'PARCIAL';
            return $base;
        }
        $tabla = $this->resolverTabla($pdo, ['rep_not', 'notas', 'nota']);
        if ($tabla === null) {
            $pdo = null;
            $advertencias[] = 'Sin cabecera de notas en MySQL para métricas del día.';
            $base['estado_escaneo'] = 'PARCIAL';
            return $base;
        }
        $cols = $this->resolverColumnas($pdo, $tabla, [
            'cod'    => ['cod_nota', 'num_nota', 'cd_barr'],
            'fecha'  => ['fec_creacion', 'fecha', 'fec_emis', 'fec_impr', 'fec_profit'],
            'cliente'=> ['co_cli', 'cliente', 'cli_id'],
            'estado' => ['estatus', 'estado', 'status'],
        ]);
        if ($cols['cod'] === null || $cols['fecha'] === null) {
            $pdo = null;
            $advertencias[] = 'La cabecera de notas no expone código o fecha para métricas del día.';
            $base['estado_escaneo'] = 'PARCIAL';
            return $base;
        }

        // a) Conteo total del día + última nota procesada + duplicados.
        $selFecha = self::q($cols['fecha']);
        try {
            $stmt = $pdo->prepare(
                'SELECT ' . self::q($cols['cod']) . ' AS cod'
                . ($cols['cliente'] !== null ? ', ' . self::q($cols['cliente']) . ' AS cliente' : '')
                . ', ' . $selFecha . ' AS fecha'
                . ($cols['estado'] !== null ? ', ' . self::q($cols['estado']) . ' AS estado' : '')
                . ' FROM ' . self::q($tabla)
                . ' WHERE CAST(' . $selFecha . ' AS DATE) = CURDATE()'
                . ' ORDER BY ' . $selFecha . ' DESC LIMIT 2000'
            );
            $stmt->execute();
            $notas = $stmt->fetchAll(PDO::FETCH_ASSOC);
            $base['total_notas_hasta_momento'] = count($notas);
        } catch (PDOException $e) {
            $pdo = null;
            $advertencias[] = 'Conteo del día no consultable: ' . self::aUtf8($e->getMessage());
            $base['estado_escaneo'] = 'PARCIAL';
            return $base;
        }

        // Duplicados: mismo cod (y cliente si existe) más de una vez en el día.
        $porClave = [];
        foreach ($notas as $n) {
            $clave = (string) ($n['cod'] ?? '')
                . ($cols['cliente'] !== null ? '|' . (string) ($n['cliente'] ?? '') : '');
            if ($clave === '') {
                continue;
            }
            $porClave[$clave][] = $n;
        }
        $duplicados = [];
        foreach ($porClave as $clave => $grupo) {
            if (count($grupo) > 1) {
                $partes = explode('|', $clave, 2);
                $duplicados[] = [
                    'doc_num' => $partes[0],
                    'co_cli'  => $cols['cliente'] !== null ? ($partes[1] ?? '') : null,
                    'veces'   => count($grupo),
                ];
            }
        }
        $base['duplicados'] = $duplicados;
        $base['errores_duplicados'] = count($duplicados);

        // Última nota procesada del día (la más reciente con estado procesado,
        // fallback a la más reciente del día).
        if ($cols['estado'] !== null && $notas !== []) {
            $procesadas = array_values(array_filter($notas, static fn (array $n): bool =>
                str_contains(strtoupper((string) ($n['estado'] ?? '')), 'PROCES')
            ));
            $base['ultima_nota_procesada'] = ($procesadas[0]['cod'] ?? $notas[0]['cod'] ?? null);
        } elseif ($notas !== []) {
            $base['ultima_nota_procesada'] = $notas[0]['cod'] ?? null;
        }

        // b) Errores de impresión: notas del día en estado Procesado sin log.
        $pendientes = $this->pendientesImpresion($pdo, $tabla, $cols, $advertencias);
        $base['pendientes_impresion'] = $pendientes;
        $base['errores_impresion'] = count($pendientes);
        $pdo = null;
        return $base;
    }

    /**
     * Notas del día con estado Procesado que NO tienen registro de impresión
     * en app_log_impresiones (tabla local creada best-effort).
     *
     * @return array<int,array<string,mixed>>
     */
    private function pendientesImpresion(PDO $pdo, string $tabla, array $cols, array &$advertencias): array
    {
        if ($cols['estado'] === null) {
            return [];
        }
        // Garantizar la tabla local de impresiones (best-effort).
        try {
            $pdo->exec(
                'CREATE TABLE IF NOT EXISTS `app_log_impresiones` ('
                . '`id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, `nota_id` VARCHAR(40) NOT NULL,'
                . '`usuario` VARCHAR(80) NOT NULL DEFAULT "", `fecha_impresion` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,'
                . 'KEY `idx_ali_nota` (`nota_id`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'
            );
        } catch (PDOException $e) {
            $advertencias[] = 'No se pudo crear app_log_impresiones: ' . self::aUtf8($e->getMessage());
            return [];
        }
        $selFecha = self::q($cols['fecha']);
        $selEstado = self::q($cols['estado']);
        try {
            $stmt = $pdo->prepare(
                'SELECT DISTINCT ' . self::q($cols['cod']) . ' AS cod'
                . ' FROM ' . self::q($tabla) . ' n'
                . ' WHERE CAST(' . $selFecha . ' AS DATE) = CURDATE()'
                . ' AND UPPER(' . $selEstado . ') LIKE "%PROCES%"'
                . ' AND NOT EXISTS (SELECT 1 FROM `app_log_impresiones` l WHERE l.`nota_id` = n.' . self::q($cols['cod']) . ')'
                . ' LIMIT 500'
            );
            $stmt->execute();
            $pendientes = [];
            foreach ($stmt->fetchAll(PDO::FETCH_COLUMN) as $cod) {
                $pendientes[] = ['doc_num' => self::aUtf8((string) $cod)];
            }
            return $pendientes;
        } catch (PDOException $e) {
            $advertencias[] = 'Errores de impresión no consultables: ' . self::aUtf8($e->getMessage());
            return [];
        }
    }

    /**
     * Notas activas (estatus de cola) desde rep_not, best-effort.
     *
     * @param array<int,string> $advertencias (por referencia)
     *
     * @return array<int,string>
     */
    private function notasActivas(array &$advertencias, int $limite): array
    {
        try {
            $pdo = $this->conectarMySQL();
        } catch (PDOException $e) {
            $advertencias[] = 'MySQL legacy no disponible (' . self::aUtf8($e->getMessage())
                            . '); sin alcance de notas activas.';
            return [];
        }
        $tabla = $this->resolverTabla($pdo, ['rep_not', 'notas', 'nota']);
        if ($tabla === null) {
            $pdo = null;
            $advertencias[] = 'Sin cabecera de notas en MySQL para el alcance de notas activas.';
            return [];
        }
        $cols = $this->resolverColumnas($pdo, $tabla, [
            'cod'    => ['cod_nota', 'num_nota', 'cd_barr'],
            'estado' => ['estatus', 'estado', 'status'],
        ]);
        if ($cols['cod'] === null || $cols['estado'] === null) {
            $pdo = null;
            $advertencias[] = 'La cabecera de notas no expone estatus para el alcance activo.';
            return [];
        }
        $marks = implode(',', array_fill(0, count(self::ESTATUS_ACTIVOS), '?'));
        try {
            $stmt = $pdo->prepare(
                'SELECT ' . self::q($cols['cod']) . ' FROM ' . self::q($tabla)
                . ' WHERE ' . self::q($cols['estado']) . ' IN (' . $marks . ')'
                . ' ORDER BY ' . self::q($cols['cod']) . ' LIMIT ' . $limite
            );
            $stmt->execute(self::ESTATUS_ACTIVOS);
            $codigos = array_map('strval', $stmt->fetchAll(PDO::FETCH_COLUMN));
            $pdo = null;
            return $codigos;
        } catch (PDOException $e) {
            $pdo = null;
            $advertencias[] = 'Notas activas no consultables: ' . self::aUtf8($e->getMessage());
            return [];
        }
    }

    /**
     * Catálogo de artículos (existe/n o no el SKU). Best-effort: null si el
     * catálogo no está disponible (el chequeo 3 se omite con aviso).
     *
     * @param array<int,array<string,mixed>> $renglones
     * @param array<int,string>              $advertencias (por referencia)
     *
     * @return array<string,true>|null
     */
    private function catalogoArticulos(array $renglones, array &$advertencias): ?array
    {
        $codigos = [];
        foreach ($renglones as $r) {
            $art = strtoupper(trim((string) ($r['art'] ?? '')));
            if ($art !== '') {
                $codigos[$art] = true;
            }
        }
        if ($codigos === []) {
            return [];
        }
        try {
            $pdo = $this->conectarMySQL();
        } catch (PDOException $e) {
            $advertencias[] = 'Catálogo no disponible (MySQL legacy): ' . self::aUtf8($e->getMessage())
                            . ' — chequeo de descatalogados omitido.';
            return null;
        }
        $tabla = $this->resolverTabla($pdo, ['articulos', 'art', 'inventario', 'productos']);
        if ($tabla === null) {
            $pdo = null;
            $advertencias[] = 'Sin tabla de artículos en MySQL (candidatas: articulos, art, inventario, productos)'
                            . ' — chequeo de descatalogados omitido.';
            return null;
        }
        $colArt = $this->resolverColumna($pdo, $tabla, ['co_art', 'cod_art', 'codigo', 'articulo', 'art']);
        if ($colArt === null) {
            $pdo = null;
            $advertencias[] = 'La tabla de artículos no expone columna de código — chequeo de descatalogados omitido.';
            return null;
        }
        $existentes = [];
        foreach (array_chunk(array_keys($codigos), 200) as $chunk) {
            $marks = implode(',', array_fill(0, count($chunk), '?'));
            try {
                $stmt = $pdo->prepare(
                    'SELECT ' . self::q($colArt) . ' FROM ' . self::q($tabla)
                    . ' WHERE ' . self::q($colArt) . ' IN (' . $marks . ')'
                );
                $stmt->execute($chunk);
                foreach ($stmt->fetchAll(PDO::FETCH_COLUMN) as $co) {
                    $existentes[strtoupper(trim((string) $co))] = true;
                }
            } catch (PDOException $e) {
                $pdo = null;
                $advertencias[] = 'Catálogo no consultable: ' . self::aUtf8($e->getMessage())
                                . ' — chequeo de descatalogados omitido.';
                return null;
            }
        }
        $pdo = null;
        return $existentes;
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
