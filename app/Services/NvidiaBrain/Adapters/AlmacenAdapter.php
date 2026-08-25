<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Adapters;

use App\Services\NvidiaBrain\NvidiaBrainClient;
use App\Services\NvidiaBrain\ToolRegistry;

/**
 * Adaptador del departamento de Almacén (picking, preparación y chequeo).
 *
 * Flujo:
 *   1. Valida los parámetros de la operación (numero_nota, items_count,
 *      accion); si faltan, lista los requeridos (criterio de aceptación).
 *   2. LÓGICA FAST-TRACK (< 3 ítems): cuando el conteo de ítems de la nota
 *      es menor a 3, asigna automáticamente:
 *        - mesa = "0" (Pasillo)
 *        - estado = "AUTOCHEQUEO"
 *        - marca de tiempo actual (ISO 8601)
 *        - audio de finalización /sond/finaliza.mp
 *      SIN requerir validación manual del chequeador.
 *   3. Con 3 o más ítems la nota queda "PREPARADA" y requiere validación
 *      manual en la mesa de chequeo.
 *
 * Las decisiones de negocio (mesa/estado/audio) son DETERMINISTAS y se
 * calculan antes de cualquier llamada al LLM: el modelo solo puede redactar
 * la respuesta (fraseoConLlama), nunca alterar la decisión operativa.
 */
final class AlmacenAdapter extends BaseAdapter
{
    /** Umbral de la regla fast-track: < UMBRAL_FAST_TRACK ítems → AUTOCHEQUEO. */
    public const UMBRAL_FAST_TRACK = 3;

    /** Mesa del Pasillo para el autochequeo. */
    public const MESA_AUTOCHEQUEO = '0';

    /** Audio de finalización del flujo de chequeo. */
    public const AUDIO_FINALIZA = '/sond/finaliza.mp';

    private bool $fraseoConLlama;

    /**
     * @param NvidiaBrainClient|null $client        Cliente LLM (Ollama local).
     * @param ToolRegistry|null      $registry      Registro de tools.
     * @param array<string,mixed>    $contexto      Contexto de sesión.
     * @param bool                   $fraseoConLlama true para que el LLM
     *                                               redacte la respuesta final
     *                                               (default false: velocidad
     *                                               operativa de las mesas).
     */
    public function __construct(
        ?NvidiaBrainClient $client = null,
        ?ToolRegistry $registry = null,
        array $contexto = [],
        bool $fraseoConLlama = false
    ) {
        parent::__construct($client, $registry, $contexto);
        $this->fraseoConLlama = $fraseoConLlama;
    }

    protected function getSystemPrompt(): string
    {
        return 'Eres el supervisor digital de almacén y empaque. Mantén las '
             . 'respuestas cortas, directas y enfocadas en la velocidad '
             . 'operativa de las mesas de chequeo.';
    }

    /**
     * {@inheritDoc}
     *
     * Contexto esperado:
     *   - numero_nota   (string, requerido) número de la nota de entrega.
     *   - items_count   (int, requerido)    conteo de ítems de la nota.
     *   - accion        (string, opcional)  'picking' | 'preparacion' | 'chequeo'.
     *   - preparador_id (string, opcional)  ID del preparador activo.
     */
    public function procesar(string $mensaje, array $contexto = []): array
    {
        $datos = array_merge($this->contexto, $contexto);

        $faltantes = $this->parametrosFaltantes(['numero_nota', 'items_count'], $datos);
        if ($faltantes !== []) {
            return $this->respuestaFaltanParametros(
                $faltantes,
                'procesar la nota en la mesa de chequeo'
            );
        }

        $numeroNota = trim((string) $datos['numero_nota']);
        $itemsCount = (int) $datos['items_count'];
        $accion = strtolower(trim((string) ($datos['accion'] ?? 'preparacion')));
        if ($accion === '') {
            $accion = 'preparacion';
        }
        $preparadorId = trim((string) ($datos['preparador_id'] ?? ''));
        $marcaTiempo = date('c'); // ISO 8601: marca de tiempo actual

        // ── Fast-Track: < 3 ítems → autochequeo sin validación manual ────────
        if ($itemsCount < self::UMBRAL_FAST_TRACK) {
            return $this->responder(
                $this->textoFastTrack(
                    $numeroNota,
                    $itemsCount,
                    $accion,
                    $marcaTiempo,
                    $preparadorId
                ),
                [
                    'accion'            => $accion,
                    'numero_nota'       => $numeroNota,
                    'items_count'       => $itemsCount,
                    'modo'              => 'fast_track',
                    'mesa'              => self::MESA_AUTOCHEQUEO,
                    'pasillo'           => 'Pasillo',
                    'estado'            => 'AUTOCHEQUEO',
                    'marca_tiempo'      => $marcaTiempo,
                    'audio'             => self::AUDIO_FINALIZA,
                    'validacion_manual' => false,
                    'preparador_id'     => $preparadorId !== '' ? $preparadorId : null,
                ]
            );
        }

        // ── Chequeo manual: 3 o más ítems → espera validación del chequeador ─
        $textoManual = sprintf(
            'La nota %s tiene %d ítems (>= %d) y requiere validación manual del '
            . 'chequeador. Estado: PREPARADA; mesa por asignar.',
            $numeroNota,
            $itemsCount,
            self::UMBRAL_FAST_TRACK
        );
        $data = [
            'accion'            => $accion,
            'numero_nota'       => $numeroNota,
            'items_count'       => $itemsCount,
            'modo'              => 'chequeo_manual',
            'mesa'              => null,
            'estado'            => 'PREPARADA',
            'marca_tiempo'      => $marcaTiempo,
            'audio'             => null,
            'validacion_manual' => true,
            'preparador_id'     => $preparadorId !== '' ? $preparadorId : null,
        ];
        return $this->responder(
            $this->fraseoConLlama ? $this->frasear($data, $textoManual) : $textoManual,
            $data
        );
    }

    /**
     * Texto determinista del fast-track (se usa siempre; el LLM solo lo
     * puede redactar en un formato alternativo, nunca cambiarlo).
     */
    private function textoFastTrack(string $numeroNota, int $itemsCount, string $accion, string $marcaTiempo, string $preparadorId): string
    {
        $texto = sprintf(
            'Nota %s: %d ítems (< %d) → FAST-TRACK AUTOCHEQUEO. Mesa %s '
            . '(Pasillo), estado AUTOCHEQUEO, %s.',
            $numeroNota,
            $itemsCount,
            self::UMBRAL_FAST_TRACK,
            self::MESA_AUTOCHEQUEO,
            $marcaTiempo
        );
        if ($preparadorId !== '') {
            $texto .= sprintf(' Preparador: %s.', $preparadorId);
        }
        $texto .= sprintf(' Finalización: %s. Sin validación manual.', self::AUDIO_FINALIZA);
        return $texto;
    }

    /**
     * Redacta la respuesta con el LLM; si no responde, usa el texto base.
     *
     * @param array<string,mixed> $datos
     */
    private function frasear(array $datos, string $textoBase): string
    {
        $res = $this->preguntar(
            'Operación de almacén resuelta. Datos: '
            . json_encode($datos, NvidiaBrainClient::JSON_FLAGS)
            . PHP_EOL . 'Redacta una respuesta corta y directa para el operador de la mesa.',
            [],
            'none',
            ['temperature' => 0.2, 'max_tokens' => 150]
        );
        return $res['success'] && $res['respuesta'] !== '' ? $res['respuesta'] : $textoBase;
    }
}
