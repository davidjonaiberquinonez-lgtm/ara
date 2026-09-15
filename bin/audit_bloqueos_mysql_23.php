<?php

declare(strict_types=1);
declare(ticks=1);

// ═══════════════════════════════════════════════════════════════════════
// AUDITOR DE BLOQUEOS MySQL/XAMPP — 192.168.4.23 (v1, 08/09)
// -----------------------------------------------------------------------
// A pedido del usuario: además de sql_kill_switch.php (que ya mata
// bloqueadores del SQL Server 192.168.4.20, sin importar de qué IP venga
// la conexión — eso YA cubre "si algo de .23 bloquea al .20, se mata ahí",
// sin tocar este archivo), este script AUDITA por separado los bloqueos
// propios de InnoDB dentro del MySQL/MariaDB que corre en 192.168.4.23
// (XAMPP, bases "ocr_scanner" y "retenciones").
//
// REGLA DURA, a nivel de código, no solo de lógica: este archivo NUNCA
// ejecuta KILL QUERY / KILL CONNECTION contra 192.168.4.23. No existe esa
// sentencia en ningún lado de este archivo — es solo lectura
// (information_schema.innodb_lock_waits/innodb_trx). Si algún día hace
// falta matar algo en .23, eso va en un script aparte, a pedido explícito,
// nunca acá.
//
// "Entrada"/"salida" de bloqueos: compara la foto actual contra la última
// guardada (data/estado_bloqueos_mysql_23.json) — loguea como ENTRADA los
// bloqueos que no estaban antes, y como SALIDA los que estaban antes y ya
// no están (se resolvieron solos o el cliente se desconectó).
//
// Uso:
//   php bin/audit_bloqueos_mysql_23.php
// ═══════════════════════════════════════════════════════════════════════

$aB_maxS = (int) (getenv('ARA_CLI_MAX_S') ?: '5');
$aB_maxS = max(3, min(600, $aB_maxS));
set_time_limit($aB_maxS);
ini_set('max_execution_time', (string) $aB_maxS);
ini_set('memory_limit', '64M');

$aB_inicio = microtime(true);
register_tick_function(static function () use ($aB_inicio, $aB_maxS): void {
    if ((microtime(true) - $aB_inicio) > $aB_maxS) {
        if (defined('STDERR')) {
            fwrite(STDERR, '{"success":false,"error":"audit_bloqueos_mysql_23: timeout preventivo (' . $aB_maxS . 's)."}');
        }
        exit(1);
    }
});

function aB_emitir(array $payload, int $exitCode = 0): never
{
    $json = json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    echo ($json === false ? '{"success":false,"error":"Salida no serializable"}' : $json) . PHP_EOL;
    exit($exitCode);
}

// ── 1) Conexión de SOLO LECTURA a 192.168.4.23 ───────────────────────────
// Mismo host/usuario que ya usa bd_ocr.py (Servidor de Retenciones) —
// root sin clave, confirmado en vivo (08/09) contra ese XAMPP real.
$aB_host = getenv('XAMPP_MYSQL_HOST') ?: '192.168.4.23';
$aB_user = getenv('XAMPP_MYSQL_USER') ?: 'root';
$aB_pass = getenv('XAMPP_MYSQL_PASSWORD') ?: '';

try {
    $aB_pdo = new PDO(
        "mysql:host={$aB_host};port=3306;charset=utf8mb4",
        $aB_user,
        $aB_pass,
        [PDO::ATTR_TIMEOUT => 4, PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]
    );
} catch (Throwable $e) {
    aB_emitir(['success' => false, 'error' => 'No se pudo conectar a ' . $aB_host . ': ' . $e->getMessage()], 1);
}

// ── 2) Bloqueos InnoDB activos AHORA MISMO ───────────────────────────────
// information_schema.innodb_lock_waits (estilo MariaDB/MySQL 5.7, sigue
// disponible en esta MariaDB 10.4 — confirmado en vivo) en vez de
// performance_schema.data_lock_waits (solo MySQL 8+, no aplica acá).
$aB_sql = "SELECT
        w.requesting_trx_id, w.blocking_trx_id,
        rt.trx_mysql_thread_id AS waiting_thread, rt.trx_query AS waiting_query,
        TIMESTAMPDIFF(SECOND, rt.trx_wait_started, NOW()) AS espera_seg,
        bt.trx_mysql_thread_id AS blocking_thread, bt.trx_query AS blocking_query,
        bp.HOST AS blocking_host, bp.DB AS blocking_db
    FROM information_schema.innodb_lock_waits w
    JOIN information_schema.innodb_trx rt ON rt.trx_id = w.requesting_trx_id
    JOIN information_schema.innodb_trx bt ON bt.trx_id = w.blocking_trx_id
    LEFT JOIN information_schema.processlist bp ON bp.ID = bt.trx_mysql_thread_id";

try {
    $aB_filas = $aB_pdo->query($aB_sql)->fetchAll(PDO::FETCH_ASSOC);
} catch (Throwable $e) {
    aB_emitir(['success' => false, 'error' => 'Fallo al listar bloqueos: ' . $e->getMessage()], 1);
}

$aB_actual = [];
foreach ($aB_filas as $aB_f) {
    // clave estable: par (thread que espera, thread que bloquea) — el
    // trx_id se recicla, el par identifica el MISMO bloqueo entre pasadas.
    $aB_clave = $aB_f['waiting_thread'] . '->' . $aB_f['blocking_thread'];
    $aB_actual[$aB_clave] = [
        'waiting_thread'  => (int) $aB_f['waiting_thread'],
        'blocking_thread' => (int) $aB_f['blocking_thread'],
        'espera_seg'      => (int) $aB_f['espera_seg'],
        'blocking_host'   => (string) ($aB_f['blocking_host'] ?? ''),
        'blocking_db'     => (string) ($aB_f['blocking_db'] ?? ''),
        'waiting_query'   => (string) ($aB_f['waiting_query'] ?? ''),
        'blocking_query'  => (string) ($aB_f['blocking_query'] ?? ''),
    ];
}

// ── 3) Comparar contra la foto anterior → detectar entrada/salida ────────
$aB_estadoFile = __DIR__ . '/../ara/ARA_Brain/data/estado_bloqueos_mysql_23.json';
$aB_anterior = [];
if (is_file($aB_estadoFile)) {
    $aB_anterior = json_decode((string) file_get_contents($aB_estadoFile), true) ?: [];
}

$aB_entradas = [];
$aB_salidas = [];
foreach ($aB_actual as $aB_clave => $aB_datos) {
    if (!isset($aB_anterior[$aB_clave])) {
        $aB_entradas[] = $aB_datos;
    }
}
foreach ($aB_anterior as $aB_clave => $aB_datos) {
    if (!isset($aB_actual[$aB_clave])) {
        $aB_salidas[] = $aB_datos;
    }
}

@file_put_contents($aB_estadoFile, json_encode($aB_actual, JSON_UNESCAPED_UNICODE));

// ── 4) Log — archivo propio, separado del kill_switch_*.txt de .20 ───────
$aB_logDir = __DIR__ . '/../logs';
if (!is_dir($aB_logDir)) {
    @mkdir($aB_logDir, 0777, true);
}
$aB_logFile = $aB_logDir . '/bloqueos_mysql_23_' . date('Y-m-d') . '.txt';
$aB_lineas = [];
foreach ($aB_entradas as $aB_e) {
    $aB_lineas[] = sprintf(
        '%s | ENTRADA | thread %d bloqueado por thread %d (host %s, db %s) | espera %ds | query esperando: %s',
        date('Y-m-d H:i:s'),
        $aB_e['waiting_thread'],
        $aB_e['blocking_thread'],
        $aB_e['blocking_host'],
        $aB_e['blocking_db'],
        $aB_e['espera_seg'],
        mb_substr($aB_e['waiting_query'], 0, 200)
    );
}
foreach ($aB_salidas as $aB_s) {
    $aB_lineas[] = sprintf(
        '%s | SALIDA  | thread %d ya no está bloqueado por thread %d (se resolvió o se desconectó)',
        date('Y-m-d H:i:s'),
        $aB_s['waiting_thread'],
        $aB_s['blocking_thread']
    );
}
if ($aB_lineas === []) {
    $aB_lineas[] = date('Y-m-d H:i:s') . ' | Scan OK, sin cambios (' . count($aB_actual) . ' bloqueo(s) activo(s))';
}
@file_put_contents($aB_logFile, implode(PHP_EOL, $aB_lineas) . PHP_EOL, FILE_APPEND | LOCK_EX);

aB_emitir([
    'success'    => true,
    'activos'    => count($aB_actual),
    'entradas'   => count($aB_entradas),
    'salidas'    => count($aB_salidas),
    'log'        => $aB_logFile,
    'elapsed_ms' => (int) ((microtime(true) - $aB_inicio) * 1000),
], 0);
