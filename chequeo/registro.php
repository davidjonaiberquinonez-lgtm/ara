<?php
/**
 * chequeo/registro.php — Registro y autochequeo del módulo de Chequeo.
 *
 * Regla de negocio (Chequeo Automático < 3 ítems): las notas con MENOS de 3
 * ítems (total_items < UMBRAL_ITEMS) se despachan DIRECTAMENTE desde el
 * pasillo: se omite la validación manual de la mesa de chequeo, el responsable
 * activo (inyectado por ARA) queda como validador y la estación se registra
 * como Mesa 0. El autochequeo también llega vía estado 'AUTOCHEQUEO' /
 * autochequeo=true (sync de preparacion). Las notas con 3 ítems o más
 * (>= UMBRAL_ITEMS) sí pasan por validación estricta de cantidades
 * (solicitada vs. chequeada).
 *
 * GET  /chequeo/registro.php                → formulario HTML de prueba (botón
 *                                             "Guardar" con name="registro").
 * POST /chequeo/registro.php                → procesa el guardado del chequeo
 *   (application/x-www-form-urlencoded o JSON).
 *
 * Parámetros aceptados (POST / JSON):
 *   nota | num_nota | notar | not_num → N° de nota/factura a registrar
 *   responsable | usuario | id_usuario → id del chequeador (ej. '03' REIMER QUIROZ)
 *   monto                              → monto de la nota (informativo)
 *   total_items                        → renglones únicos de la nota
 *   items                              → array JSON [{co_art, solicitada, chequeada}]
 *   registro                           → marca del botón "Guardar" (name="registro")
 *   autochequeo                        → 'true'/'1'/'on' si es autochequeo
 *   mesa, estado, hora                 → compatibilidad con registrar_autochequeo_php
 *                                        (Mesa 0 = pasillo; hora inyectada por ARA)
 *
 * Retorna: {"status":"success","message":"Nota Lista para procesar...",
 *           "es_nota_menor_3":bool,"total_items":int,"integridad_ok":bool,
 *           "discrepancias":[...],"autochequeo":bool,"mesa":"0","hora":"...",
 *           "sond_finaliza":"/sond/finaliza.mp","respuesta_ara":{...}}
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
            fwrite(STDERR, '{"success":false,"error":"FASE25_KILL: chequeo/registro.php excedió tiempo (' . $t_maxS . 's). Proceso abortado."}');
        }
        error_log('FASE25_KILL: script chequeo/registro.php excedió tiempo (' . $t_maxS . 's)');
        exit(1);
    }
});

error_reporting(E_ALL);
ini_set('display_errors', '0');
date_default_timezone_set('America/Caracas');

// HOTFIX v4.17.1 (fuga de conexiones MySQL): prohibir conexiones persistentes
// accidentales — cada request abre y CIERRA su propia conexión.
ini_set('mysql.allow_persistent', 'Off');
ini_set('mysqli.allow_persistent', 'Off');

header('Content-Type: application/json; charset=utf-8');

/* =============================================================================
 * CONFIGURACIÓN — Ajuste aquí la URL del servidor ARA y el Profit/gestion.
 * El envío al servidor ARA es BEST-EFFORT: si ARA no responde, la transacción
 * PHP se completa igual (el aviso se expone en 'advertencias').
 * ============================================================================= */

// URL base del servidor ARA (Flask 0.0.0.0:5000). Si ARA corre en la misma
// máquina que este PHP, puede usarse 'http://localhost:5000'.
const ARA_API_URL         = 'http://192.168.4.148:5000';
const ARA_FINALIZAR_PATH  = '/api/preparacion_hex/finalizar';
const ARA_TIMEOUT_S       = 3.0;

// Umbral de la regla de negocio: notas con MENOS de este número de ítems
// (total_items < UMBRAL_ITEMS) disparan el AUTOCHEQUEO DIRECTO desde el
// pasillo (Mesa 0), omitiendo la validación manual de la mesa de chequeo.
const UMBRAL_ITEMS        = 3;

// MySQL legacy del flujo (barquisimeto .148): aquí viven rep_not (estatus de
// la nota) y gestion (trazabilidad física: verifi_cheq/num_cheq/hora2 del
// chequeo). La interfaz web lee de estas tablas — si no se escribe aquí, la
// web muestra celdas vacías o 0000-00-00 00:00:00. Sobrescribible por env:
// MYSQL_HOST / MYSQL_DATABASE / MYSQL_USER / MYSQL_PASSWORD / MYSQL_PORT.
define('DB_CONFIG', [
    'server'   => getenv('MYSQL_HOST') ?: '192.168.4.148',
    'database' => getenv('MYSQL_DATABASE') ?: 'barquisimeto',
    'user'     => getenv('MYSQL_USER') ?: 'jonaiber',
    'pass'     => getenv('MYSQL_PASSWORD') ?: 'Crist2026.',
    'port'     => (int)(getenv('MYSQL_PORT') ?: 3306),
]);

const TABLA_NOTAS       = 'rep_not';
const VALOR_CHEQUEADO   = 'CHEQUEO';    // valor legacy REAL de rep_not.estatus (731 filas usan 'CHEQUEO'; 'CHEQUEADA' solo 1)
const TABLA_GESTION     = 'gestion';       // trazabilidad física del flujo
const VALOR_VERIFI_CHEQ = 'VERIFICADA';    // valor legacy de gestion.verifi_cheq
const VALOR_TIPO_CHEQ   = 'CHEQUEADOR';    // gestion.tip_pre
const VALOR_UBIC_CHEQ   = 'CHEQUEO';       // gestion.ubicacion2
const TABLA_LOG_CHEQUEO = 'log_chequeo';   // bitácora local (puede no existir)
const AUTO_CREAR_LOG    = false;

// Columnas candidatas (resolución automática), igual patrón que visor/registro.php
const COLS_NOTAS = [
    'estado' => ['estado', 'status', 'statu', 'estatus'],
    'nota'   => ['not_num', 'nota', 'numero_nota', 'nro_nota', 'codigobarra', 'cod_nota'],
    'fecha'  => ['fecha_chequeo', 'fecha_registro', 'fecha', 'fec_cheq'],
];

// Bloque de CHEQUEO de la tabla gestion (trazabilidad física):
// verifi_cheq | numeroMesa | num_cheq | tip_pre | ubicacion2 | hora2
const COLS_GESTION = [
    'nota'       => ['cd_barr', 'codigo', 'cod_nota'],
    'verifi'     => ['verifi_cheq', 'verificacion_chequeo'],
    'mesa'       => ['numeroMesa', 'num_mesa', 'mesa'],
    'chequeador' => ['num_cheq', 'chequeador'],
    'tipo'       => ['tip_pre', 'tipo_chequeador'],
    'ubicacion'  => ['ubicacion2', 'ubicacion_chequeo'],
    'hora'       => ['hora2', 'fecha_chequeo', 'fec_cheq'],
];

const COLS_LOG = [
    'nota'   => ['nota', 'not_num', 'nota_id'],
    'usuario'=> ['usuario', 'responsable', 'chequeador', 'id_usuario'],
    'monto'  => ['monto', 'total', 'monto_nota'],
    'items'  => ['items', 'cantidad_items', 'total_items'],
    'fecha'  => ['fecha', 'fecha_registro'],
];

/* =============================================================================
 * UTILIDADES
 * ============================================================================= */

/** Conexión PDO a la MySQL legacy del flujo (rep_not/gestion). */
function db_conectar(): PDO
{
    $c = DB_CONFIG;
    if (!in_array('mysql', PDO::getAvailableDrivers(), true)) {
        throw new RuntimeException('Driver pdo_mysql no disponible. Drivers: ' . implode(', ', PDO::getAvailableDrivers()));
    }
    $dsn = "mysql:host={$c['server']};port={$c['port']};dbname={$c['database']};charset=utf8mb4";
    return new PDO($dsn, $c['user'], $c['pass'], [
        PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        // HOTFIX v4.17.1: NUNCA conexiones persistentes (sesiones dormidas en MySQL).
        PDO::ATTR_PERSISTENT         => false,
        // rowCount() de UPDATE = filas COINCIDENTES, no solo filas MODIFICADAS:
        // un re-chequeo con valores idénticos NO debe reportar 0 filas (v4.5).
        PDO::MYSQL_ATTR_FOUND_ROWS   => true,
    ]);
}

/** Devuelve las columnas reales de una tabla (minúsculas) o [] si no existe. */
function columnas_tabla(PDO $pdo, string $tabla): array
{
    try {
        $sql = 'SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ?';
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
    $json = json_encode(
        sanitizar_utf8($payload),
        JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES
    );
    if ($json === false) {
        $json = json_encode(['status' => 'error', 'mensaje' => 'json_encode falló: ' . json_last_error_msg()]);
    }
    echo $json;
    exit;
}

/**
 * Sanitiza recursivamente strings no-UTF-8 (p.ej. mensajes del driver ODBC
 * de SQL Server en Windows-1252) para que json_encode nunca devuelva false.
 */
function sanitizar_utf8($valor)
{
    if (is_string($valor)) {
        return preg_match('//u', $valor) ? $valor : latin1_a_utf8($valor);
    }
    if (is_array($valor)) {
        $r = [];
        foreach ($valor as $k => $v) {
            $r[sanitizar_utf8($k)] = sanitizar_utf8($v);
        }
        return $r;
    }
    return $valor;
}

function latin1_a_utf8(string $s): string
{
    return function_exists('mb_convert_encoding')
        ? mb_convert_encoding($s, 'UTF-8', 'Windows-1252')
        : utf8_encode($s);
}

function parsear_bool($valor): bool
{
    if (is_bool($valor)) {
        return $valor;
    }
    return in_array(strtolower((string)$valor), ['1', 'true', 'on', 'yes'], true);
}

/** Contar ítems únicos (renglones) de la nota. */
function contar_items_unicos(array $items): int
{
    $unicos = [];
    foreach ($items as $it) {
        if (!is_array($it)) {
            continue;
        }
        $cod = strtoupper(trim((string)($it['co_art'] ?? $it['codigo'] ?? $it['cod'] ?? '')));
        if ($cod !== '') {
            $unicos[$cod] = true;
        }
    }
    return count($unicos);
}

/**
 * Mapea el identificador del chequeador contra la tabla legacy `usuarios`
 * (numero | id | nombre) y devuelve el `numero` canónico (sin ceros a la
 * izquierda) + el nombre real. El sync manda '03', pero usuarios.numero es
 * '3': sin esta resolución, gestion.num_cheq no enlaza con la cuenta de
 * puntos de la web legacy (COUNT por num_cheq ↔ usuarios.numero).
 */
function resolver_usuario_numero(PDO $pdo, string $identificador): array
{
    $normalizado = ltrim(trim($identificador), '0');   // '03' → '3'
    if ($normalizado === '') {
        return ['numero' => null, 'nombre' => null];
    }
    try {
        // Match EXACTO por numero primero (v4.23): `numero` e `id` son
        // columnas INDEPENDIENTES en usuarios (ej. numero='9'→MIGUEL CAMPOS
        // id=10, pero id=9→PEDRO VIZCAYA numero='8'). El OR sin ORDER BY
        // dejaba que MySQL devolviera cualquiera de los dos usuarios reales
        // que calzaran por columnas distintas — para autochequeo (el mismo
        // operador que preparó) esto resolvía al chequeador equivocado.
        $stmt = $pdo->prepare(
            'SELECT numero, nombre FROM usuarios WHERE activo = 1 AND CAST(numero AS CHAR) = ? LIMIT 1'
        );
        $stmt->execute([$normalizado]);
        $fila = $stmt->fetch(PDO::FETCH_ASSOC);
        if (!$fila) {
            $stmt = $pdo->prepare(
                'SELECT numero, nombre FROM usuarios WHERE activo = 1 ' .
                'AND (CAST(id AS CHAR) = ? OR UPPER(nombre) = UPPER(?)) LIMIT 1'
            );
            $stmt->execute([$normalizado, $identificador]);
            $fila = $stmt->fetch(PDO::FETCH_ASSOC);
        }
        if ($fila) {
            return ['numero' => (string)$fila['numero'], 'nombre' => (string)$fila['nombre']];
        }
    } catch (Throwable $e) {
        error_log('[chequeo/registro.php] resolver_usuario_numero falló: ' . $e->getMessage());
    }
    return ['numero' => null, 'nombre' => null];
}

/** POST JSON hacia el servidor ARA (cURL con fallback a stream context). */
function http_post_ara(string $url, array $payload): array
{
    $json = json_encode(sanitizar_utf8($payload), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    if ($json === false) {
        $json = json_encode(['error' => 'payload inválido']);
    }
    $opciones = [
        'http' => [
            'method'  => 'POST',
            'header'  => "Content-Type: application/json; charset=utf-8\r\n",
            'content' => $json ?: '{}',
            'timeout' => ARA_TIMEOUT_S,
            'ignore_errors' => true,
        ],
    ];
    $ctx = stream_context_create($opciones);
    $texto = @file_get_contents($url, false, $ctx);
    if ($texto === false) {
        $err = error_get_last();
        return ['status' => 'error', 'respuesta' => '', 'detalle' => $err['message'] ?? 'sin respuesta'];
    }
    $dec = json_decode($texto, true);
    return [
        'status'   => is_array($dec) ? (($dec['status'] ?? 'ok') === 'success' ? 'ok' : 'error') : 'ok',
        'respuesta'=> $texto,
        'detalle'  => $dec,
    ];
}

/** Validación estricta ítem por ítem: solicitada vs. chequeada. */
function validar_integridad(array $items): array
{
    $discrepancias = [];
    foreach ($items as $it) {
        if (!is_array($it)) {
            continue;
        }
        $co  = strtoupper(trim((string)($it['co_art'] ?? $it['codigo'] ?? $it['cod'] ?? '')));
        $des = trim((string)($it['descripcion'] ?? $it['des'] ?? ''));
        $solicitada = (float)($it['solicitada'] ?? $it['pedida'] ?? 0);
        $chequeada  = (float)($it['chequeada'] ?? $it['escaneada'] ?? 0);

        if ($co === '') {
            continue;
        }
        if (abs($solicitada - $chequeada) < 0.0001) {
            continue;
        }
        $discrepancias[] = [
            'co_art'      => $co,
            'descripcion' => $des,
            'solicitada'  => $solicitada,
            'chequeada'   => $chequeada,
            'diferencia'  => round($chequeada - $solicitada, 4),
            'tipo'        => ($chequeada > $solicitada) ? 'EXCESO' : 'FALTANTE',
        ];
    }
    return ['ok' => count($discrepancias) === 0, 'discrepancias' => $discrepancias];
}

/**
 * Actualiza el estado de la nota en la MySQL legacy:
 *   - rep_not  : estatus = 'CHEQUEADA' (WHERE cod_nota)
 *   - gestion  : bloque de CHEQUEO (verifi_cheq, numeroMesa, num_cheq,
 *                tip_pre, ubicacion2, hora2) — es la trazabilidad física
 *                que lee la interfaz web; sin esto la web muestra celdas
 *                vacías o 0000-00-00 00:00:00.
 * Degradable: si falla la conexión se registra advertencia y se continúa.
 */
function registrar_estado_legacy(string $nota, array $advertencias, ?string $chequeador = null, ?int $mesa = null, ?string $hora = null, int $total_items = 0): array
{
    try {
        $pdo = db_conectar();
    } catch (Throwable $e) {
        $advertencias[] = 'MySQL legacy no disponible: ' . $e->getMessage();
        return $advertencias;
    }

    $hora_valida = ($hora !== null && preg_match('/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$/', $hora))
        ? date('Y-m-d H:i:s', strtotime(str_replace('T', ' ', $hora)))
        : date('Y-m-d H:i:s');
    // Mapeo id_usuario → usuarios (numero | id | nombre): gestion.num_cheq debe
    // quedar con el numero canónico (sin ceros a la izquierda) para que la web
    // legacy sume los puntos del usuario (COUNT num_cheq ↔ usuarios.numero).
    $usuario = ($chequeador !== null && $chequeador !== '')
        ? resolver_usuario_numero($pdo, $chequeador)
        : ['numero' => null, 'nombre' => null];
    $num_cheq = $usuario['numero'] !== null
        ? (int)$usuario['numero']
        : ((is_numeric($chequeador)) ? (int)$chequeador : 0);
    if ($usuario['nombre'] !== null) {
        $advertencias[] = 'usuarios: ' . $chequeador . ' → ' . $usuario['nombre'] .
                          ' (numero=' . $usuario['numero'] . ', num_cheq=' . $num_cheq . ').';
    } else {
        $advertencias[] = 'usuarios: ' . var_export($chequeador, true) .
                          ' sin match en usuarios (numero/id/nombre); num_cheq=' . $num_cheq . '.';
    }
    $num_mesa = $mesa ?? 0;

    // Transacción: rep_not + gestion se escriben juntas; si algo falla se
    // revierte TODO (nada de impactos a medias en el legacy).
    $pdo->beginTransaction();
    try {
        // --- 1) rep_not: estatus de la nota -------------------------------------
        $cols = columnas_tabla($pdo, TABLA_NOTAS);
        $col_estado = resolver_columna($cols, COLS_NOTAS['estado'], 'estatus');
        $col_nota   = resolver_columna($cols, COLS_NOTAS['nota'], 'cod_nota');
        if ($col_estado !== null && $col_nota !== null) {
            $sql = 'UPDATE `' . TABLA_NOTAS . '` SET `' . $col_estado . '` = ? WHERE `' . $col_nota . '` = ?';
            $stmt = $pdo->prepare($sql);
            $stmt->execute([VALOR_CHEQUEADO, $nota]);
            $advertencias[] = 'rep_not: nota ' . $nota . ' marcada como ' . VALOR_CHEQUEADO .
                              ' (' . (int)$stmt->rowCount() . ' fila(s)).';
        } else {
            $advertencias[] = 'No se actualizó ' . TABLA_NOTAS . ': sin columnas reconocibles.';
        }

        // --- 2) gestion: trazabilidad física del chequeo -------------------------
        // cd_barr es INT(11): el valor de búsqueda debe normalizarse a entero
        // cuando la nota sea numérica (el formulario puede mandar 'NC-72160252'
        // o '72160252-1'; gestion.cd_barr solo entiende números). v4.5.
        $cols_g = array_map('strtolower', columnas_tabla($pdo, TABLA_GESTION));
        $cg = [
            'nota'       => resolver_columna($cols_g, COLS_GESTION['nota'], 'cd_barr'),
            'verifi'     => resolver_columna($cols_g, COLS_GESTION['verifi'], 'verifi_cheq'),
            'mesa'       => resolver_columna($cols_g, COLS_GESTION['mesa'], 'numeromesa'),
            'chequeador' => resolver_columna($cols_g, COLS_GESTION['chequeador'], 'num_cheq'),
            'tipo'       => resolver_columna($cols_g, COLS_GESTION['tipo'], 'tip_pre'),
            'ubicacion'  => resolver_columna($cols_g, COLS_GESTION['ubicacion'], 'ubicacion2'),
            'hora'       => resolver_columna($cols_g, COLS_GESTION['hora'], 'hora2'),
        ];
        if ($cg['nota'] !== null && $cg['verifi'] !== null) {
            $nota_busqueda = is_numeric($nota) ? (int)$nota : $nota;
            $set_parte = ['`' . $cg['verifi'] . '` = ?'];
            $set_para  = [VALOR_VERIFI_CHEQ];
            if ($cg['chequeador'] !== null) { $set_parte[] = '`' . $cg['chequeador'] . '` = ?'; $set_para[] = $num_cheq; }
            if ($cg['tipo'] !== null)       { $set_parte[] = '`' . $cg['tipo'] . '` = ?';       $set_para[] = VALOR_TIPO_CHEQ; }
            if ($cg['ubicacion'] !== null)  { $set_parte[] = '`' . $cg['ubicacion'] . '` = ?';  $set_para[] = VALOR_UBIC_CHEQ; }
            if ($cg['hora'] !== null)       { $set_parte[] = '`' . $cg['hora'] . '` = ?';       $set_para[] = $hora_valida; }
            if ($cg['mesa'] !== null)       { $set_parte[] = '`' . $cg['mesa'] . '` = ?';       $set_para[] = $num_mesa; }
            $col_cant_items = resolver_columna($cols_g, ['cant_items', 'cantidad_items', 'items'], null);
            if ($col_cant_items !== null && $total_items > 0) {
                $set_parte[] = '`' . $col_cant_items . '` = ?';
                $set_para[]  = $total_items;   // conteo REAL de renglones (puntos web)
            }

            $sql_update = 'UPDATE `' . TABLA_GESTION . '` SET ' . implode(', ', $set_parte) .
                          ' WHERE `' . $cg['nota'] . '` = ?';
            $set_para_update = $set_para;
            $set_para_update[] = $nota_busqueda;
            $stmt = $pdo->prepare($sql_update);
            $stmt->execute($set_para_update);
            $filas = (int)$stmt->rowCount();
            error_log('[chequeo/registro.php] gestion UPDATE nota=' . $nota .
                      ' (busqueda=' . var_export($nota_busqueda, true) . ') col=' . $cg['nota'] .
                      ' filas=' . $filas . ' sql=' . $sql_update);
            $advertencias[] = 'gestion: bloque de chequeo actualizado (' . $filas . ' fila(s)) para la nota ' . $nota . '.';

            if ($filas === 0) {
                // El registro no se tocó. Con FOUND_ROWS activo esto significa que
                // la fila NO EXISTE en gestion; verificación defensiva antes del
                // INSERT para no duplicar filas (la tabla no tiene clave única).
                $stmt_sel = $pdo->prepare(
                    'SELECT 1 FROM `' . TABLA_GESTION . '` WHERE `' . $cg['nota'] . '` = ? LIMIT 1'
                );
                $stmt_sel->execute([$nota_busqueda]);
                $existe = (bool)$stmt_sel->fetchColumn();

                if ($existe) {
                    // Fila existente sin cambios detectados (carrera o re-chequeo):
                    // reintentar el UPDATE y seguir, NUNCA insertar duplicado.
                    $stmt->execute($set_para_update);
                    error_log('[chequeo/registro.php] gestion fila existente sin cambios: retry UPDATE filas=' .
                              (int)$stmt->rowCount() . ' nota=' . $nota);
                    $advertencias[] = 'gestion: fila existente sin cambios, retry UPDATE ' .
                                      (int)$stmt->rowCount() . ' fila(s).';
                } else {
                    // La nota no existe aún en gestion → INSERT con TODAS las
                    // columnas NOT NULL (v4.5: faltaba 'ubicacion' y el INSERT
                    // reventaba con error 1364 "Field doesn't have a default",
                    // haciendo rollback de rep_not + gestion).
                    $ic = [];
                    $iv = [];
                    $agrega = function (string $col, $val) use (&$ic, &$iv) {
                        $col = strtolower($col);
                        if (!in_array($col, $ic, true)) {
                            $ic[] = $col;
                            $iv[] = $val;
                        }
                    };
                    foreach (['nota' => $nota_busqueda, 'verifi' => VALOR_VERIFI_CHEQ, 'mesa' => $num_mesa,
                              'chequeador' => $num_cheq, 'tipo' => VALOR_TIPO_CHEQ,
                              'ubicacion' => VALOR_UBIC_CHEQ, 'hora' => $hora_valida] as $k => $v) {
                        if ($cg[$k] !== null) { $agrega($cg[$k], $v); }
                    }
                    foreach (['hora', 'hora2', 'fecha'] as $c) {
                        if (in_array($c, $cols_g, true)) { $agrega($c, $hora_valida); }
                    }
                    // Defaults legacy completos: TODAS las NOT NULL de la tabla
                    // (incluida 'ubicacion' del bloque PREPARACION — sin valor por
                    // defecto, el INSERT falla). 'ubicacion' se rellena con el
                    // valor de CHEQUEO por compatibilidad con la web.
                    foreach (['co_cli' => '', 'cli_des' => '', 'verifi_pre' => '', 'num_prep' => 0,
                              'tipo_perso' => '', 'ubicacion' => VALOR_UBIC_CHEQ,
                              'num_emb' => 0, 'verifi_emb' => '', 'tip_emb' => '',
                              'ubicacion3' => '', 'hora3' => $hora_valida,
                              'cant_items' => $total_items, 'num_mesa_emba' => 0] as $c => $v) {
                        if (in_array($c, $cols_g, true)) { $agrega($c, $v); }
                    }
                    $cols_sql = implode(',', array_map(static fn(string $c) => '`' . $c . '`', $ic));
                    $marks = implode(',', array_fill(0, count($iv), '?'));
                    $sql_insert = 'INSERT INTO `' . TABLA_GESTION . '` (' . $cols_sql . ') VALUES (' . $marks . ')';
                    try {
                        $stmt = $pdo->prepare($sql_insert);
                        $stmt->execute($iv);
                        error_log('[chequeo/registro.php] gestion INSERT nota=' . $nota .
                                  ' columnas=' . count($ic) . ' sql=' . $sql_insert);
                        $advertencias[] = 'gestion: fila creada para la nota ' . $nota .
                                          ' (INSERT ' . (int)$stmt->rowCount() . ').';
                    } catch (Throwable $eIns) {
                        // Carrera con otro proceso (la fila nació entre el SELECT
                        // y el INSERT): reintentar el UPDATE en vez de abortar.
                        $codigo = (string)$eIns->getCode();
                        if ($codigo === '23000' || stripos($eIns->getMessage(), 'Duplicate') !== false) {
                            $stmt->execute($set_para_update);
                            error_log('[chequeo/registro.php] gestion INSERT duplicado (carrera), retry UPDATE filas=' .
                                      (int)$stmt->rowCount() . ' nota=' . $nota);
                            $advertencias[] = 'gestion: INSERT duplicado (carrera), retry UPDATE ' .
                                              (int)$stmt->rowCount() . ' fila(s).';
                        } else {
                            throw $eIns;
                        }
                    }
                }
            }
        } else {
            error_log('[chequeo/registro.php] gestion SIN COLUMNAS RECONOCIBLES: cols=' . implode(',', $cols_g));
            $advertencias[] = 'No se actualizó ' . TABLA_GESTION . ': sin columnas reconocibles.';
        }

        $pdo->commit();
    } catch (Throwable $e) {
        if ($pdo->inTransaction()) {
            $pdo->rollBack();
        }
        $advertencias[] = 'MySQL legacy: no se aplicó la transacción de chequeo (' . $e->getMessage() . ').';
    } finally {
        // HOTFIX v4.17.1 (fuga de conexiones MySQL): cierre EXPLÍCITO de la
        // conexión en TODOS los caminos (éxito, rollback, excepción) para no
        // dejar sesiones dormidas en el MySQL legacy.
        if (isset($pdo)) {
            $pdo = null;
        }
    }

    return $advertencias;
}

/** Bitácora local del chequeo (OPCIONAL; falla silenciosa si la tabla no existe). */
function registrar_bitacora(string $nota, string $usuario, float $monto, int $total_items): array
{
    $advertencias = [];
    try {
        $pdo = db_conectar();
    } catch (Throwable $e) {
        return $advertencias;
    }

    try {
        $cols = columnas_tabla($pdo, TABLA_LOG_CHEQUEO);
        if (count($cols) === 0 && AUTO_CREAR_LOG) {
            $pdo->exec(
                'CREATE TABLE `' . TABLA_LOG_CHEQUEO . '` (' .
                'id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, ' .
                'nota VARCHAR(50), usuario VARCHAR(120), monto DOUBLE, ' .
                'items INT, fecha DATETIME DEFAULT NOW()) ENGINE=InnoDB'
            );
            $cols = columnas_tabla($pdo, TABLA_LOG_CHEQUEO);
        }
        if (count($cols) === 0) {
            return $advertencias;
        }

        $col_n = resolver_columna($cols, COLS_LOG['nota'], 'nota');
        $col_u = resolver_columna($cols, COLS_LOG['usuario'], 'usuario');
        $col_m = resolver_columna($cols, COLS_LOG['monto'], 'monto');
        $col_i = resolver_columna($cols, COLS_LOG['items'], 'items');
        $col_f = resolver_columna($cols, COLS_LOG['fecha'], 'fecha');

        $set = [];
        $para = [];
        if ($col_n !== null) { $set[] = '`' . $col_n . '`'; $para[] = $nota; }
        if ($col_u !== null) { $set[] = '`' . $col_u . '`'; $para[] = $usuario; }
        if ($col_m !== null) { $set[] = '`' . $col_m . '`'; $para[] = $monto; }
        if ($col_i !== null) { $set[] = '`' . $col_i . '`'; $para[] = $total_items; }
        if ($col_f !== null) { $set[] = '`' . $col_f . '`'; $para[] = date('Y-m-d H:i:s'); }
        if (count($set) > 0) {
            $marks = implode(',', array_fill(0, count($set), '?'));
            $stmt = $pdo->prepare('INSERT INTO `' . TABLA_LOG_CHEQUEO . '` (' .
                implode(',', $set) . ') VALUES (' . $marks . ')');
            $stmt->execute($para);
            $advertencias[] = 'Bitácora: ' . (int)$stmt->rowCount() . ' registro(s).';
        }
        return $advertencias;
    } finally {
        // HOTFIX v4.17.1 (fuga de conexiones MySQL): cierre EXPLÍCITO.
        $pdo = null;
    }
}

/* =============================================================================
 * FORMULARIO HTML DE PRUEBA (GET) — botón "Guardar" name="registro"
 * ============================================================================= */
function render_formulario(): void
{
    header('Content-Type: text/html; charset=utf-8');
    echo '<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">' .
         '<meta name="viewport" content="width=device-width, initial-scale=1">' .
         '<title>Chequeo — Registro de Nota</title>' .
         '<style>
            body{font-family:Segoe UI,Arial,sans-serif;background:#f1f5f9;margin:0;padding:24px;color:#0f172a}
            .card{max-width:640px;margin:0 auto;background:#fff;border-radius:14px;padding:24px;box-shadow:0 4px 14px rgba(0,0,0,.08)}
            h2{margin:0 0 4px;color:#1e3a8a}
            p.sub{color:#64748b;margin:0 0 18px;font-size:13px}
            label{display:block;font-size:12px;font-weight:700;color:#334155;margin:12px 0 4px}
            input,button{width:100%;box-sizing:border-box;padding:10px;border:1px solid #cbd5e1;border-radius:8px;font-size:14px}
            .row{display:flex;gap:8px;margin-top:8px;align-items:center}
            .row input{flex:1}
            .row button{width:auto;padding:10px 14px}
            button[type=submit]{margin-top:20px;background:#1d4ed8;color:#fff;border:none;font-weight:700;padding:14px;border-radius:10px;font-size:16px}
            button.agregar{background:#16a34a;color:#fff;border:none}
            .row button.quitar{background:#dc2626;color:#fff;border:none}
          </style></head><body><div class="card">
         <h2>📋 Registro de Chequeo</h2>
         <p class="sub">Guardado de nota con regla &lt; ' . UMBRAL_ITEMS . ' ítems (autochequeo directo desde el pasillo, Mesa 0).</p>
         <form method="post">
            <input type="hidden" name="registro" value="1">
            <label>N° de Nota / Factura</label>
            <input type="text" name="num_nota" required placeholder="Ej: 72160252">
            <label>Responsable / Chequeador (ID)</label>
            <input type="text" name="responsable" required placeholder="Ej: 03">
            <label>Monto</label>
            <input type="number" name="monto" step="0.01" value="0">
            <label>Total de ítems (renglones únicos)</label>
            <input type="number" name="total_items" value="1" min="1">
            <label>Renglones (solicitada vs. chequeada)</label>
            <div id="filas"></div>
            <button type="button" class="agregar" onclick="agregarFila()">+ Agregar renglón</button>
            <button type="submit">💾 Guardar Chequeo</button>
         </form>
         <script>
            function agregarFila(co, sol, che){
                const d=document.createElement("div");d.className="row";
                d.innerHTML=\'<input name="items_cod[]" placeholder="Código art." value="\'+(co||\'\')+\'">\'+
                            \'<input name="items_sol[]" type="number" min="0" placeholder="Solicitada" value="\'+(sol||\'\')+\'">\'+
                            \'<input name="items_che[]" type="number" min="0" placeholder="Chequeada" value="\'+(che||\'\')+\'">\'+
                            \'<button type="button" class="quitar" onclick="this.parentNode.remove()">✕</button>\';
                document.getElementById("filas").appendChild(d);
            }
            for(let i=0;i<4;i++) agregarFila();
         </script></div></body></html>';
    exit;
}

/* =============================================================================
 * FLUJO PRINCIPAL
 * ============================================================================= */

// GET sin parámetros → formulario de prueba
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'GET' && empty($_GET)) {
    render_formulario();
}

try {
    $body = leer_body();

    // Aliases ampliados (v4.5): el formulario legacy usa 'nota', pero la web y
    // el sync pueden enviar codigoBarra / numero / factura / codigo / num_not.
    $nota        = trim((string)($body['nota'] ?? $body['num_nota'] ?? $body['notar'] ?? $body['not_num']
                              ?? $body['codigoBarra'] ?? $body['codigo_barr'] ?? $body['numero']
                              ?? $body['factura'] ?? $body['codigo'] ?? ''));
    $responsable = trim((string)($body['responsable'] ?? $body['usuario'] ?? $body['id_usuario'] ?? ''));
    $monto       = (float)($body['monto'] ?? 0);
    $autochequeo = parsear_bool($body['autochequeo'] ?? false);
    $mesa        = trim((string)($body['mesa'] ?? '0'));      // Mesa 0 = pasillo (autochequeo)
    $estado      = trim((string)($body['estado'] ?? 'CHEQUEADA'));
    $hora        = trim((string)($body['hora'] ?? ''));
    if ($hora === '' || !preg_match('/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$/', $hora)) {
        $hora = date('Y-m-d H:i:s');
    }
    $registro    = isset($body['registro']);

    // Normalización de la nota (v4.5): gestion.cd_barr es INT; si el POST llega
    // con formato compuesto ('NC-72160252', '72160252-1', 'NOTA 72160252') se
    // extrae la secuencia numérica para que el WHERE del UPDATE coincida.
    if ($nota !== '' && !ctype_digit($nota) && preg_match('/\d{6,12}/', $nota, $m)) {
        error_log('[chequeo/registro.php] nota normalizada: "' . $nota . '" → "' . $m[0] . '"');
        $nota = $m[0];
    }

    error_log('[chequeo/registro.php] POST recibido: claves=' .
              implode(',', array_keys($body)) . ' | nota="' . $nota .
              '" responsable="' . $responsable . '" mesa=' . $mesa .
              ' estado="' . $estado . '" hora="' . $hora . '"');

    if ($nota === '') {
        responder(400, ['status' => 'error', 'mensaje' => 'Falta la nota (num_nota).']);
    }
    if ($responsable === '') {
        $responsable = trim((string)($body['usuario'] ?? ''));
    }
    if ($responsable === '') {
        responder(400, ['status' => 'error', 'mensaje' => 'Falta el responsable/chequeador.']);
    }

    // 1) Construcción del arreglo de ítems (JSON o formulario por arrays)
    $items = [];
    if (isset($body['items']) && is_array($body['items'])) {
        $items = $body['items'];
    } elseif (isset($body['items']) && is_string($body['items'])) {
        $dec = json_decode($body['items'], true);
        $items = is_array($dec) ? $dec : [];
    } else {
        // Formulario: arrays paralelos items_cod[] / items_sol[] / items_che[]
        $cods = isset($body['items_cod']) && is_array($body['items_cod']) ? $body['items_cod'] : [];
        $sols = isset($body['items_sol']) && is_array($body['items_sol']) ? $body['items_sol'] : [];
        $ches = isset($body['items_che']) && is_array($body['items_che']) ? $body['items_che'] : [];
        foreach ($cods as $i => $cod) {
            $cod = trim((string)$cod);
            if ($cod === '') {
                continue;
            }
            $items[] = [
                'co_art'     => $cod,
                'solicitada' => (float)($sols[$i] ?? 0),
                'chequeada'  => (float)($ches[$i] ?? 0),
            ];
        }
    }

    // 2) Cálculo del total de renglones/ítems únicos de la nota actual
    $total_items = (int)($body['total_items'] ?? 0);
    if ($total_items <= 0 && count($items) > 0) {
        $total_items = contar_items_unicos($items);
    }
    if ($total_items <= 0) {
        $total_items = 1; // sin dato fiable se asume 1 (dispara la regla <3 → autochequeo)
    }

    // 3) Condición de umbral: nota con MENOS de 3 ítems → autochequeo directo
    $es_nota_menor_3 = ($total_items < UMBRAL_ITEMS);
    $autochequeo_real = $es_nota_menor_3 || $autochequeo || $estado === 'AUTOCHEQUEO';

    $advertencias = [];

    // 4) Nota con 3 ítems o más → validación estricta de integridad de cantidades
    //    (las notas < 3 ítems se autochequean sin validación manual)
    $validacion = ['ok' => true, 'discrepancias' => []];
    if (!$es_nota_menor_3) {
        $validacion = validar_integridad($items);
        if (!$validacion['ok']) {
            $advertencias[] = 'Nota con ' . $total_items . ' ítems (>= ' . UMBRAL_ITEMS . '): ' .
                              count($validacion['discrepancias']) .
                              ' renglón(es) sin coincidencia solicitada/chequeada.';
        } else {
            $advertencias[] = 'Nota con ' . $total_items . ' ítems (>= ' . UMBRAL_ITEMS .
                              '): integridad de cantidades OK.';
        }
    } else {
        $advertencias[] = 'Autochequeo (< ' . UMBRAL_ITEMS . ' ítems) desde Mesa 0: ' .
                          'sin validación manual requerida.';
    }

    // 5) Registro en el lado PHP (MySQL legacy: rep_not + gestion + bitácora)
    $advertencias = registrar_estado_legacy($nota, $advertencias, $responsable, (int)$mesa, $hora, $total_items);
    $advertencias = array_merge($advertencias, registrar_bitacora($nota, $responsable, $monto, $total_items));

    // 6) Notificación al servidor ARA (payload extendido + autochequeo: true).
    //    Solo se reenvía a ARA cuando existe evidencia de un guardado real
    //    (marcador 'registro', total_items explícito o ítems chequeados). El sync
    //    que ARA hace vía registrar_autochequeo_php (solo nota/usuario/mesa/estado)
    //    NO lleva estas claves, con lo que se evita el ciclo ARA ↔ PHP.
    $total_items_explicito = (int)($body['total_items'] ?? 0) > 0;
    $hay_items             = count($items) > 0;
    $reenviar_a_ara        = ($registro || $total_items_explicito || $hay_items);
    $respuesta_ara = ['status' => 'no_enviado', 'respuesta' => '', 'detalle' => null];
    if (ARA_API_URL !== '' && $reenviar_a_ara) {
        $respuesta_ara = http_post_ara(ARA_API_URL . ARA_FINALIZAR_PATH, [
            'codigoBarra'   => $nota,
            'preparador'    => $responsable,
            'monto'         => $monto,
            'total_items'   => $total_items,
            'autochequeo'   => true,
            'es_nota_menor_3' => $es_nota_menor_3,
            'mesa'          => $mesa,
            'hora'          => $hora,
            'estado'        => $estado,
            'items_chequeados' => $items,
        ]);
        if ($respuesta_ara['status'] !== 'ok') {
            $advertencias[] = 'ARA no confirmó la finalización: ' .
                              ($respuesta_ara['detalle']['mensaje'] ?? $respuesta_ara['respuesta'] ?? 'sin respuesta');
        }
    }

    responder(200, [
        'status'          => 'success',
        'message'         => $autochequeo_real
            ? 'Nota Lista para procesar (autochequeo < ' . UMBRAL_ITEMS . ' ítems desde Mesa 0).'
            : 'Chequeo registrado correctamente.',
        'es_nota_menor_3' => $es_nota_menor_3,
        'es_nota_mayor_3' => !$es_nota_menor_3,   // compatibilidad con el frontend previo
        'total_items'     => $total_items,
        'autochequeo'     => $autochequeo_real,
        'mesa'            => $mesa,
        'hora'            => $hora,
        'sond_finaliza'   => '/sond/finaliza.mp',
        'integridad_ok'   => $validacion['ok'],
        'discrepancias'   => $validacion['discrepancias'],
        'registro'        => $registro,
        'responsable'     => $responsable,
        'monto'           => $monto,
        'respuesta_ara'   => $respuesta_ara,
        'advertencias'    => $advertencias,
    ]);

} catch (Throwable $e) {
    responder(500, [
        'status'  => 'error',
        'mensaje' => 'Error en chequeo/registro.php: ' . $e->getMessage(),
    ]);
}
