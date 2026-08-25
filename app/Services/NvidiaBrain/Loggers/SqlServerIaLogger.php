<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Loggers;

use App\Services\NvidiaBrain\Contracts\IaLoggerInterface;

/**
 * Implementación de IaLoggerInterface sobre SQL Server vía PDO (dblib/sqlsrv).
 *
 * Inserta un registro en [dbo].[IA_Logs] de forma BEST-EFFORT: un fallo de
 * conexión o escritura NUNCA rompe la respuesta del agente (se loguea en
 * error_log() y se continúa). De ahí el try/catch interno y el contrato
 * `void`.
 *
 * Uso:
 *   $logger = new SqlServerIaLogger(
 *       host: '192.168.4.20',
 *       database: 'ProfitPlus',
 *       user: 'sa',
 *       pass: '***'
 *   );
 */
final class SqlServerIaLogger implements IaLoggerInterface
{
    /** @var \PDO|null Conexión perezosa. */
    private ?\PDO $pdo = null;

    private string $dsn;

    private string $user;

    private string $pass;

    /** @var bool Loguear fallos del logger en error_log(). */
    private bool $debug;

    /**
     * @param string $server    Host de SQL Server.
     * @param string $database  Base de datos.
     * @param string $user      Usuario SQL.
     * @param string $pass      Contraseña SQL.
     * @param bool   $debug     Logs de depuración del logger.
     */
    public function __construct(
        string $server,
        string $database,
        string $user,
        string $pass,
        bool $debug = false
    ) {
        $this->user = $user;
        $this->pass = $pass;
        $this->debug = $debug;
        // Prefiere PDO_SQLSRV (Windows) y degrada a PDO_DBLIB (Linux).
        $driver = in_array('sqlsrv', \PDO::getAvailableDrivers(), true) ? 'sqlsrv' : 'dblib';
        $this->dsn = $driver === 'sqlsrv'
            ? sprintf('sqlsrv:Server=%s;Database=%s', $server, $database)
            : sprintf('dblib:host=%s;dbname=%s', $server, $database);
    }

    public function log(array $datos): void
    {
        try {
            $stmt = $this->conexion()->prepare(
                'INSERT INTO [dbo].[IA_Logs]
                    ([ID_Conversacion], [ID_Usuario], [Modulo], [Herramienta_Ejecutada],
                     [Tiempo_Respuesta_MS], [Iteraciones_Usadas], [Status_Code], [Error_Detalle])
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)'
            );
            $stmt->execute([
                $datos['id_conversacion'] ?? null,
                (int) ($datos['id_usuario'] ?? 0),
                (string) ($datos['modulo'] ?? ''),
                isset($datos['herramienta_ejecutada']) && $datos['herramienta_ejecutada'] !== null
                    ? (string) $datos['herramienta_ejecutada']
                    : null,
                (int) ($datos['tiempo_ms'] ?? 0),
                (int) ($datos['iteraciones_usuadas'] ?? 1),
                (string) ($datos['status_code'] ?? 'OK'),
                isset($datos['error_detalle']) && $datos['error_detalle'] !== null
                    ? (string) $datos['error_detalle']
                    : null,
            ]);
        } catch (\Throwable $e) {
            if ($this->debug) {
                error_log('[IA_Logs] No se pudo registrar la métrica: ' . $e->getMessage());
            }
        }
    }

    private function conexion(): \PDO
    {
        if ($this->pdo !== null) {
            return $this->pdo;
        }
        $this->pdo = new \PDO($this->dsn, $this->user, $this->pass, [
            \PDO::ATTR_ERRMODE            => \PDO::ERRMODE_EXCEPTION,
            \PDO::ATTR_DEFAULT_FETCH_MODE => \PDO::FETCH_ASSOC,
            \PDO::ATTR_TIMEOUT            => 5,
        ]);
        return $this->pdo;
    }
}
