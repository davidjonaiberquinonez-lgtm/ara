<?php

declare(strict_types=1);

declare(ticks=1);

// ═══════════════════════════════════════════════════════════════════════
// CANDADO ANTI-ZOMBI (Orden v4.14): el script se suicida a los N segundos
// pase lo que pase. Default 300s (suite con LLM/webhook); override con env
// ARA_CLI_MAX_S; tope 600s. Evita procesos muertos en background.
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
    if ((microtime(true) - $t_inicio) > $t_maxS) {
        if (defined('STDERR')) {
            fwrite(STDERR, '{"success":false,"error":"test: timeout preventivo (' . $t_maxS . 's). Proceso abortado."}');
        }
        exit(1);
    }
});

/**
 * Suite de pruebas unificada de los 4 adaptadores NVIDIA BRAIN.
 *
 * Ejecuta en lote AlmacenAdapter, WhatsappClienteAdapter, FinanzasAdapter y
 * TecnologiaAdapter contra la infraestructura real (PRUEB25 en
 * 192.168.4.20:1433 vía PDO ODBC y Ollama local qwen2.5-coder:3b), mide la
 * latencia de cada caso y consolida un reporte JSON estructurado.
 *
 * CASOS DE PRUEBA:
 *   1. almacen   — nota con < 3 ítems: respuesta determinista fast-track
 *                  (mesa "0", estado AUTOCHEQUEO, audio /sond/finaliza.mp,
 *                  sin validación manual). No consulta BD.
 *   2. whatsapp  — validación de cliente con datos de prueba (RIF/Código +
 *                  últimos 4 dígitos): caso negativo → respuesta amigable;
 *                  caso positivo (cliente real con telefonos) → restricción
 *                  de contexto: data.cliente.co_cli == identificador validado.
 *   3. finanzas  — conciliación con PDO activo (SpyPDO registra eventos de
 *                  transacción): verifica beginTransaction → rollBack, que la
 *                  transacción quede cerrada y que la base NO se altere
 *                  (snapshot de la factura real antes/después).
 *   4. tecnologia— conexión PDO real a PRUEB25: EXEC sp_IA_Obtener_Metricas
 *                  con los 4 conjuntos de resultados y estatus_servicio='OK'.
 *
 * PRE-FLIGHT:
 *   - Extensiones críticas (pdo_odbc, curl, mbstring): si falta alguna →
 *     JSON {"status":"ERROR_DRIVER_MISSING",...,"code":500} y exit(500).
 *   - Sondeo Ollama /api/tags (timeout conexión 1s, tope 2s): si está
 *     offline → advertencia en el reporte preflight y en STDERR; la suite
 *     continúa (los adaptadores degradan a sus fallbacks deterministas).
 *
 * REPORTE JSON (consolidado al final, también en STDOUT legible):
 *   {status, timestamp (ISO 8601), preflight{pdo_odbc,ollama_local},
 *    summary{total,passed,failed,total_time_ms},
 *    results{almacen,whatsapp,finanzas,tecnologia}{passed,latency_ms,data}}
 *
 * Códigos de salida:
 *   0   los 4 adaptadores pasan
 *   1   al menos un adaptador falló
 *   2   error inesperado (Throwable no controlado)
 *   500 extensiones PHP requeridas faltantes
 *
 * Uso:
 *   php bin/test_adapters_suite.php
 */

use App\Services\NvidiaBrain\Adapters\AlmacenAdapter;
use App\Services\NvidiaBrain\Adapters\FinanzasAdapter;
use App\Services\NvidiaBrain\Adapters\TecnologiaAdapter;
use App\Services\NvidiaBrain\Adapters\WhatsappClienteAdapter;
use App\Services\NvidiaBrain\NvidiaBrainClient;

require_once __DIR__ . '/../app/Services/ConnectionWrapper.php';
require_once __DIR__ . '/../app/Services/ConnectionWrapperException.php';
require_once __DIR__ . '/../app/Services/NvidiaBrain/Contracts/AgentToolInterface.php';
require_once __DIR__ . '/../app/Services/NvidiaBrain/NvidiaBrainClient.php';
require_once __DIR__ . '/../app/Services/NvidiaBrain/Tools/Auditoria/ConsultarSaldoClienteTool.php';
require_once __DIR__ . '/../app/Services/NvidiaBrain/Tools/Recepcion/ConciliarFacturaTool.php';
require_once __DIR__ . '/../app/Services/NvidiaBrain/Security/CustomerAuthenticator.php';
require_once __DIR__ . '/../app/Services/NvidiaBrain/Adapters/BaseAdapter.php';
require_once __DIR__ . '/../app/Services/NvidiaBrain/Adapters/AlmacenAdapter.php';
require_once __DIR__ . '/../app/Services/NvidiaBrain/Adapters/FinanzasAdapter.php';
require_once __DIR__ . '/../app/Services/NvidiaBrain/Adapters/WhatsappClienteAdapter.php';
require_once __DIR__ . '/../app/Services/NvidiaBrain/Adapters/TecnologiaAdapter.php';

// ── Pre-Flight 1: extensiones críticas ─────────────────────────────────────
$faltantes = [];
foreach (['pdo_odbc', 'curl', 'mbstring'] as $ext) {
    if (!extension_loaded($ext)) {
        $faltantes[] = $ext;
    }
}
if ($faltantes !== []) {
    fwrite(STDERR, json_encode([
        'status'  => 'ERROR_DRIVER_MISSING',
        'message' => 'Extensión PHP requerida no instalada: ' . implode(', ', $faltantes),
        'code'    => 500,
    ], JSON_UNESCAPED_UNICODE) . PHP_EOL);
    exit(500);
}

/**
 * Sondeo Ollama local (/api/tags): timeout de conexión 1s, tope absoluto 2s.
 */
function preflight_ollama_online(string $baseUrl): bool
{
    $ch = curl_init(rtrim($baseUrl, '/') . '/api/tags');
    if ($ch === false) {
        return false;
    }
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_CONNECTTIMEOUT => 1,
        CURLOPT_TIMEOUT        => 2,
        CURLOPT_NOSIGNAL       => true,
    ]);
    $res = curl_exec($ch);
    $http = (int) curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    curl_close($ch);
    return is_string($res) && $http >= 200 && $http < 500;
}

// ── Pre-Flight 2: Ollama local ─────────────────────────────────────────────
$OLLAMA_BASE = 'http://localhost:11434';
$ollamaOnline = preflight_ollama_online($OLLAMA_BASE);
$ollamaAviso = '';
if (!$ollamaOnline) {
    $ollamaAviso = 'Ollama Local no está respondiendo en localhost:11434 (advertencia; la suite continúa con fallbacks deterministas).';
    fwrite(STDERR, '[PreFlight] ⚠️ ' . $ollamaAviso . PHP_EOL);
    NvidiaBrainClient::blacklistEndpoint(
        $OLLAMA_BASE,
        'PreFlight: Ollama Local offline (fast-fail 1s)'
    );
}

/**
 * Sanitización UTF-8 recursiva (CP1252 → UTF-8, regla de proyecto).
 */
function sanitizar_utf8_recursivo(mixed $v): mixed
{
    if (is_string($v)) {
        $limpio = trim($v);
        if (mb_check_encoding($limpio, 'UTF-8')) {
            return $limpio;
        }
        $convertido = mb_convert_encoding($limpio, 'UTF-8', 'CP1252');
        return is_string($convertido) ? $convertido : $limpio;
    }
    if (is_array($v)) {
        foreach ($v as $k => $item) {
            $v[$k] = sanitizar_utf8_recursivo($item);
        }
    }
    return $v;
}

/**
 * Conexión PDO a PRUEB25 (sqlsrv si está disponible, si no ODBC "SQL Server").
 */
function conectar_profit(): PDO
{
    $host = getenv('PROFIT_SQL_HOST') ?: '192.168.4.20';
    $name = getenv('PROFIT_SQL_NAME') ?: 'PRUEB25';
    $user = getenv('PROFIT_SQL_USER') ?: 'profit';
    $pass = getenv('PROFIT_SQL_PASS') ?: 'profit';

    $drivers = PDO::getAvailableDrivers();
    $dsn = in_array('sqlsrv', $drivers, true)
        ? "sqlsrv:Server=$host,1433;Database=$name"
        : "odbc:Driver={SQL Server};Server=$host,1433;Database=$name";

    $opts = [
        PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_TIMEOUT            => 8,
    ];
    return new PDO($dsn, $user, $pass, $opts);
}

/**
 * PDO con bitácora de transacciones (verifica inicio/commit/rollback reales).
 */
final class SpyPDO extends PDO
{
    /** @var list<string> */
    public array $eventos = [];

    public function beginTransaction(): bool
    {
        $this->eventos[] = 'begin';
        return parent::beginTransaction();
    }

    public function commit(): bool
    {
        $this->eventos[] = 'commit';
        return parent::commit();
    }

    public function rollBack(): bool
    {
        $this->eventos[] = 'rollback';
        return parent::rollBack();
    }
}

/**
 * @return array{passed: bool, latency_ms: int, data: array<string, mixed>}
 */
function ejecutar_caso(string $nombre, callable $fn): array
{
    $inicio = hrtime(true);
    try {
        $r = $fn();
        $ms = (int) (round((hrtime(true) - $inicio) / 1e6));
        $data = is_array($r['data'] ?? null) ? $r['data'] : [];
        return [
            'passed'     => (bool) ($r['passed'] ?? false),
            'latency_ms' => $ms,
            'data'       => sanitizar_utf8_recursivo($data),
        ];
    } catch (Throwable $e) {
        $ms = (int) (round((hrtime(true) - $inicio) / 1e6));
        return [
            'passed'     => false,
            'latency_ms' => $ms,
            'data'       => [
                'detalle' => 'EXCEPCIÓN ' . get_class($e) . ': ' . $e->getMessage(),
            ],
        ];
    }
}

// ── 1) ALMACEN: fast-track < 3 ítems (determinista, sin BD) ────────────────
$testAlmacen = ejecutar_caso('almacen', static function (): array {
    $numeroNota = 'TEST-' . date('YmdHis');
    try {
        $pdo = conectar_profit();
        $stmt = $pdo->query(
            'SELECT TOP 1 [fact_num] FROM [not_ent] '
            . 'WHERE CAST([fec_emis] AS DATE) = CAST(GETDATE() AS DATE) '
            . 'AND [anulada] = 0 ORDER BY [fact_num] DESC'
        );
        $v = $stmt->fetchColumn();
        if (is_string($v) || is_numeric($v)) {
            $numeroNota = (string) $v;
        }
    } catch (Throwable) {
        // El caso no depende de la BD; se usa un número sintético.
    }

    $adapter = new AlmacenAdapter();
    $r = sanitizar_utf8_recursivo($adapter->procesar('preparar la nota', [
        'numero_nota' => $numeroNota,
        'items_count' => 2,
        'accion'      => 'chequeo',
    ]));
    $data = $r['data'] ?? [];

    $fallos = [];
    if (!($r['success'] ?? false)) {
        $fallos[] = 'success=false';
    }
    if (($data['mesa'] ?? null) !== '0') {
        $fallos[] = 'mesa != "0"';
    }
    if (($data['estado'] ?? null) !== 'AUTOCHEQUEO') {
        $fallos[] = 'estado != AUTOCHEQUEO';
    }
    if (($data['audio'] ?? null) !== '/sond/finaliza.mp') {
        $fallos[] = 'audio != /sond/finaliza.mp';
    }
    if (($data['modo'] ?? null) !== 'fast_track') {
        $fallos[] = 'modo != fast_track';
    }
    if (($data['validacion_manual'] ?? true) !== false) {
        $fallos[] = 'validacion_manual no es false';
    }
    if (trim((string) ($r['respuesta'] ?? '')) === '') {
        $fallos[] = 'respuesta vacía';
    }

    // Informativo: 3 ítems → PREPARADA (no afecta el PASS).
    $rManual = $adapter->procesar('preparar la nota', [
        'numero_nota' => $numeroNota,
        'items_count' => 3,
    ]);
    $estadoManual = ($rManual['data']['estado'] ?? '?');

    return [
        'passed' => $fallos === [],
        'data'   => [
            'detalle'         => $fallos === []
                ? sprintf(
                    'Nota %s: fast-track mesa 0/AUTOCHEQUEO/audio OK; 3 ítems → %s (manual).',
                    $numeroNota,
                    $estadoManual
                )
                : implode('; ', $fallos),
            'numero_nota'     => $numeroNota,
            'items_count'     => 2,
            'mesa'            => $data['mesa'] ?? null,
            'estado'          => $data['estado'] ?? null,
            'audio'           => $data['audio'] ?? null,
            'modo'            => $data['modo'] ?? null,
            'validacion_manual' => $data['validacion_manual'] ?? null,
            'manual_3_items'  => $estadoManual,
        ],
    ];
});

// ── 2) WHATSAPP: validación de cliente (negativo + restricción de contexto) ─
$testWhatsapp = ejecutar_caso('whatsapp', static function (): array {
    $pdo = conectar_profit();
    $adapter = new WhatsappClienteAdapter(dbProfit: $pdo);

    // Negativo: credenciales inexistentes → respuesta amigable, nunca vacía.
    $neg = sanitizar_utf8_recursivo($adapter->procesar('¿Cuál es mi saldo?', [
        'identificador'     => 'ZZ-999.999.999',
        'factor_secundario' => '0000',
    ]));
    $amigable = !($neg['success'] ?? true)
        && trim((string) ($neg['respuesta'] ?? '')) !== ''
        && (isset($neg['data']['formato_requerido'])
            || stripos((string) ($neg['respuesta'] ?? ''), 'verific') !== false);

    // Positivo: cliente real con telefonos poblado → restricción al co_cli.
    $positivo = true;
    $detalle = '';
    $coAutenticado = null;
    $clienteNombre = null;
    $stmt = $pdo->query(
        'SELECT TOP 1 [co_cli] AS co_cli, [rif] AS rif, [cli_des] AS nombre, '
        . '[telefonos] AS telefonos FROM [clientes] '
        . "WHERE [co_cli] <> '' AND LEN(LTRIM(RTRIM([telefonos]))) >= 8 "
        . 'ORDER BY [co_cli]'
    );
    $cliente = $stmt->fetch(PDO::FETCH_ASSOC);
    if (!is_array($cliente)) {
        $positivo = false;
        $detalle = 'Sin cliente de prueba con telefonos en PRUEB25';
    } else {
        $cliente = sanitizar_utf8_recursivo($cliente);
        $telef = (string) $cliente['telefonos'];
        $digitos = preg_replace('/\D/', '', $telef) ?? '';
        $factor = strlen($digitos) >= 4 ? substr($digitos, -4) : '';
        $pos = sanitizar_utf8_recursivo($adapter->procesar('Hola, quiero saber mi saldo', [
            'identificador'     => (string) $cliente['co_cli'],
            'factor_secundario' => $factor,
        ]));
        $coAutenticado = isset($pos['data']['cliente']['co_cli'])
            ? strtoupper(trim((string) $pos['data']['cliente']['co_cli']))
            : '';
        $clienteNombre = isset($pos['data']['cliente']['nombre'])
            ? (string) $pos['data']['cliente']['nombre']
            : null;
        $coEsperado = strtoupper(trim((string) $cliente['co_cli']));
        $positivo = ($pos['success'] ?? false) && $coAutenticado !== '' && $coAutenticado === $coEsperado;
        $detalle = sprintf(
            'cliente %s (%s): %s',
            (string) $cliente['co_cli'],
            (string) $cliente['nombre'],
            $positivo
                ? 'co_cli autenticado == identificador (restricción OK)'
                : 'falló restricción de contexto'
        );
    }

    $fallos = [];
    if (!$amigable) {
        $fallos[] = 'negativo: respuesta amigable no confirmada';
    }
    if (!$positivo) {
        $fallos[] = 'positivo: ' . ($detalle !== '' ? $detalle : 'no confirmado');
    }

    return [
        'passed' => $fallos === [],
        'data'   => [
            'detalle'          => $fallos === []
                ? 'negativo amigable OK; ' . $detalle
                : implode('; ', $fallos),
            'negativo_amigable' => $amigable,
            'cliente_autenticado' => $coAutenticado,
            'cliente_nombre'   => $clienteNombre,
            'restriccion_co_cli' => $positivo,
        ],
    ];
});

// ── 3) FINANZAS: transacción PDO con rollback y BD intacta ─────────────────
$testFinanzas = ejecutar_caso('finanzas', static function (): array {
    $spy = new SpyPDO(
        (in_array('sqlsrv', PDO::getAvailableDrivers(), true)
            ? 'sqlsrv:Server=192.168.4.20,1433;Database=PRUEB25'
            : 'odbc:Driver={SQL Server};Server=192.168.4.20,1433;Database=PRUEB25'),
        'profit',
        'profit'
    );
    $spy->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    $spy->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE, PDO::FETCH_ASSOC);

    // Snapshot de una factura real (referencia de "no alteración").
    $stmt = $spy->query('SELECT TOP 1 [fact_num] AS fact_num, [saldo] AS saldo, '
        . '[status] AS status FROM [factura] WHERE [anulada] = 0 ORDER BY [fact_num] DESC');
    $snap = $stmt->fetch(PDO::FETCH_ASSOC);
    $snap = is_array($snap) ? sanitizar_utf8_recursivo($snap) : null;

    $tablaExiste = (bool) $spy->query(
        "SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'FacturasDrogueria'"
    )->fetchColumn();

    // Número seguro: si FacturasDrogueria existe, jamás se toca una factura
    // real (se usa uno inexistente → "no encontrada" → rollback).
    $numeroPrueba = $tablaExiste
        ? 'TEST-SUITE-' . date('YmdHis')
        : (string) ($snap['fact_num'] ?? 'TEST-SUITE-' . date('YmdHis'));

    $adapter = new FinanzasAdapter(pdo: $spy);
    $r = sanitizar_utf8_recursivo($adapter->procesar('conciliar pago', [
        'numero_factura'     => $numeroPrueba,
        'monto_conciliado'   => 1234.56,
        'referencia_bancaria' => 'TEST-REF-' . date('YmdHis'),
    ]));

    // Re-lectura de la factura real: debe estar idéntica.
    $bdIntacta = true;
    if (is_array($snap) && isset($snap['fact_num'])) {
        $stmt2 = $spy->query(
            'SELECT [saldo] AS saldo, [status] AS status FROM [factura] '
            . 'WHERE [fact_num] = ' . (int) $snap['fact_num']
        );
        $ahora = $stmt2->fetch(PDO::FETCH_ASSOC);
        $ahora = is_array($ahora) ? sanitizar_utf8_recursivo($ahora) : null;
        $bdIntacta = is_array($ahora)
            && (string) $ahora['saldo'] === (string) $snap['saldo']
            && (string) $ahora['status'] === (string) $snap['status'];
    }

    $fallos = [];
    if (!in_array('begin', $spy->eventos, true)) {
        $fallos[] = 'beginTransaction no ejecutado';
    }
    if ($spy->inTransaction()) {
        $fallos[] = 'transacción quedó abierta';
    }
    if (trim((string) ($r['respuesta'] ?? '')) === '') {
        $fallos[] = 'respuesta vacía';
    }
    if (!($r['success'] ?? true)) {
        if (!in_array('rollback', $spy->eventos, true)) {
            $fallos[] = 'falló sin rollback en eventos';
        }
        if (stripos((string) ($r['respuesta'] ?? ''), 'revertida') === false) {
            $fallos[] = 'no informa operación revertida';
        }
    }
    if (!$bdIntacta) {
        $fallos[] = 'la factura real cambió (BD alterada)';
    }

    return [
        'passed' => $fallos === [],
        'data'   => [
            'detalle'            => $fallos === []
                ? sprintf(
                    'begin→rollback verificados (%s), transacción cerrada, BD intacta (factura %s%s).',
                    implode(',', $spy->eventos),
                    (string) ($snap['fact_num'] ?? '?'),
                    $tablaExiste ? '; FacturasDrogueria existe → número sintético' : ''
                )
                : implode('; ', $fallos),
            'eventos_transaccion' => $spy->eventos,
            'transaccion_cerrada' => !$spy->inTransaction(),
            'bd_intacta'          => $bdIntacta,
            'factura_referencia'  => $snap['fact_num'] ?? null,
            'numero_prueba'       => $numeroPrueba,
            'respuesta'           => $r['respuesta'] ?? null,
        ],
    ];
});

// ── 4) TECNOLOGÍA: sp_IA_Obtener_Metricas con PDO real ─────────────────────
$testTecnologia = ejecutar_caso('tecnologia', static function (): array {
    $pdo = conectar_profit();
    $adapter = new TecnologiaAdapter(pdo: $pdo);
    $r = sanitizar_utf8_recursivo($adapter->procesar('métricas del día'));
    $data = $r['data'] ?? [];
    $metricas = $data['metricas'] ?? [];

    $estatus = $metricas[0][0]['estatus_servicio'] ?? null;
    $notasDia = $metricas[1][0]['notas_del_dia'] ?? null;
    $conjuntos = (int) ($data['conjuntos'] ?? 0);

    $fallos = [];
    if (!($r['success'] ?? false)) {
        $fallos[] = 'success=false: ' . (string) ($r['error'] ?? '?');
    }
    if ($estatus !== 'OK') {
        $fallos[] = 'estatus_servicio != OK (' . var_export($estatus, true) . ')';
    }
    if ($conjuntos < 4) {
        $fallos[] = "se esperaban 4 conjuntos, hay $conjuntos";
    }
    if ($notasDia === null) {
        $fallos[] = 'métrica notas_del_dia ausente';
    }

    return [
        'passed' => $fallos === [],
        'data'   => [
            'detalle'          => $fallos === []
                ? sprintf(
                    '%d conjuntos de resultados, estatus_servicio=OK, notas del día=%s.',
                    $conjuntos,
                    (string) $notasDia
                )
                : implode('; ', $fallos),
            'estatus_servicio' => $estatus,
            'conjuntos'        => $conjuntos,
            'notas_del_dia'    => $notasDia,
            'base_datos'       => $metricas[0][0]['base_datos'] ?? null,
        ],
    ];
});

// ── Consolidación del reporte JSON ─────────────────────────────────────────
$results = [
    'almacen'    => $testAlmacen,
    'whatsapp'   => $testWhatsapp,
    'finanzas'   => $testFinanzas,
    'tecnologia' => $testTecnologia,
];

$passedCount = count(array_filter($results, static fn (array $r): bool => $r['passed']));
$totalMs = array_sum(array_map(static fn (array $r): int => $r['latency_ms'], $results));
$status = $passedCount === count($results) ? 'OK' : 'ERROR';

$reporte = [
    'status'    => $status,
    'timestamp' => (new DateTimeImmutable())->format('c'),
    'preflight' => [
        'pdo_odbc'    => extension_loaded('pdo_odbc'),
        'ollama_local'=> $ollamaOnline,
    ],
    'summary'   => [
        'total'          => count($results),
        'passed'         => $passedCount,
        'failed'         => count($results) - $passedCount,
        'total_time_ms'  => $totalMs,
    ],
    'results'   => $results,
];
if ($ollamaAviso !== '') {
    $reporte['preflight']['ollama_advertencia'] = $ollamaAviso;
}

$json = json_encode(
    $reporte,
    JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES
);
echo ($json === false ? '{"status":"ERROR","message":"json_encode falló"}' : $json) . PHP_EOL;

echo PHP_EOL . '== RESUMEN ==' . PHP_EOL;
echo 'PreFlight: pdo_odbc=' . ($reporte['preflight']['pdo_odbc'] ? 'si' : 'no')
    . ' | ollama_local=' . ($reporte['preflight']['ollama_local'] ? 'ONLINE' : 'OFFLINE') . PHP_EOL;
foreach ($results as $nombre => $r) {
    echo str_pad($nombre, 10) . ($r['passed'] ? 'PASS' : 'FAIL')
        . ' | ' . $r['latency_ms'] . 'ms | ' . ($r['data']['detalle'] ?? '') . PHP_EOL;
}
echo $status . ' | ' . $passedCount . '/' . count($results) . PHP_EOL;

exit($passedCount === count($results) ? 0 : 1);
