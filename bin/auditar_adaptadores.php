<?php

declare(strict_types=1);

/**
 * Auditoría estática de los 27 adaptadores departamentales (NvidiaBrain\Tools):
 * motor de conexión, si el pooling ODBC/sqlsrv está desactivado (hotfix
 * v4.17.1, evita SPID dormido en el servidor), si el cierre es explícito o
 * automático (destructor de ConnectionWrapper / GC de PDO no persistente), y
 * si hay loops sin guard de timeout. No abre ninguna conexión real — es
 * análisis de código fuente, cero riesgo.
 *
 * Uso: php bin/auditar_adaptadores.php [--json]
 */

require_once __DIR__ . '/../app/Services/NvidiaBrain/Tools/Common/CardBuilder.php';

use App\Services\NvidiaBrain\Tools\Common\CardBuilder;

$raiz = dirname(__DIR__) . '/app/Services/NvidiaBrain/Tools';

/** Archivo relativo => departamento (mismo orden que ToolRegistry::loadFromDirectory). */
$adaptadores = [
    'Almacen/BultoCerradoRouterTool.php'               => 'Almacen',
    'Almacen/BuscarInventarioTool.php'                 => 'Almacen',
    'Almacen/ConsultarNotaTool.php'                     => 'Almacen',
    'Almacen/DetectorErroresNotaTool.php'               => 'Almacen',
    'Almacen/FlujoNotasTiempoRealTool.php'              => 'Almacen',
    'Almacen/GestionNotasTool.php'                      => 'Almacen',
    'Almacen/GestionSuperEsteroideSearchTool.php'       => 'Almacen',
    'Almacen/MonitorModificacionesEliminacionesTool.php'=> 'Almacen',
    'Almacen/PythonStockBultoCerradoSkillTool.php'      => 'Almacen',
    'Almacen/RendimientoPreparadoresSmartAssignTool.php'=> 'Almacen',
    'Almacen/StockSurtidoPrioritarioTool.php'           => 'Almacen',
    'Almacen/TrazabilidadVidaUtilNotaTool.php'          => 'Almacen',
    'Auditoria/ConsultarSaldoClienteTool.php'           => 'Auditoria',
    'Auditoria/CrearReporteTool.php'                    => 'Auditoria',
    'Auditoria/DetectorMalsurtidoLogTool.php'           => 'Auditoria',
    'Auditoria/DiscrepanciaTrasladosTool.php'           => 'Auditoria',
    'Auditoria/PythonAuditoriaSkillTool.php'            => 'Auditoria',
    'Compras/PythonComprasSkillTool.php'                => 'Compras',
    'Compras/ReporteQuiebresComprasTool.php'            => 'Compras',
    'Despacho/CierreTransaccionalTool.php'              => 'Despacho',
    'Despacho/ConsultarClienteTool.php'                 => 'Despacho',
    'Despacho/PythonDespachoSkillTool.php'               => 'Despacho',
    'Orquestador/HermesChatTool.php'                    => 'Orquestador',
    'Recepcion/ConciliarFacturaTool.php'                => 'Recepcion',
    'Recepcion/LeerVoucherOcrTool.php'                  => 'Recepcion',
    'Recepcion/PythonRecepcionSkillTool.php'            => 'Recepcion',
    'Recepcion/RecepcionFacturaVisionTool.php'          => 'Recepcion',
];

function analizar(string $ruta): array
{
    $codigo = file_get_contents($ruta) ?: '';

    $usaConnectionWrapper = (bool) preg_match('/new ConnectionWrapper\(/', $codigo);
    $usaPdoDirecto = (bool) preg_match('/new PDO\(/', $codigo);
    $esSqlServer = (bool) preg_match('/sqlsrv:|odbc:Driver=\{.*SQL Server/i', $codigo);
    $esMysql = (bool) preg_match('/mysql:host=/', $codigo);
    $usaPython = (bool) preg_match('/proc_open\(|PythonSkillExecutor|PythonSkillBridge/', $codigo);

    if ($usaConnectionWrapper) {
        $engine = 'ConnectionWrapper';
        $poolingSeguro = true; // conectarProfit() ya fuerza ConnectionPooling=0 + ATTR_PERSISTENT=false
        $cierre = 'automático (destructor __destruct→forceDisconnect)';
    } elseif ($usaPdoDirecto && $esSqlServer) {
        $engine = 'PDO SQL Server directo';
        $poolingSeguro = (bool) preg_match('/ConnectionPooling=0/', $codigo)
            && (bool) preg_match('/ATTR_PERSISTENT\s*=>\s*false/', $codigo);
        $cierre = preg_match('/finally\s*\{/', $codigo)
            ? 'finally explícito + GC (no persistente)'
            : 'automático (GC, no persistente)';
    } elseif ($usaPdoDirecto && $esMysql) {
        $engine = 'PDO MySQL directo';
        $poolingSeguro = (bool) preg_match('/ATTR_PERSISTENT\s*=>\s*false/', $codigo);
        $cierre = 'automático (GC, no persistente)';
    } elseif ($usaPython) {
        $engine = 'Python (subprocess, sin conexión PHP)';
        $poolingSeguro = true; // n/a
        $cierre = 'n/a (no abre conexión DB en PHP)';
    } else {
        $engine = 'Sin conexión DB (lógica pura / delega en otro tool)';
        $poolingSeguro = true; // n/a
        $cierre = 'n/a';
    }

    $tieneLoop = (bool) preg_match('/while\s*\(\s*true\s*\)|for\s*\(\s*;;\s*\)/', $codigo);
    $tieneGuardTiempo = (bool) preg_match('/\$tope|max_execution_time|ARA_CLI_MAX_S/', $codigo);
    $loopRiesgo = $tieneLoop && !$tieneGuardTiempo;

    return [
        'engine'          => $engine,
        'pooling_seguro'  => $poolingSeguro,
        'cierre'          => $cierre,
        'tiene_loop'      => $tieneLoop,
        'loop_riesgo'     => $loopRiesgo,
    ];
}

$resultados = [];
foreach ($adaptadores as $rel => $depto) {
    $ruta = $raiz . '/' . $rel;
    $nombre = basename($rel, '.php');
    if (!is_file($ruta)) {
        $resultados[] = ['tool' => $nombre, 'depto' => $depto, 'error' => 'archivo no encontrado'];
        continue;
    }
    $r = analizar($ruta);
    $r['tool'] = $nombre;
    $r['depto'] = $depto;
    $resultados[] = $r;
}

$totalConexionDb = 0;
$totalPoolingInseguro = 0;
$totalLoopRiesgo = 0;
$ofensores = [];
foreach ($resultados as $r) {
    if (($r['engine'] ?? '') !== 'Sin conexión DB (lógica pura / delega en otro tool)'
        && ($r['engine'] ?? '') !== 'Python (subprocess, sin conexión PHP)') {
        $totalConexionDb++;
    }
    if (($r['pooling_seguro'] ?? true) === false) {
        $totalPoolingInseguro++;
        $ofensores[] = $r['tool'] . ' (' . $r['engine'] . ')';
    }
    if ($r['loop_riesgo'] ?? false) {
        $totalLoopRiesgo++;
        $ofensores[] = $r['tool'] . ' (loop sin guard)';
    }
}

if (in_array('--json', $argv, true)) {
    echo json_encode(['resultados' => $resultados, 'total' => count($resultados)], JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE) . "\n";
    exit(0);
}

$emoji = ($totalPoolingInseguro === 0 && $totalLoopRiesgo === 0) ? '✅' : '⚠️';
$card = CardBuilder::iniciar($emoji, 'AUDITORÍA DE CONEXIONES — 27 ADAPTADORES')
    ->seccion('Resumen')
    ->campo('Total adaptadores', count($resultados), 'numero')
    ->campo('Con conexión a BD', $totalConexionDb, 'numero')
    ->campo('Pooling inseguro (SPID dormido)', $totalPoolingInseguro, 'numero')
    ->campo('Loop sin guard de timeout', $totalLoopRiesgo, 'numero')
    ->campo('ConnectionWrapper (auto-cierre)', count(array_filter($resultados, static fn ($r) => ($r['engine'] ?? '') === 'ConnectionWrapper')), 'numero');

if ($ofensores !== []) {
    $card->seccion('Pendientes')->lista($ofensores);
} else {
    $card->seccion('Estado')->linea('Todos los adaptadores con pooling desactivado y sin loops fantasma.');
}
$card->footer('Análisis estático de código, sin conexiones reales — ' . date('Y-m-d H:i:s'));

echo $card->tarjeta() . "\n";
