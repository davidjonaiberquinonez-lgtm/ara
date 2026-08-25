<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Common;

/**
 * CardBuilder — Constructor único de Tarjetas Visuales Markdown (v4.13).
 *
 * Estándar de presentación de TODAS las skills del chat:
 *
 *   ┌─────────────────────────────────────────┐
 *   │  🎯 HEADER (título con emoji)           │
 *   ├─────────────────────────────────────────┤
 *   │  📋 BODY (datos organizados)            │
 *   │     • Parámetros con ancho fijo         │
 *   │     • Emojis semánticos por tipo dato   │
 *   ├─────────────────────────────────────────┤
 *   │  ⚡ FOOTER (metadata: tiempo, fuente)   │
 *   └─────────────────────────────────────────┘
 *
 * Reglas:
 *  - ANCHO máximo 80 caracteres por línea (corte por palabras, no corta a la
 *    mitad de un carácter multibyte).
 *  - LARGO máximo 20 líneas por tarjeta; el exceso se resume con
 *    "+N líneas omitidas" (paginación futura queda del lado del chat).
 *  - Nunca devuelve JSON crudo al usuario: execute() siempre expone 'card'.
 *
 * Uso típico:
 *   $card = CardBuilder::iniciar('📦', 'RESULTADOS DE INVENTARIO')
 *       ->seccion('Productos')
 *       ->campo('CÓDIGO', 'MD02009')
 *       ->campo('STOCK', 488, 'numero')
 *       ->lista($productos)
 *       ->footer('Fuente: Profit PRUEB25 (NOLOCK)')
 *       ->tarjeta();
 */
final class CardBuilder
{
    public const ANCHO_MAX = 80;
    public const LINEAS_MAX = 20;

    /** @var array<int,string> */
    private array $lineas = [];

    public static function iniciar(string $emoji, string $titulo): self
    {
        $cb = new self();
        $cb->lineas[] = $emoji . ' *' . self::cortar($titulo, self::ANCHO_MAX - 4) . '*';
        return $cb;
    }

    /** Separador horizontal (bloque). */
    public function seccion(string $titulo = ''): self
    {
        $this->lineas[] = self::raya();
        if ($titulo !== '') {
            $this->lineas[] = '▸ ' . self::cortar($titulo, self::ANCHO_MAX - 2);
        }
        return $this;
    }

    /**
     * Par clave/valor con ancho fijo y formato semántico.
     *
     * @param mixed $valor
     */
    public function campo(string $etiqueta, $valor, string $tipo = 'texto'): self
    {
        $valorFormateado = self::formatear($valor, $tipo);
        $texto = '  • ' . self::cortar($etiqueta, 22) . ': ' . $valorFormateado;
        $this->lineas[] = self::cortar($texto, self::ANCHO_MAX);
        return $this;
    }

    /** Línea libre sin etiqueta (con viñeta). */
    public function linea(string $texto): self
    {
        $this->lineas[] = '  • ' . self::cortar($texto, self::ANCHO_MAX - 4);
        return $this;
    }

    /**
     * Lista compacta: cada ítem es string plano o [etiqueta, valor].
     *
     * @param array<int,mixed> $items
     */
    public function lista(array $items, string $tipo = 'texto', int $tope = 12): self
    {
        $visibles = array_slice($items, 0, max(1, $tope));
        foreach ($visibles as $item) {
            if (is_array($item)) {
                $etiqueta = (string) ($item[0] ?? $item['etiqueta'] ?? '');
                $valor = $item[1] ?? $item['valor'] ?? '';
                $this->campo($etiqueta, $valor, $tipo);
            } else {
                $this->linea((string) $item);
            }
        }
        $ocultos = count($items) - count($visibles);
        if ($ocultos > 0) {
            $this->lineas[] = '  ⏩ +' . $ocultos . ' ítem(s) más (usa paginación para ver todos).';
        }
        return $this;
    }

    /** Barra ASCII de progreso (gráfica semántica, máx 10 celdas). */
    public function barra(string $etiqueta, float $valor, float $max, int $celdas = 10): self
    {
        $proporcion = $max > 0 ? max(0.0, min(1.0, $valor / $max)) : 0.0;
        $llenas = (int) round($proporcion * $celdas);
        $barra = str_repeat('█', $llenas) . str_repeat('░', max(0, $celdas - $llenas));
        $this->lineas[] = '  ' . self::cortar($etiqueta, 24)
            . ' [' . $barra . '] ' . number_format($proporcion * 100, 1, ',', '.') . '%';
        return $this;
    }

    /** Fila alineada de tabla: $celdas = [ancho1, ancho2...] (paddings fijos). */
    public function fila(array $celdasValores, array $anchos = []): self
    {
        $partes = [];
        foreach ($celdasValores as $i => $valor) {
            $ancho = (int) ($anchos[$i] ?? 0);
            $texto = self::cortar((string) $valor, $ancho > 0 ? $ancho : self::ANCHO_MAX - 4);
            $partes[] = $ancho > 0
                ? str_pad($texto, min($ancho, self::ANCHO_MAX), ' ', STR_PAD_RIGHT)
                : $texto;
        }
        $this->lineas[] = self::cortar(implode(' | ', $partes), self::ANCHO_MAX);
        return $this;
    }

    /** Footer con metadata (fuente/tiempo). */
    public function footer(string $metadata = ''): self
    {
        $this->lineas[] = self::raya();
        if ($metadata !== '') {
            $this->lineas[] = '⚡ ' . self::cortar($metadata, self::ANCHO_MAX - 4);
        }
        return $this;
    }

    /** Ensambla la tarjeta respetando ANCHO_MAX x LINEAS_MAX. */
    public function tarjeta(): string
    {
        $lineas = $this->lineas;
        $ocultas = count($lineas) - self::LINEAS_MAX;
        if ($ocultas > 0) {
            $lineas = array_slice($lineas, 0, self::LINEAS_MAX - 1);
            $lineas[] = '... (+' . $ocultas . ' líneas omitidas)';
        }
        return implode("\n", $lineas);
    }

    /**
     * Tarjeta genérica a partir de un payload JSON de una skill Python
     * (superconector v4.13): escalares → campo; arrays → conteo + primeras
     * entradas; estructura anidada → resumen JSON acortado.
     *
     * @param array<string,mixed> $data
     */
    public static function resumir(array $data, string $titulo, string $emoji = '🤖', string $fuente = ''): string
    {
        $cb = self::iniciar($emoji, $titulo)
            ->seccion('Resumen de la skill');
        foreach (array_slice($data, 0, 10) as $k => $v) {
            if (is_array($v)) {
                $n = count($v);
                $cb->campo($k, $n === 0 ? 'vacío' : ($n . ' elemento(s)'));
                foreach (array_slice($v, 0, 3) as $sub) {
                    if (is_scalar($sub) && $sub !== '') {
                        $cb->linea((string) $sub);
                    } elseif (is_array($sub) && $sub !== []) {
                        $primer = reset($sub);
                        if (is_scalar($primer)) {
                            $cb->linea((string) $primer);
                        }
                    }
                }
            } elseif (is_bool($v)) {
                $cb->campo($k, $v ? 'Sí' : 'No');
            } else {
                $cb->campo($k, $v);
            }
        }
        if ($fuente !== '') {
            $cb->footer($fuente);
        }
        return $cb->tarjeta();
    }

    // ------------------------------------------------------------------ util
    private static function raya(): string
    {
        return '━━' . str_repeat('━', self::ANCHO_MAX - 2);
    }

    /**
     * Formato semántico de valores por tipo.
     *
     * @param mixed $valor
     */
    private static function formatear($valor, string $tipo): string
    {
        if ($valor === null || $valor === '') {
            return 'N/A';
        }
        switch ($tipo) {
            case 'numero':
                return number_format((float) $valor, 0, ',', '.');
            case 'decimal':
                return number_format((float) $valor, 2, ',', '.');
            case 'moneda':
                return 'Bs ' . number_format((float) $valor, 2, ',', '.');
            case 'porcentaje':
                return number_format((float) $valor, 1, ',', '.') . '%';
            case 'fecha':
                $fecha = is_numeric($valor) ? (int) $valor : strtotime((string) $valor);
                return $fecha > 0 ? date('d/m/Y H:i', $fecha) : (string) $valor;
            case 'estado':
                $mapa = ['PREPARACION' => '🟡', 'CHEQUEO' => '🔵', 'EMBALADA' => '🟣',
                         'IMPRESA' => '⚪', 'CHEQUEADA' => '✅', 'VERIFICADA' => '✅',
                         'PENDIENTE' => '⏳', 'PROCESADA' => '🚚'];
                $clave = strtoupper(trim((string) $valor));
                return ($mapa[$clave] ?? '❓') . ' ' . (string) $valor;
            default:
                return (string) $valor;
        }
    }

    /** Corta por palabras respetando multibyte; nunca excede $ancho. */
    private static function cortar(string $texto, int $ancho): string
    {
        $texto = trim($texto);
        if (mb_strlen($texto) <= $ancho) {
            return $texto;
        }
        $cortado = mb_substr($texto, 0, $ancho - 1);
        $ultimo = mb_strrpos($cortado, ' ');
        return ($ultimo !== false && $ultimo > 0)
            ? mb_substr($cortado, 0, $ultimo) . '…'
            : $cortado . '…';
    }
}
