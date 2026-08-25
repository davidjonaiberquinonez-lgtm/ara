<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Common;

/**
 * Trait compartido para Tools que consultan el servidor Flask local
 * (ara/ARA_Brain/ara_server.py) por HTTP — mismo patrón que
 * ConsultarNotaTool::getJson(), extraído aquí para no duplicarlo en cada
 * tool nueva que lee datos que solo viven en el SQLite de ARA_Brain
 * (movimientos_preparador, reportes_ubicacion, sesiones_ruta_activa, etc.),
 * a los que ninguna tool PHP se conecta directo por PDO.
 *
 * Solo lectura (GET). Sin shell, sin dependencias de framework (ext-curl).
 */
trait FlaskApiTrait
{
    private string $flaskBaseUrl;
    private int $flaskTimeoutS;

    /**
     * Resuelve la URL base: parámetro explícito > env ARA_ERP_URL > default
     * loopback. Debe llamarse desde el constructor de la tool.
     */
    private function initFlaskApi(string $baseUrl = '', int $timeoutS = 10): void
    {
        if ($baseUrl === '') {
            $envUrl = getenv('ARA_ERP_URL');
            // Fallback actualizado (24/08): ara_server.py se movió de 5000 a
            // 4050 (PC-NVR.exe tomaba el 5000 en esta máquina) — un fallback
            // desactualizado apuntaba a un puerto muerto en silencio si
            // ARA_ERP_URL no llegaba a estar seteada en el proceso que invoca
            // la tool.
            $baseUrl = is_string($envUrl) && trim($envUrl) !== '' ? trim($envUrl) : 'http://127.0.0.1:4050';
        }
        $this->flaskBaseUrl = rtrim($baseUrl, '/');
        $this->flaskTimeoutS = max(1, $timeoutS);
    }

    /**
     * GET a $path (relativo, con querystring ya armada) → array decodificado
     * del JSON de respuesta. Lanza RuntimeException en cualquier fallo
     * (curl no disponible, timeout, HTTP no-2xx, JSON inválido) para que la
     * tool lo capture y devuelva ['ok'=>false,'error'=>...] al LLM.
     *
     * @param list<string> $headersExtra Headers adicionales tipo 'X-API-Key: valor'
     *                                    — a pedido del conector del watchdog SQL
     *                                    (24/08), el primer consumidor de este
     *                                    trait que necesita autenticación.
     */
    private function flaskGetJson(string $path, array $headersExtra = []): mixed
    {
        $ch = curl_init();
        if ($ch === false) {
            throw new \RuntimeException('curl_init() falló: extensión cURL no disponible.');
        }
        try {
            curl_setopt_array($ch, [
                CURLOPT_URL            => $this->flaskBaseUrl . $path,
                CURLOPT_RETURNTRANSFER => true,
                CURLOPT_TIMEOUT        => $this->flaskTimeoutS,
                CURLOPT_CONNECTTIMEOUT => 5,
                CURLOPT_HTTPHEADER     => array_merge(['Accept: application/json'], $headersExtra),
            ]);
            $raw = curl_exec($ch);
            $status = (int) curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
            if ($raw === false) {
                throw new \RuntimeException('Petición a ' . $path . ' falló: ' . curl_error($ch));
            }
            if ($status < 200 || $status >= 300) {
                throw new \RuntimeException('Petición a ' . $path . ' devolvió HTTP ' . $status . '.');
            }
            $data = json_decode((string) $raw, true);
            if (!is_array($data)) {
                throw new \RuntimeException('Respuesta no-JSON del servidor en ' . $path . '.');
            }
            return $data;
        } finally {
            curl_close($ch);
        }
    }
}
