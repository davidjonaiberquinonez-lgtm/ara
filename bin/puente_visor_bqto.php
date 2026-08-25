<?php
declare(strict_types=1);

/**
 * Puente HTTP para el visor legacy LOCAL de BQTO (192.168.4.148:8000/visor/,
 * que ese Apache solo expone en 127.0.0.1 de la máquina de BQTO — nunca en su
 * IP de red, por eso es invisible desde S/C incluso por Tailscale directo).
 *
 * Este script corre DENTRO de la máquina de BQTO (accesible vía Tailscale,
 * 100.116.126.99) y reenvía las consultas a "localhost:8000" ahí mismo — el
 * mismo truco que evita que el Apache detecte la IP de origen como ajena a
 * BQTO y filtre los datos. Sin este puente, el visor solo muestra el
 * subconjunto de notas de la sede desde la que se conecta (verificado en
 * vivo, v4.53): la MISMA ruta física (ej. "CARABOBO - ALTA PUERTO CABELLO")
 * trae 6 notas S/C consultada desde S/C, pero trae 6 notas S/C + 20 notas
 * BQTO reales (72163xxx) consultada desde BQTO — es la fuente real de rutas
 * para BQTO que se estuvo buscando, no `zona.zon_des` de Profit.
 *
 * NUNCA escribe nada — solo reenvía GET/POST de solo-lectura hacia el
 * Apache local. No expone confirmar_registro/cierre de despacho.
 *
 * Arranque:
 *   php -S 0.0.0.0:5099 bin/puente_visor_bqto.php
 * (dejar corriendo como tarea programada al inicio de sesión, igual que los
 * watchdogs del proyecto — ver bin/watchdog_*.ps1 como referencia de patrón)
 *
 * Endpoints:
 *   GET  /catalogo            -> {status, html}  (index.php crudo)
 *   POST /lista {responsable, ruta} -> {status, html} (lista.php ya logueado)
 */

const VISOR_BASE = 'http://localhost:8000/visor';
const TIMEOUT_S = 15;

header('Content-Type: application/json; charset=utf-8');

function responder(array $payload, int $http = 200): never
{
    http_response_code($http);
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function curl_get(string $url, ?string $cookieFile = null): string
{
    $ch = curl_init($url);
    $opts = [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => TIMEOUT_S,
    ];
    if ($cookieFile !== null) {
        $opts[CURLOPT_COOKIEJAR] = $cookieFile;
        $opts[CURLOPT_COOKIEFILE] = $cookieFile;
    }
    curl_setopt_array($ch, $opts);
    $resp = curl_exec($ch);
    if ($resp === false) {
        $err = curl_error($ch);
        curl_close($ch);
        throw new RuntimeException("GET $url falló: $err");
    }
    curl_close($ch);
    return (string) $resp;
}

function curl_post(string $url, array $datos, ?string $cookieFile = null): string
{
    $ch = curl_init($url);
    $opts = [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => TIMEOUT_S,
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => http_build_query($datos),
    ];
    if ($cookieFile !== null) {
        $opts[CURLOPT_COOKIEJAR] = $cookieFile;
        $opts[CURLOPT_COOKIEFILE] = $cookieFile;
    }
    curl_setopt_array($ch, $opts);
    $resp = curl_exec($ch);
    if ($resp === false) {
        $err = curl_error($ch);
        curl_close($ch);
        throw new RuntimeException("POST $url falló: $err");
    }
    curl_close($ch);
    return (string) $resp;
}

$metodo = $_SERVER['REQUEST_METHOD'] ?? 'GET';
$uri = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/';

try {
    if ($uri === '/catalogo' && $metodo === 'GET') {
        $html = curl_get(VISOR_BASE . '/index.php');
        responder(['status' => 'success', 'html' => $html]);
    }

    if ($uri === '/lista' && $metodo === 'POST') {
        $body = json_decode(file_get_contents('php://input') ?: '{}', true) ?: [];
        $responsable = trim((string) ($body['responsable'] ?? ''));
        $ruta = trim((string) ($body['ruta'] ?? ''));
        if ($responsable === '' || $ruta === '') {
            responder(['status' => 'error', 'mensaje' => 'Falta responsable o ruta'], 400);
        }

        $cookieFile = tempnam(sys_get_temp_dir(), 'puente_bqto_');
        try {
            // Paso 1: iniciar guía (fija la sesión PHP a esta ruta).
            curl_post(VISOR_BASE . '/index.php', [
                'responsable' => $responsable,
                'ruta' => $ruta,
                'consulta' => 'Empezar',
            ], $cookieFile);

            // Paso 2: consultar lista.php en la MISMA sesión.
            $html = curl_get(VISOR_BASE . '/lista.php', $cookieFile);
            responder(['status' => 'success', 'html' => $html]);
        } finally {
            @unlink($cookieFile);
        }
    }

    responder(['status' => 'error', 'mensaje' => 'Ruta no encontrada: ' . $uri], 404);
} catch (\Throwable $e) {
    responder(['status' => 'error', 'mensaje' => $e->getMessage()], 502);
}
