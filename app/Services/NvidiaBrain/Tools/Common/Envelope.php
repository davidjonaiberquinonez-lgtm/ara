<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Common;

/**
 * Envelope de respuesta JSON estándar de la Fase 4.17 (ajuste de adaptadores).
 *
 * Todos los tools/adaptadores DEBEN devolver (a nivel raíz del resultado):
 *   ['success' => bool, 'data' => array|object, 'message' => string,
 *    'timestamp' => string ISO 8601]
 *
 * Reglas globales de la orden:
 *  - Consulta vacía: success=true, data=[], message="Sin registros".
 *  - Se conserva la clave extra 'card' (tarjeta Markdown para el LLM, estándar
 *    v4.13) cuando el tool la emite; no forma parte del envelope formal.
 *  - Se conserva la clave 'ok' (retrocompatibilidad con contratos previos que
 *    usaban ok=true/false) junto a success.
 *  - Nunca se lanza excepción: los fallos controlados se serializan como
 *    success=false con message descriptivo y data=[].
 */
final class Envelope
{
    /**
     * Envelope de éxito.
     *
     * @param array<mixed>|object $data
     * @param string              $message Mensaje humano; si está vacío y data
     *                                      es [], se usa "Sin registros".
     */
    public static function ok(array|object $data, string $message = ''): array
    {
        if ($message === '') {
            $message = (is_array($data) && $data === []) ? 'Sin registros' : 'Consulta exitosa';
        }
        return [
            'success'   => true,
            'data'      => $data,
            'message'   => $message,
            'timestamp' => date('c'),
        ];
    }

    /**
     * Envelope de consulta vacía (success=true, data=[], message="Sin registros").
     */
    public static function vacio(string $message = 'Sin registros'): array
    {
        return [
            'success'   => true,
            'data'      => [],
            'message'   => $message,
            'timestamp' => date('c'),
        ];
    }

    /**
     * Envelope de error controlado (success=false, data=[], message legible).
     */
    public static function error(string $message, string $codigo = ''): array
    {
        $e = [
            'success'   => false,
            'data'      => [],
            'message'   => $message,
            'timestamp' => date('c'),
        ];
        if ($codigo !== '') {
            $e['codigo'] = $codigo;
        }
        return $e;
    }

    /**
     * Error HTTP 409 para el caso específico de estado inválido en
     * modificación/eliminación de notas (AD1). Se conserva en 'codigo'.
     */
    public static function conflicto(string $message): array
    {
        return [
            'success'   => false,
            'data'      => [],
            'message'   => $message,
            'codigo'    => 'HTTP_409_CONFLICTO',
            'timestamp' => date('c'),
        ];
    }

    /**
     * Añade la tarjeta Markdown (v4.13) y la clave de retrocompatibilidad 'ok'
     * al envelope, sin mutar el original.
     *
     * @param array<string,mixed> $envelope
     */
    public static function conCard(array $envelope, string $card): array
    {
        $envelope['card'] = $card;
        $envelope['ok'] = (bool) ($envelope['success'] ?? false);
        return $envelope;
    }
}
