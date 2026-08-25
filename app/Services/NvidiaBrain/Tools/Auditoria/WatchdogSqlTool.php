<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Auditoria;

use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\FlaskApiTrait;

/**
 * Conector de auditoría hacia el watchdog anti-bloqueo SQL Server
 * (ara/ARA_Brain/watchdog_dashboard_server.py, puerto 5005) — expone lo que
 * ese watchdog viene detectando y matando en Profit (SQL Server) desde
 * v4.47: sesiones fantasma de KICKSERVER y SPIDs que bloquean a otras
 * sesiones por más de 15-30s.
 *
 * Tres vistas ('accion'):
 *   - resumen (default): totales, ranking de hosts/IPs culpables, huecos de
 *     ejecución, y — el caso más crítico de auditar — kills que el watchdog
 *     intentó pero NO surtieron efecto (proceso zombie que sigue vivo).
 *   - vivo: bloqueos activos AHORA MISMO en Profit (consulta en tiempo real).
 *   - spid: identidad + query real (actual e histórica) de un SPID puntual.
 */
final class WatchdogSqlTool implements AgentToolInterface
{
    use FlaskApiTrait;

    public function __construct()
    {
        $envUrl = getenv('WATCHDOG_URL');
        $baseUrl = is_string($envUrl) && trim($envUrl) !== '' ? trim($envUrl) : 'http://127.0.0.1:5005';
        $this->initFlaskApi($baseUrl, 10);
    }

    public function getName(): string
    {
        return 'watchdog_sql_auditoria';
    }

    public function getDescription(): string
    {
        return 'Audita el watchdog anti-bloqueo de SQL Server (Profit): sesiones ' .
            'fantasma y bloqueadoras que fue matando, quién las origina (host/IP/' .
            'programa), huecos donde el watchdog no corrió, y — el caso más ' .
            'crítico — intentos de KILL que fallaron (proceso zombie que sigue ' .
            'vivo). También permite ver bloqueos activos en vivo o el detalle de ' .
            'un SPID puntual.';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'accion' => [
                    'type' => 'string',
                    'enum' => ['resumen', 'vivo', 'spid'],
                    'description' => "'resumen' (default): estadísticas y ranking de culpables. " .
                        "'vivo': bloqueos activos ahora mismo. 'spid': detalle de un SPID puntual " .
                        "(requiere 'spid').",
                ],
                'spid' => ['type' => 'integer', 'description' => 'Número de SPID (solo con accion=spid).'],
                'host' => ['type' => 'string', 'description' => 'Filtra el resumen por host (substring, opcional).'],
                'ip' => ['type' => 'string', 'description' => 'Filtra el resumen por IP (substring, opcional).'],
                'desde' => ['type' => 'string', 'description' => "Filtra desde esta fecha 'YYYY-MM-DD HH:MM:SS' (opcional)."],
                'hasta' => ['type' => 'string', 'description' => "Filtra hasta esta fecha 'YYYY-MM-DD HH:MM:SS' (opcional)."],
            ],
            'required' => [],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $apiKey = (string) (getenv('WATCHDOG_API_KEY') ?: '');
        if ($apiKey === '') {
            return ['ok' => false, 'error' => 'Falta configurar WATCHDOG_API_KEY en el entorno de PHP.'];
        }
        $headers = ['X-API-Key: ' . $apiKey];

        $accion = (string) ($arguments['accion'] ?? 'resumen');

        try {
            if ($accion === 'vivo') {
                $data = $this->flaskGetJson('/api/vivo', $headers);
                return ['ok' => true, 'fuente' => 'watchdog_sql (en vivo)'] + (is_array($data) ? $data : []);
            }

            if ($accion === 'spid') {
                $spid = (int) ($arguments['spid'] ?? 0);
                if ($spid <= 0) {
                    return ['ok' => false, 'error' => "accion='spid' requiere un 'spid' válido."];
                }
                $data = $this->flaskGetJson('/api/spid/' . $spid . '/query', $headers);
                return ['ok' => true, 'fuente' => 'watchdog_sql (detalle SPID)'] + (is_array($data) ? $data : []);
            }

            $query = array_filter([
                'host' => (string) ($arguments['host'] ?? ''),
                'ip' => (string) ($arguments['ip'] ?? ''),
                'desde' => (string) ($arguments['desde'] ?? ''),
                'hasta' => (string) ($arguments['hasta'] ?? ''),
            ], static fn ($v) => $v !== '');
            $path = '/api/analisis' . ($query !== [] ? '?' . http_build_query($query) : '');
            $data = $this->flaskGetJson($path, $headers);
            return ['ok' => true, 'fuente' => 'watchdog_sql (resumen)'] + (is_array($data) ? $data : []);
        } catch (\Throwable $e) {
            return ['ok' => false, 'error' => 'No se pudo consultar el watchdog SQL: ' . $e->getMessage()];
        }
    }
}
