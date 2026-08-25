<?php

declare(strict_types=1);

declare(ticks=1);

// ═══════════════════════════════════════════════════════════════════════
// CANDADO ANTI-ZOMBI (Fase 2.5): suicidio a los N segundos (default 90s).
// ═══════════════════════════════════════════════════════════════════════
$t_maxS = (int) (getenv('ARA_CLI_MAX_S') ?: '90');
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
            fwrite(STDERR, '{"success":false,"error":"test: timeout preventivo (' . $t_maxS . 's)."}');
        }
        exit(1);
    }
});

/**
 * Prueba Fase 2.5 T2/T3 del ConnectionWrapper contra PRUEB25 real.
 *
 * Verifica:
 *   1. Query timeout: una SELECT con WAITFOR DELAY 35s DEBE fallar a ~30s
 *      con ConnectionWrapperException ("Query timeout after 30s").
 *   2. Bindings nombrados (:c) siguen funcionando (regresión ConsultarCliente).
 *   3. Circuit breaker: SP inexistente (2812) — 3 fallos abren el circuito.
 *
 * Uso:
 *   php bin/test_fase25_wrapper.php
 *
 * Códigos de salida:
 *   0   todo OK
 *   1   algún criterio falló
 *   2   error inesperado
 */

require_once __DIR__ . '/../app/Services/ConnectionWrapper.php';
require_once __DIR__ . '/../app/Services/ConnectionWrapperException.php';

use App\Services\ConnectionWrapper;
use App\Services\ConnectionWrapperException;

$fallos = [];

// ── 1) Query timeout WAITFOR 35s → 30s ─────────────────────────────────────
echo "== T2: Query timeout (WAITFOR 35s -> 30s) ==" . PHP_EOL;
$wrapper = new ConnectionWrapper();
$wrapper->setQueryTimeout(30);
echo 'queryTimeout configurado: ' . $wrapper->getQueryTimeout() . 's' . PHP_EOL;

$t0 = microtime(true);
try {
    // SELECT puro (isSelectOnly pasa) con WAITFOR DELAY.
    $wrapper->queryProfit("SELECT 1 AS v WHERE 1=1 WAITFOR DELAY '00:00:35'");
    $fallos[] = 'T2: la query NO falló con timeout (¡cuelga!).';
} catch (ConnectionWrapperException $e) {
    $dur = microtime(true) - $t0;
    echo 'ConnectionWrapperException: ' . $e->getMessage() . ' | sqlstate=' . $e->getCode() . ' | dur=' . round($dur, 1) . 's' . PHP_EOL;
    if ($dur < 25 || $dur > 35) {
        $fallos[] = "T2: duración $dur s no está en rango esperado (25-35s).";
    }
} catch (PDOException $e) {
    $fallos[] = 'T2: PDOException inesperada (no timeout): ' . $e->getMessage();
}

// ── 2) Regresión bindings nombrados :c ─────────────────────────────────────
echo PHP_EOL . "== T2: bindings nombrados (:c) ==" . PHP_EOL;
try {
    $w2 = new ConnectionWrapper();
    $filas = $w2->querySafe(
        "SELECT TOP 3 co_cli, cli_des FROM clientes WITH (NOLOCK) WHERE RTRIM(LTRIM(co_cli)) LIKE :c ORDER BY cli_des",
        [':c' => '%EV%'],
        0
    );
    echo 'Filas con :c = ' . count($filas) . PHP_EOL;
    foreach ($filas as $f) {
        echo '  ' . json_encode($f, JSON_UNESCAPED_UNICODE) . PHP_EOL;
    }
} catch (Throwable $e) {
    $fallos[] = 'T2 regresión :c: ' . get_class($e) . ' — ' . $e->getMessage();
}

// ── 3) Circuit breaker SP inexistente (2812) ───────────────────────────────
echo PHP_EOL . "== T3: Circuit breaker SP inexistente ==" . PHP_EOL;
$w3 = new ConnectionWrapper();
$spFalso = 'sp_Fase25_Inexistente';
echo 'spBloqueado inicial: ' . var_export($w3->spBloqueado($spFalso), true) . PHP_EOL;
$w3->registrarFalloSP($spFalso);
echo 'fallo 1 registrado' . PHP_EOL;
$w3->registrarFalloSP($spFalso);
echo 'fallo 2 registrado' . PHP_EOL;
$w3->registrarFalloSP($spFalso);
echo 'fallo 3 registrado' . PHP_EOL;
$bloqueado = $w3->spBloqueado($spFalso);
echo 'tras 3 fallos → spBloqueado: ' . var_export($bloqueado, true) . PHP_EOL;
if (!$bloqueado) {
    $fallos[] = 'T3: el circuito no se abrió tras 3 fallos.';
}
$w3->resetFalloSP($spFalso);
echo 'tras reset → spBloqueado: ' . var_export($w3->spBloqueado($spFalso), true) . PHP_EOL;

echo PHP_EOL;
if ($fallos !== []) {
    echo '== FALLO ==' . PHP_EOL;
    foreach ($fallos as $f) {
        echo '- ' . $f . PHP_EOL;
    }
    exit(1);
}
echo '== OK: T2 query timeout 30s + bindings :c + T3 circuit breaker ==' . PHP_EOL;
exit(0);
