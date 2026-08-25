<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Contracts;

/**
 * Contrato de persistencia de auditoría del agente (tabla IA_Logs).
 *
 * El motor registra, al final de cada ejecución, la métrica de tiempo (MS),
 * el estado (Status_Code) y los detalles del recorrido. Esta interfaz permite
 * inyectar cualquier implementación (PDO SQL Server, ADO, stub en tests)
 * sin acoplar el motor a un driver específico.
 */
interface IaLoggerInterface
{
    /**
     * Persiste un registro de auditoría del agente.
     *
     * @param array<string,mixed> $datos Campos esperados (todos opcionales
     *                                   salvo los que la implementación exija):
     *                                   id_conversacion, id_usuario, modulo,
     *                                   herramienta_ejecutada, tiempo_ms,
     *                                   iteraciones_usuadas, status_code,
     *                                   error_detalle.
     */
    public function log(array $datos): void;
}
