<?php

declare(strict_types=1);

namespace App\Services;

use PDOException;

/**
 * Excepción específica del wrapper de conexión a SQL Server (Fase 2.5).
 *
 * Hereda de PDOException para que TODOS los `catch (PDOException)` existentes
 * de las Skills/Tools sigan degradando con elegancia (no hay que modificar la
 * lógica de negocio). Distingue dos escenarios:
 *
 *   1. Query timeout: el servidor SQL Server cortó la consulta a los
 *      `queryTimeout` segundos (SQLSTATE S1T00 / SQL_ATTR_QUERY_TIMEOUT).
 *      Mensaje: "Query timeout after {n}s".
 *   2. Circuit breaker de procedimiento almacenado: sp_* inexistente (error
 *      2812) que falló ≥3 veces en 60s → circuito abierto 300s. Mensaje:
 *      "SP {nombre} 2812 ... circuito abierto".
 *
 * El código SQLSTATE se conserva en getCode() para que la auditoría de red
 * del adaptador Tecnología (SQLSTATE 08S01/08001/2812...) siga clasificando
 * igual que antes.
 */
final class ConnectionWrapperException extends PDOException
{
    /** SQLSTATE de timeout de consulta ODBC/SQL Server. */
    public const SQLSTATE_TIMEOUT = 'S1T00';

    /** Error de procedimiento almacenado inexistente en SQL Server. */
    public const ERROR_SP_INEXISTENTE = '2812';

    /**
     * @param string          $message   Mensaje descriptivo del fallo.
     * @param string          $sqlstate  SQLSTATE a conservar en getCode().
     * @param PDOException|null $previous Excepción original (si existe).
     */
    public function __construct(string $message, string $sqlstate = self::SQLSTATE_TIMEOUT, ?PDOException $previous = null)
    {
        parent::__construct($message);
        $this->code = $sqlstate;
        if ($previous !== null) {
            $this->errorInfo = $previous->errorInfo;
        }
    }
}
