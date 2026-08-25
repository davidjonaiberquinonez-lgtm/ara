<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain;

/**
 * Mapeo de la respuesta FINAL del agente hacia el controlador/frontend de ARA.
 *
 * Normaliza el resultado del motor (OpenCodeAgentEngine::run) en un JSON
 * estable para la interfaz, de modo que el frontend siempre reciba la misma
 * forma sin importar el camino interno (éxito, error o límite de iteraciones).
 *
 * Formato estándar retornado al controlador:
 *
 * {
 *   "success":      true|false,          // operación global exitosa
 *   "respuesta":    "texto del agente",  // mensaje final para el usuario
 *   "error":        null|"detalle",      // error legible si success=false
 *   "iteraciones":  2,                   // iteraciones usadas en el bucle
 *   "max_agotado":  false,               // true si se cortó por límite
 *   "tiempo_ms":    1840,                // métrica (idéntica a IA_Logs)
 *   "status":       "OK"|"ERROR"|"MAX_ITERACIONES",
 *   "tool_calls":   [ { "name": "...", "arguments": {...} } ],
 *   "conversacion_id": 12|null           // id de IA_Conversaciones si aplica
 * }
 *
 * El JSON final se obtiene con toJson() (flags JSON_THROW_ON_ERROR).
 */
final class NvidiaBrainResponse
{
    /**
     * Construye el payload de respuesta estandarizado a partir del resultado
     * del motor.
     *
     * @param array<string,mixed> $motorResult Resultado de OpenCodeAgentEngine::run().
     * @param int|null            $conversacionId ID de conversación persistida.
     *
     * @return array<string,mixed> Forma normalizada para el frontend.
     */
    public static function fromEngine(array $motorResult, ?int $conversacionId = null): array
    {
        return [
            'success'        => (bool) ($motorResult['success'] ?? false),
            'respuesta'      => (string) ($motorResult['respuesta'] ?? ''),
            'error'          => $motorResult['error'] ?? null,
            'iteraciones'    => (int) ($motorResult['iteraciones'] ?? 1),
            'max_agotado'    => (bool) ($motorResult['max_agotado'] ?? false),
            'tiempo_ms'      => (int) ($motorResult['tiempo_ms'] ?? 0),
            'status'         => (string) ($motorResult['status'] ?? ($motorResult['error'] !== null ? 'ERROR' : 'OK')),
            'tool_calls'     => $motorResult['tool_calls'] ?? [],
            'conversacion_id'=> $conversacionId,
        ];
    }

    /**
     * Error controlado (cuando el fallo ocurre ANTES de poder orquestar el
     * motor: cliente no disponible, permisos inválidos, etc.).
     *
     * @return array<string,mixed>
     */
    public static function error(string $detalle, int $tiempoMs = 0): array
    {
        return [
            'success'        => false,
            'respuesta'      => '',
            'error'          => $detalle,
            'iteraciones'    => 0,
            'max_agotado'    => false,
            'tiempo_ms'      => $tiempoMs,
            'status'         => 'ERROR',
            'tool_calls'     => [],
            'conversacion_id'=> null,
        ];
    }

    /**
     * Respuesta de éxito directa (sin ejecución de motor).
     *
     * @return array<string,mixed>
     */
    public static function success(string $respuesta, array $extra = []): array
    {
        return array_merge([
            'success'        => true,
            'respuesta'      => $respuesta,
            'error'          => null,
            'iteraciones'    => 1,
            'max_agotado'    => false,
            'tiempo_ms'      => 0,
            'status'         => 'OK',
            'tool_calls'     => [],
            'conversacion_id'=> null,
        ], $extra);
    }

    /**
     * Serializa la forma normalizada a JSON string.
     *
     * @param array<string,mixed> $payload
     *
     * @throws \JsonException Si el payload no es serializable.
     */
    public static function toJson(array $payload): string
    {
        return json_encode($payload, NvidiaBrainClient::JSON_FLAGS);
    }
}
