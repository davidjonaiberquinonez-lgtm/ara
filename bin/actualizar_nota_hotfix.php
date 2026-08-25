<?php
/**
 * actualizar_nota.php — HOTFIX ANTI-ZOMBI v2.2
 * BD: barquisimeto (MySQL legacy real 192.168.4.148) — esquema rep_not real
 * (cod_nota/estatus) + gestion + asignaciones_preparacion.
 */

declare(strict_types=1);

declare(ticks=1);

// ═══════════════════════════════════════════════════════════════
// CANDADO ANTI-ZOMBI (HOTFIX v4.17.1): el script se suicida a los
// N segundos pase lo que pase. Default 30s (endpoint HTTP); override
// con env ARA_CLI_MAX_S; tope 600s. Watchdog por tick: si excede 240s
// aborta. Shutdown cierra la conexión MySQL SIEMPRE (no deja dormidas).
// ═══════════════════════════════════════════════════════════════
$an_maxS = (int) (getenv('ARA_CLI_MAX_S') ?: '30');
$an_maxS = max(5, min(600, $an_maxS));
$an_dryRun = getenv('ARA_DRY_RUN') === '1';
set_time_limit($an_maxS);
ini_set('max_execution_time', (string) $an_maxS);
ini_set('memory_limit', '128M');

$an_inicio = microtime(true);
register_shutdown_function(static function (): void {
    // Cierra la conexión MySQL si el flujo terminó por exit/die inesperado.
    if (isset($GLOBALS['pdo']) && $GLOBALS['pdo'] !== null) {
        try {
            if ($GLOBALS['pdo']->inTransaction()) {
                $GLOBALS['pdo']->rollBack();
            }
        } catch (Throwable $ignore) {
        }
        $GLOBALS['pdo'] = null;
    }
    gc_collect_cycles();
});
register_tick_function(static function () use ($an_inicio, $an_maxS): void {
    if ((microtime(true) - $an_inicio) > 240) {
        error_log('HOTFIX_KILL: actualizar_nota_hotfix.php excedió tiempo (' . $an_maxS . 's). Proceso abortado.');
        exit(1);
    }
});

// ═══════════════════════════════════════════════════════════════
// HOTFIX ANTI-ZOMBI — INICIO
// ═══════════════════════════════════════════════════════════════
ini_set('mysql.allow_persistent', 'Off');
ini_set('mysqli.allow_persistent', 'Off');

header('Content-Type: application/json; charset=utf-8');

// ═══════════════════════════════════════════════════════════════
// CONFIGURACIÓN — barquisimeto (MySQL legacy real)
// ═══════════════════════════════════════════════════════════════
$DB_HOST = '192.168.4.148';
$DB_PORT = '3306';
$DB_NAME = 'barquisimeto';
$DB_USER = 'jonaiber';
$DB_PASS = 'Crist2026.';
$DB_CHARSET = 'utf8mb4';

// ═══════════════════════════════════════════════════════════════
// CONEXIÓN SEGURA (no persistente)
// ═══════════════════════════════════════════════════════════════
function db_conectar() {
    global $DB_HOST, $DB_PORT, $DB_NAME, $DB_USER, $DB_PASS, $DB_CHARSET;
    // connect_timeout en el DSN: fast-fail de red (5s) — no cuelga si MySQL no responde.
    $dsn = "mysql:host={$DB_HOST};port={$DB_PORT};dbname={$DB_NAME};charset={$DB_CHARSET};connect_timeout=5";
    $opts = [
        PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES   => false,
        PDO::ATTR_PERSISTENT         => false,
        PDO::ATTR_TIMEOUT            => 5,
    ];
    return new PDO($dsn, $DB_USER, $DB_PASS, $opts);
}

function json_resp($data, $code = 200) {
    http_response_code($code);
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
}

// ═══════════════════════════════════════════════════════════════
// FLUJO PRINCIPAL — try/finally garantiza cierre SIEMPRE
// ═══════════════════════════════════════════════════════════════
$pdo = null;

try {
    $pdo = db_conectar();

    $codigoBarra      = isset($_POST['codigoBarra'])      ? trim($_POST['codigoBarra'])      : '';
    $numeroPreparador = isset($_POST['numeroPreparador']) ? trim($_POST['numeroPreparador']) : '';
    $cant_items       = isset($_POST['cant_items'])       ? trim($_POST['cant_items'])       : '';

    if (empty($codigoBarra)) {
        json_resp(['success' => false, 'message' => 'Código de barra requerido.']);
        exit;
    }

    // ═══════════════════════════════════════════════════════════
    // MODO 1: ASIGNACIÓN DE PREPARADOR
    // ═══════════════════════════════════════════════════════════
    if ($numeroPreparador !== '') {
        $pdo->beginTransaction();
        try {
            $stmt = $pdo->prepare("SELECT estatus FROM rep_not WHERE cod_nota = :codigo LIMIT 1");
            $stmt->execute([':codigo' => $codigoBarra]);
            $row = $stmt->fetch();

            if (!$row) {
                $pdo->rollBack();
                json_resp(['success' => false, 'message' => 'La nota no está registrada en la tabla rep_not.']);
                exit;
            }

            // Estados de proceso: la nota ya pasó por preparación/chequeo/embalaje.
            $an_estatus = $row['estatus'] ?? '';
            if (in_array($an_estatus, ['PREPARACION', 'CHEQUEO', 'CHEQUEADA', 'EMBALADA'], true)) {
                $pdo->rollBack();
                json_resp(['success' => false, 'message' => 'La nota ya fue registrada en el proceso de Preparación.']);
                exit;
            }

            if ($an_dryRun) {
                $pdo->rollBack();
                json_resp([
                    'success'   => true,
                    'message'   => 'DRY-RUN: datos listos para actualizar (sin persistir).',
                    'playAudio' => true,
                ]);
                exit;
            }

            // rep_not: solo marca estatus (no tiene columnas de preparador).
            $stmt = $pdo->prepare("
                UPDATE rep_not 
                SET estatus = 'PREPARACION'
                WHERE cod_nota = :codigo
            ");
            $stmt->execute([':codigo' => $codigoBarra]);

            // gestion: trazabilidad física real (num_prep = preparador).
            $stmt = $pdo->prepare("
                UPDATE gestion 
                SET num_prep     = :prep,
                    verifi_pre   = 'VERIFICADA',
                    tipo_perso   = 'PREPARADOR',
                    ubicacion    = 'PREPARACION',
                    hora         = NOW(),
                    cant_items   = :cant
                WHERE cd_barr = :codigo
            ");
            $stmt->execute([
                ':prep'   => $numeroPreparador,
                ':cant'   => $cant_items,
                ':codigo' => $codigoBarra,
            ]);

            // asignaciones_preparacion: registro de asignación real.
            $stmt = $pdo->prepare("
                INSERT INTO asignaciones_preparacion
                    (cod_nota, numero_trabajador, tamano_nota, asignado_en)
                VALUES
                    (:codigo, :prep, :cant, NOW())
            ");
            $stmt->execute([
                ':prep'   => $numeroPreparador,
                ':cant'   => $cant_items,
                ':codigo' => $codigoBarra,
            ]);

            $pdo->commit();
            json_resp([
                'success'  => true,
                'message'  => 'Datos actualizados correctamente.',
                'playAudio'=> true,
            ]);
            exit;

        } catch (Throwable $e) {
            if ($pdo->inTransaction()) {
                $pdo->rollBack();
            }
            throw $e;
        }
    }

    // ═══════════════════════════════════════════════════════════
    // MODO 2: CONSULTA DE NOTA
    // ═══════════════════════════════════════════════════════════
    $stmt = $pdo->prepare("SELECT estatus FROM rep_not WHERE cod_nota = :codigo LIMIT 1");
    $stmt->execute([':codigo' => $codigoBarra]);
    $repRow = $stmt->fetch();

    if (!$repRow) {
        json_resp(['success' => false, 'message' => 'La nota no está registrada en la tabla rep_not.']);
        exit;
    }

    if (in_array($repRow['estatus'] ?? '', ['PREPARACION', 'CHEQUEO', 'CHEQUEADA', 'EMBALADA'], true)) {
        json_resp(['success' => false, 'message' => 'La nota ya fue registrada en el proceso de Preparación.']);
        exit;
    }

    // Info cliente/cantidad desde gestion (la trazabilidad física real en MySQL).
    $stmt = $pdo->prepare("
        SELECT 
            COALESCE(NULLIF(TRIM(cli_des), ''), '') AS cli_des,
            COALESCE(NULLIF(TRIM(co_cli), ''), '')  AS co_cli,
            COALESCE(cant_items, 0) AS cantidad_items
        FROM gestion
        WHERE cd_barr = :codigo
        LIMIT 1
    ");
    $stmt->execute([':codigo' => $codigoBarra]);
    $notaInfo = $stmt->fetch();

    if (!$notaInfo) {
        $notaInfo = ['cli_des' => '', 'co_cli' => '', 'cantidad_items' => '0'];
    }

    // Nota: en MySQL legacy (barquisimeto) no existe detalle por ítem
    // (reng_nde/art son de SQL Server PRUEB25); el detalle se deja vacío.
    json_resp([
        'articulos' => [],
        'notaInfo'  => [
            'cantidad_items' => (string)$notaInfo['cantidad_items'],
            'co_cli'         => str_pad($notaInfo['co_cli'], 10, ' '),
            'cli_des'        => str_pad($notaInfo['cli_des'], 100, ' '),
        ],
    ]);
    exit;

} catch (Throwable $e) {
    error_log('[actualizar_nota.php] ERROR: ' . $e->getMessage());
    json_resp([
        'success' => false,
        'message' => 'Ocurrió un error al procesar la solicitud.',
    ], 500);
    exit;

} finally {
    if ($pdo !== null) {
        try {
            if ($pdo->inTransaction()) {
                $pdo->rollBack();
            }
        } catch (Throwable $ignore) {}
        $pdo = null;
    }
    gc_collect_cycles();
}
