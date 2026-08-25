<?php
/**
 * visor/registro.php — Registro de cierre de despacho y confirmación de escaneo
 *                       (módulo REALIZAR RUTA + escaneo 1:1).
 *
 * POST /visor/registro.php   (application/x-www-form-urlencoded o JSON)
 *
 * Parámetros aceptados:
 *   nota / notar / not_num  → N° de nota o factura a marcar como procesada
 *   guia                    → N° de guía de despacho (sub-ruta)
 *   sub_ruta                → nombre de la sub-ruta
 *   ayudantes               → IDs separados por punto, ej. "22.15"
 *   chofer                  → nombre del conductor
 *   carro / credenciales    → placa / datos del vehículo
 *   codigo + tipo           → confirmación de escaneo simple (caja|factura) sin nota
 *
 * Retorna: {"status":"success","message":"Despacho registrado correctamente"}
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
            fwrite(STDERR, '{"success":false,"error":"FASE25_KILL: legacy_visor/registro.php excedió tiempo (' . $t_maxS . 's). Proceso abortado."}');
        }
        error_log('FASE25_KILL: script legacy_visor/registro.php excedió tiempo (' . $t_maxS . 's)');
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
 * Profit Plus / Legacy. La actualización de estado es OPCIONAL en estructura:
 * si la tabla/columna configurada no existe, se responde success con una advertencia.
 * ===================================================================================== */

const DB_CONFIG = [
    'server'   => '192.168.4.148',          // host de SQL Server / Profit Plus
    'database' => 'ProfitPlus',             // nombre de la base de datos
    'user'     => 'sa',                     // usuario SQL
    'pass'     => 'SuClaveSQL_Aqui',        // ← CONFIGURE SU CLAVE
];

const TABLA_NOTAS        = 'rep_not';       // tabla donde se actualiza el estado de la nota
const VALOR_PROCESADO    = 'PROCESADA';     // valor que se escribe en la columna de estado
const TABLA_LOG_DESPACHO = 'despachos';     // tabla de bitácora de despachos (puede no existir)
const AUTO_CREAR_LOG     = false;           // true = crea TABLA_LOG_DESPACHO si no existe

// Columnas candidatas para la actualización de estado (resolución automática)
const COLS_NOTAS = [
    'estado' => ['estado', 'status', 'statu', 'estatus'],
    'nota'   => ['not_num', 'nota', 'numero_nota', 'nro_nota', 'codigobarra'],
    'fecha'  => ['fecha_despacho', 'fecha_proceso', 'fecha', 'fec_desp'],
];

const COLS_LOG = [
    'nota'   => ['nota', 'not_num', 'nota_id'],
    'guia'   => ['guia', 'nro_guia'],
    'chofer' => ['chofer', 'conductor'],
    'ayudantes' => ['ayudantes', 'asistentes'],
    'carro'  => ['carro', 'credenciales', 'placa', 'vehiculo'],
    'fecha'  => ['fecha', 'fecha_registro'],
];

/* =====================================================================================
 * UTILIDADES
 * ===================================================================================== */

/** Conexión PDO con fallback de drivers: sqlsrv → odbc → mssql. */
function db_conectar(): PDO
{
    $c = DB_CONFIG;
    $dsns = [];
    $dsns['sqlsrv'] = "sqlsrv:Server={$c['server']};Database={$c['database']}";
    $dsns['odbc']   = "odbc:Driver={SQL Server};Server={$c['server']};Database={$c['database']}";
    $dsns['mssql']  = "mssql:host={$c['server']};dbname={$c['database']}";

    $errores = [];
    foreach ($dsns as $nombre => $dsn) {
        if (!in_array($nombre, PDO::getAvailableDrivers(), true)) {
            continue;
        }
        try {
            $pdo = new PDO($dsn, $c['user'], $c['pass'], [
                PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
                PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
                // HOTFIX v4.17.1: NUNCA conexiones persistentes (sesiones dormidas).
                PDO::ATTR_PERSISTENT         => false,
            ]);
            return $pdo;
        } catch (Throwable $e) {
            $errores[] = $nombre . ': ' . $e->getMessage();
        }
    }
    throw new RuntimeException('No se pudo conectar a SQL Server. Detalles: ' . implode(' | ', $errores));
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

function resolver_columna(array $cols_tabla, array $candidatas, ?string $default = null): ?string
{
    foreach ($candidatas as $c) {
        if (in_array(strtolower((string)$c), $cols_tabla, true)) {
            return $c;
        }
    }
    return $default;
}

function ident_ok(string $ident): bool
{
    return (bool)preg_match('/^[A-Za-z_][A-Za-z0-9_]*$/', $ident);
}

/** Lee el body como array asociativo (JSON o form-urlencoded). */
function leer_body(): array
{
    $ct = strtolower((string)($_SERVER['CONTENT_TYPE'] ?? ''));
    if (strpos($ct, 'json') !== false) {
        $raw = file_get_contents('php://input');
        $data = json_decode($raw ?: '{}', true);
        return is_array($data) ? $data : [];
    }
    return $_POST;
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
    $body = leer_body();

    $nota    = trim((string)($body['nota'] ?? $body['notar'] ?? $body['not_num'] ?? ''));
    $guia    = trim((string)($body['guia'] ?? ''));
    $sub_ruta= trim((string)($body['sub_ruta'] ?? ''));
    $chofer  = trim((string)($body['chofer'] ?? ''));
    $ayudantes = trim((string)($body['ayudantes'] ?? ''));
    $carro   = trim((string)($body['carro'] ?? $body['credenciales'] ?? $body['placa'] ?? ''));
    $codigo  = trim((string)($body['codigo'] ?? ''));
    $tipo    = trim((string)($body['tipo'] ?? ''));

    $pdo = null;   // HOTFIX v4.17.1: se cierra en el finally SIEMPRE.

    if ($nota === '' && $codigo === '') {
        responder(400, ['status' => 'error', 'mensaje' => 'Falta nota o codigo.']);
    }

    $advertencias = [];

    if ($nota !== '') {
        $pdo = db_conectar();
        $cols_notas = columnas_tabla($pdo, TABLA_NOTAS);
        $col_estado = resolver_columna($cols_notas, COLS_NOTAS['estado'], 'estado');
        $col_nota   = resolver_columna($cols_notas, COLS_NOTAS['nota'], 'not_num');

        if ($col_estado !== null && $col_nota !== null) {
            // La nota puede llegar como lista separada por comas (ej. "N-1,N-3")
            $notas = array_filter(array_map('trim', explode(',', $nota)));
            $total_actualizadas = 0;
            foreach ($notas as $una_nota) {
                $sql = 'UPDATE [' . TABLA_NOTAS . '] SET [' . $col_estado . '] = ?';
                $params = [VALOR_PROCESADO];
                if (resolver_columna($cols_notas, COLS_NOTAS['fecha'], null) !== null) {
                    $col_fecha = resolver_columna($cols_notas, COLS_NOTAS['fecha']);
                    $sql .= ', [' . $col_fecha . '] = GETDATE()';
                }
                $sql .= ' WHERE [' . $col_nota . '] = ?';
                $params[] = $una_nota;

                $stmt = $pdo->prepare($sql);
                $stmt->execute($params);
                $total_actualizadas += (int)$stmt->rowCount();
            }
            $advertencias[] = 'Notas [' . implode(', ', $notas) . '] actualizadas a ' .
                              VALOR_PROCESADO . " ({$total_actualizadas} fila(s)).";
        } else {
            $advertencias[] = 'No se actualizó el estado: la tabla ' . TABLA_NOTAS .
                              ' no tiene columnas de estado/nota reconocibles (revise COLS_NOTAS).';
        }

        // Bitácora de despacho (opcional; falla silenciosa si la tabla no existe)
        if (TABLA_LOG_DESPACHO !== '') {
            try {
                $cols_log = columnas_tabla($pdo, TABLA_LOG_DESPACHO);

                if (count($cols_log) === 0 && AUTO_CREAR_LOG) {
                    $pdo->exec(
                        'CREATE TABLE [' . TABLA_LOG_DESPACHO . '] (' .
                        'id INT IDENTITY(1,1) PRIMARY KEY, ' .
                        'guia NVARCHAR(50), ' .
                        'nota NVARCHAR(50), ' .
                        'chofer NVARCHAR(120), ' .
                        'ayudantes NVARCHAR(120), ' .
                        'carro NVARCHAR(80), ' .
                        'fecha DATETIME DEFAULT GETDATE())'
                    );
                    $cols_log = columnas_tabla($pdo, TABLA_LOG_DESPACHO);
                }

                if (count($cols_log) > 0) {
                    $col_g = resolver_columna($cols_log, COLS_LOG['guia']);
                    $col_n = resolver_columna($cols_log, COLS_LOG['nota'], 'nota');
                    $col_c = resolver_columna($cols_log, COLS_LOG['chofer'], 'chofer');
                    $col_a = resolver_columna($cols_log, COLS_LOG['ayudantes'], 'ayudantes');
                    $col_v = resolver_columna($cols_log, COLS_LOG['carro'], 'carro');
                    $col_f = resolver_columna($cols_log, COLS_LOG['fecha'], 'fecha');

                    $set = [];
                    $para = [];
                    if ($col_g !== null) { $set[] = '[' . $col_g . ']'; $para[] = $guia; }
                    if ($col_n !== null) { $set[] = '[' . $col_n . ']'; $para[] = $nota; }
                    if ($col_c !== null) { $set[] = '[' . $col_c . ']'; $para[] = $chofer; }
                    if ($col_a !== null) { $set[] = '[' . $col_a . ']'; $para[] = $ayudantes; }
                    if ($col_v !== null) { $set[] = '[' . $col_v . ']'; $para[] = $carro; }
                    if ($col_f !== null) {
                        $set[] = '[' . $col_f . ']';
                        $para[] = date('Y-m-d H:i:s');
                    }
                    if (count($set) > 0) {
                        $marks = implode(',', array_fill(0, count($set), '?'));
                        $stmt_log = $pdo->prepare('INSERT INTO [' . TABLA_LOG_DESPACHO . '] (' .
                            implode(',', $set) . ') VALUES (' . $marks . ')');
                        $stmt_log->execute($para);
                        $advertencias[] = 'Bitácora: ' . $stmt_log->rowCount() . ' registro(s) insertado(s).';
                    }
                }
            } catch (Throwable $e) {
                $advertencias[] = 'Bitácora omitida: ' . $e->getMessage();
            }
        }
    } else {
        // Confirmación de escaneo simple (codigo + tipo), sin nota asociada
        $advertencias[] = 'Confirmación de escaneo ' . $tipo . ' para código ' . $codigo .
                          ' recibida (sin actualización de nota).';
    }

    $respuesta = ['status' => 'success', 'message' => 'Despacho registrado correctamente'];
    if (count($advertencias) > 0) {
        $respuesta['detalle'] = implode(' | ', $advertencias);
    }
    responder(200, $respuesta);

} catch (Throwable $e) {
    responder(500, [
        'status'  => 'error',
        'mensaje' => 'Error en visor/registro.php: ' . $e->getMessage(),
    ]);
} finally {
    // HOTFIX v4.17.1 (fuga de conexiones): cierre EXPLÍCITO de la conexión en
    // TODOS los caminos (éxito, error, excepción) — no dejar sesiones dormidas.
    if (isset($pdo) && $pdo instanceof PDO) {
        $pdo = null;
    }
    gc_collect_cycles();
}
