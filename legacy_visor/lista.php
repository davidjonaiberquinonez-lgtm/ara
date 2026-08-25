<?php
/**
 * visor/lista.php — Notas de entrega / facturas de una RUTA MACRO (módulo REALIZAR RUTA).
 *
 * GET /visor/lista.php?ruta=CARACAS
 *
 * Devuelve JSON con los items de todas las sub-rutas cuyo nombre INICIA con la
 * palabra clave (ej: 'CARACAS' → 'CARACAS BAJA CHARALLAVE', 'CARACAS ALTA LA GUAIRA',
 * 'CARACAS ESTE HIGUEROTE'). Compatible PHP 7.0+ / 8.x.
 *
 * Campos por item:
 *   nota, codigo, descripcion, sub_ruta, factura, paquetes ('N/N'), creada,
 *   impresa ('SI'/'NO'), estado ('Procesada'/'S/P')
 */

declare(strict_types=1);

declare(ticks=1);

// ═══════════════════════════════════════════════════════════════════════
// CANDADO ANTI-ZOMBI (Fase 2.5): el script se suicida a los N segundos
// pase lo que pase. Default 300s; override con env ARA_CLI_MAX_S; tope 600s.
// Watchdog por tick: si excede 240s, fuerza desconexión y aborta (FASE25).
// ═══════════════════════════════════════════════════════════════════════
$t_maxS = (int) (getenv('ARA_CLI_MAX_S') ?: '300');
$t_maxS = max(5, min(600, $t_maxS));
set_time_limit($t_maxS);
ini_set('max_execution_time', (string) $t_maxS);
ini_set('memory_limit', '128M');

$t_inicio = microtime(true);
register_shutdown_function(static function (): void {
    gc_collect_cycles();
});
register_tick_function(static function () use ($t_inicio, $t_maxS): void {
    if ((microtime(true) - $t_inicio) > 240) {
        if (defined('STDERR')) {
            fwrite(STDERR, '{"success":false,"error":"FASE25_KILL: legacy_visor/lista.php excedió tiempo (' . $t_maxS . 's). Proceso abortado."}');
        }
        error_log('FASE25_KILL: script legacy_visor/lista.php excedió tiempo (' . $t_maxS . 's)');
        exit(1);
    }
});

error_reporting(E_ALL);
ini_set('display_errors', '0');
date_default_timezone_set('America/Caracas');

// HOTFIX v4.17.1 (fuga de conexiones): prohibir conexiones persistentes
// accidentales — cada request abre y CIERRA su propia conexión.
ini_set('mysql.allow_persistent', 'Off');
ini_set('mysqli.allow_persistent', 'Off');

header('Content-Type: application/json; charset=utf-8');

/* =====================================================================================
 * CONFIGURACIÓN — Ajuste aquí los nombres de tablas y columnas según su esquema
 * Profit Plus / Legacy. Si una columna no existe en la tabla, el script intenta
 * resolver los alias automáticamente (ver lista de candidatas por campo).
 * ===================================================================================== */

const DB_CONFIG = [
    // Driver preferido: PDO sqlsrv, luego PDO odbc, luego PDO mssql (fallback automático).
    'server'   => '192.168.4.148',          // host de SQL Server / Profit Plus
    'database' => 'ProfitPlus',             // nombre de la base de datos
    'user'     => 'sa',                     // usuario SQL
    'pass'     => 'SuClaveSQL_Aqui',        // ← CONFIGURE SU CLAVE
];

const TABLA_NOTAS     = 'rep_not';          // tabla principal de notas de entrega
const TABLA_PAQUETES  = 'despachos';        // tabla de paquetes/cajas por nota (puede no existir)

// Columnas candidatas (se resuelven en orden de prioridad contra INFORMATION_SCHEMA)
const COLS = [
    'nota'      => ['not_num', 'nota', 'numero_nota', 'nro_nota', 'codigobarra'],
    'factura'   => ['fact_num', 'factura', 'num_fact', 'nro_factura'],
    'codigo'    => ['co_cli', 'codigo', 'cod_cliente', 'cliente_id'],
    'descrip'   => ['cli_des', 'descripcion', 'razon_social', 'nombre_cliente', 'cliente', 'nombre'],
    'sub_ruta'  => ['sub_ruta', 'subruta', 'ruta', 'nombre_subruta'],
    'fecha'     => ['fecha', 'fec_em', 'fecha_emision', 'creada', 'fecha_creacion'],
    'impresa'   => ['impresa', 'impreso'],
    'estado'    => ['estado', 'status', 'statu', 'estatus'],
];

// Estados considerados PROCESADOS (se excluyen de la consulta "pendientes").
// Si la columna de estado no existe o no coincide, no se filtra.
const ESTADOS_PROCESADOS = ['P', 'PROCESADA', 'DESPACHADA', 'ENTREGADA', 'C', 'X'];

// Sub-consulta de paquetes: SELECT COUNT(*) FROM despachos WHERE <col_nota> = <nota>
const TABLA_PAQUETES_COL_NOTA = 'nota';     // columna en TABLA_PAQUETES que referencia la nota
const PAQUETES_FALLBACK = '1/1';            // si TABLA_PAQUETES no existe

/* =====================================================================================
 * UTILIDADES
 * ===================================================================================== */

/** Conexión PDO con fallback de drivers: sqlsrv → odbc → mssql. */
function db_conectar(): PDO
{
    $c = DB_CONFIG;
    $dsns = [];
    try {
        $dsns['sqlsrv'] = "sqlsrv:Server={$c['server']};Database={$c['database']}";
    } catch (Throwable $e) { /* driver no disponible */ }
    try {
        $dsns['odbc'] = "odbc:Driver={SQL Server};Server={$c['server']};Database={$c['database']}";
    } catch (Throwable $e) { /* driver no disponible */ }
    $dsns['mssql'] = "mssql:host={$c['server']};dbname={$c['database']}";

    $errores = [];
    foreach ($dsns as $nombre => $dsn) {
        if (!in_array($nombre, PDO::getAvailableDrivers(), true)) {
            $errores[] = "$nombre no instalado";
            continue;
        }
        try {
            $pdo = new PDO($dsn, $c['user'], $c['pass'], [
                PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
                PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
                // HOTFIX v4.17.1: NUNCA conexiones persistentes (sesiones dormidas).
                PDO::ATTR_PERSISTENT         => false,
                PDO::ATTR_STRINGIFY_FETCHES  => false,
            ]);
            return $pdo;
        } catch (Throwable $e) {
            $errores[] = $nombre . ': ' . $e->getMessage();
        }
    }
    throw new RuntimeException('No se pudo conectar a SQL Server. Drivers disponibles: ' .
        implode(', ', PDO::getAvailableDrivers()) . ' | Detalles: ' . implode(' | ', $errores));
}

/** Devuelve las columnas reales de una tabla (minúsculas) o [] si no existe. */
function columnas_tabla(PDO $pdo, string $tabla): array
{
    try {
        $dbname = $pdo->query('SELECT DB_NAME() AS db')->fetchColumn();
        $sql = $dbname
            ? "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = ? AND TABLE_SCHEMA = 'dbo'"
            : "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = ?";
        $stmt = $pdo->prepare($sql);
        $stmt->execute([$tabla]);
        return array_map('strtolower', $stmt->fetchAll(PDO::FETCH_COLUMN));
    } catch (Throwable $e) {
        return [];
    }
}

/** Resuelve la primera columna candidata que exista en la tabla. */
function resolver_columna(array $cols_tabla, array $candidatas, ?string $default = null): ?string
{
    foreach ($candidatas as $c) {
        if (in_array(strtolower((string)$c), $cols_tabla, true)) {
            return $c;
        }
    }
    return $default;
}

/** Escapa un literal para evitar inyección en identificadores (nombres de columna resueltos). */
function ident_ok(string $ident): bool
{
    return (bool)preg_match('/^[A-Za-z_][A-Za-z0-9_]*$/', $ident);
}

function responder(int $http, array $payload): void
{
    http_response_code($http);
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

/* =====================================================================================
 * FLUJO PRINCIPAL
 * ===================================================================================== */

try {
    $pdo = null;   // HOTFIX v4.17.1: se cierra en el finally SIEMPRE.

    $ruta = strtoupper(trim((string)($_GET['ruta'] ?? '')));
    if ($ruta === '') {
        responder(400, ['status' => 'error', 'mensaje' => 'Falta el parámetro ruta (ej: ?ruta=CARACAS).']);
    }
    if (!preg_match('/^[A-Z0-9ÁÉÍÓÚÑ \-\.]+$/', $ruta)) {
        responder(400, ['status' => 'error', 'mensaje' => 'Parámetro ruta inválido.']);
    }

    $pdo = db_conectar();

    $cols_notas  = columnas_tabla($pdo, TABLA_NOTAS);
    $col_nota    = resolver_columna($cols_notas, COLS['nota'], 'not_num');
    $col_factura = resolver_columna($cols_notas, COLS['factura'], 'fact_num');
    $col_codigo  = resolver_columna($cols_notas, COLS['codigo'], 'co_cli');
    $col_desc    = resolver_columna($cols_notas, COLS['descrip'], 'cli_des');
    $col_sub     = resolver_columna($cols_notas, COLS['sub_ruta'], 'sub_ruta');
    $col_fecha   = resolver_columna($cols_notas, COLS['fecha'], 'fecha');
    $col_impresa = resolver_columna($cols_notas, COLS['impresa'], 'impresa');
    $col_estado  = resolver_columna($cols_notas, COLS['estado'], 'estado');

    $select_cols = [];
    foreach ([
        'nota' => $col_nota, 'factura' => $col_factura, 'codigo' => $col_codigo,
        'descrip' => $col_desc, 'sub_ruta' => $col_sub, 'fecha' => $col_fecha,
        'impresa' => $col_impresa, 'estado' => $col_estado,
    ] as $alias => $col) {
        if ($col !== null) {
            $select_cols[] = '[' . $col . '] AS ' . $alias;
        }
    }

    if (count($select_cols) === 0 || $col_sub === null) {
        responder(500, [
            'status' => 'error',
            'mensaje' => 'La tabla ' . TABLA_NOTAS . ' no tiene columnas reconocibles. ' .
                         'Revise COLS en la configuración de lista.php.',
        ]);
    }

    $where_sub = 'UPPER([' . $col_sub . ']) LIKE UPPER(?)';

    // Paquetes por nota (N/N): COUNT en la tabla de paquetes/cajas, si existe
    $cols_paq = columnas_tabla($pdo, TABLA_PAQUETES);
    $paq_col_nota = resolver_columna($cols_paq, [TABLA_PAQUETES_COL_NOTA, 'not_num', 'nota_id'], 'nota');
    $usar_paquete = ($col_nota !== null && $paq_col_nota !== null && count($cols_paq) > 0);

    $sql = 'SELECT ' . implode(', ', $select_cols) . ' FROM [' . TABLA_NOTAS . ']';
    $params = ['%' . $ruta . '%'];

    if ($col_estado !== null) {
        $sql .= ' WHERE ' . $where_sub . ' AND UPPER(CAST([' . $col_estado . '] AS NVARCHAR(20))) NOT IN (';
        $sql .= implode(',', array_fill(0, count(ESTADOS_PROCESADOS), '?'));
        $sql .= ')';
        $params = array_merge($params, ESTADOS_PROCESADOS);
    } else {
        $sql .= ' WHERE ' . $where_sub;
    }

    $sql .= ' ORDER BY [' . $col_sub . '], [' . ($col_fecha ?? $col_nota) . ']';

    $stmt = $pdo->prepare($sql);
    $stmt->execute($params);
    $filas = $stmt->fetchAll();

    $items = [];
    $paq_cache = [];

    foreach ($filas as $fila) {
        $nota = (string)($fila['nota'] ?? '');
        if ($nota === '') {
            continue;
        }

        if ($usar_paquete && !array_key_exists($nota, $paq_cache)) {
            try {
                $stmt_paq = $pdo->prepare(
                    'SELECT COUNT(*) AS n FROM [' . TABLA_PAQUETES . '] WHERE [' . $paq_col_nota . '] = ?'
                );
                $stmt_paq->execute([$nota]);
                $paq_cache[$nota] = (int)$stmt_paq->fetchColumn();
            } catch (Throwable $e) {
                $paq_cache[$nota] = 0;
            }
        }
        $n_paq = $usar_paquete ? max((int)($paq_cache[$nota] ?? 0), 1) : null;

        $impresa_raw = strtoupper((string)($fila['impresa'] ?? ''));
        $impresa = in_array($impresa_raw, ['S', '1', 'SI', 'TRUE', 'Y'], true) ? 'SI' : 'NO';

        $estado_raw = strtoupper((string)($fila['estado'] ?? ''));
        $estado = in_array($estado_raw, ESTADOS_PROCESADOS, true) ? 'Procesada' : 'S/P';

        $fecha_raw = (string)($fila['fecha'] ?? '');
        $fecha = $fecha_raw !== '' ? date('Y-m-d H:i', strtotime(str_replace('/', '-', $fecha_raw))) : '';

        $items[] = [
            'nota'       => $nota,
            'codigo'     => (string)($fila['codigo'] ?? ''),
            'descripcion'=> (string)($fila['descrip'] ?? ''),
            'sub_ruta'   => (string)($fila['sub_ruta'] ?? ''),
            'factura'    => (string)($fila['factura'] ?? ''),
            'paquetes'   => $n_paq !== null ? $n_paq . '/' . $n_paq : PAQUETES_FALLBACK,
            'creada'     => $fecha,
            'impresa'    => $impresa,
            'estado'     => $estado,
        ];
    }

    responder(200, [
        'status' => 'success',
        'ruta'   => $ruta,
        'total'  => count($items),
        'items'  => $items,
    ]);

} catch (Throwable $e) {
    responder(500, [
        'status'  => 'error',
        'mensaje' => 'Error en visor/lista.php: ' . $e->getMessage(),
    ]);
} finally {
    // HOTFIX v4.17.1 (fuga de conexiones): cierre EXPLÍCITO de la conexión en
    // TODOS los caminos (éxito, error, excepción) — no dejar sesiones dormidas.
    if (isset($pdo) && $pdo instanceof PDO) {
        $pdo = null;
    }
    gc_collect_cycles();
}
