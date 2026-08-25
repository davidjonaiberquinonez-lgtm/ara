<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Almacen;

use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;
use App\Services\NvidiaBrain\Tools\Common\FlaskApiTrait;

require_once __DIR__ . '/../Common/FlaskApiTrait.php';
require_once __DIR__ . '/../Common/CardBuilder.php';

/**
 * Herramienta "operador_inactividad": operadores que tienen una nota tomada
 * (estado 'preparando') pero llevan más de N minutos sin registrar ningún
 * movimiento — es decir, sin escanear/mover nada.
 *
 * Fuente: SQLite de ARA_Brain (`notas_entrega` para saber quién está activo
 * ahora + `movimientos_preparador` para su último movimiento registrado) vía
 * el endpoint GET /api/operadores/inactividad (notas_hexagonal.py). Acotado
 * al día actual y excluye los dos turnos de almuerzo (12:30-14:00 y
 * 14:00-15:30) del cómputo de minutos inactivos (v4.56).
 */
final class OperadorInactividadTool implements AgentToolInterface
{
    use FlaskApiTrait;

    public function __construct(string $baseUrl = '', int $timeoutS = 10)
    {
        $this->initFlaskApi($baseUrl, $timeoutS);
    }

    public function getName(): string
    {
        return 'operador_inactividad';
    }

    public function getDescription(): string
    {
        return 'Lista operadores que tienen una nota tomada (estado '
             . '"preparando") pero llevan más de un umbral de minutos sin '
             . 'registrar ningún movimiento (sin escanear/mover nada) — '
             . 'útil para detectar operadores parados/atascados. Parámetro: '
             . 'umbral_minutos (opcional, default 60).';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'umbral_minutos' => [
                    'type'        => 'integer',
                    'description' => 'Minutos sin actividad a partir de los cuales se considera inactivo (default 60).',
                ],
            ],
            'required' => [],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $umbral = (int) ($arguments['umbral_minutos'] ?? 60);
        $umbral = max(1, min(1440, $umbral));

        try {
            $data = $this->flaskGetJson('/api/operadores/inactividad?umbral_minutos=' . $umbral);
        } catch (\Throwable $e) {
            return ['ok' => false, 'error' => 'No se pudo consultar inactividad de operadores: ' . $e->getMessage()];
        }

        if (($data['status'] ?? '') === 'error') {
            return ['ok' => false, 'error' => (string) ($data['mensaje'] ?? 'Error del servidor.')];
        }

        $inactivos = is_array($data['inactivos'] ?? null) ? $data['inactivos'] : [];
        $totalActivos = (int) ($data['total_operadores_activos'] ?? 0);

        if ($inactivos === []) {
            $sinDatos = "Ningún operador lleva más de {$umbral} minutos sin actividad (de {$totalActivos} con nota tomada ahora mismo).";
            return [
                'ok'                     => true,
                'umbral_minutos'         => $umbral,
                'total_operadores_activos' => $totalActivos,
                'inactivos'              => [],
                'respuesta'              => $sinDatos,
                'card'                   => CardBuilder::iniciar('⏱️', 'OPERADORES INACTIVOS (HOY)')
                    ->campo('UMBRAL', $umbral . ' min')
                    ->campo('CON NOTA TOMADA', $totalActivos, 'numero')
                    ->linea($sinDatos)
                    ->footer('Excluye almuerzo (12:30-14:00 y 14:00-15:30) · solo movimientos de hoy')
                    ->tarjeta(),
            ];
        }

        $cb = CardBuilder::iniciar('⏱️', 'OPERADORES INACTIVOS (HOY)')
            ->campo('UMBRAL', $umbral . ' min')
            ->campo('CON NOTA TOMADA', $totalActivos, 'numero')
            ->campo('INACTIVOS', count($inactivos), 'numero')
            ->seccion('Detalle');
        foreach ($inactivos as $i) {
            $usuario = (string) ($i['usuario'] ?? 'N/D');
            $nota = (string) ($i['nota_actual'] ?? 'N/D');
            $mins = $i['minutos_inactivo'] ?? null;
            $ultima = (string) ($i['ultima_accion_hoy'] ?? '');
            $minsTxt = $mins === null ? 'sin movimiento hoy' : round((float) $mins) . ' min';
            $ultimaTxt = $ultima !== '' ? " (última: {$ultima})" : '';
            $cb->linea("{$usuario} · nota {$nota} · {$minsTxt}{$ultimaTxt}");
        }
        $cb->footer('Excluye almuerzo (12:30-14:00 y 14:00-15:30) · solo movimientos de hoy');

        $nombres = implode(', ', array_map(static fn (array $i): string => (string) $i['usuario'], $inactivos));
        return [
            'ok'                     => true,
            'umbral_minutos'         => $umbral,
            'total_operadores_activos' => $totalActivos,
            'total_inactivos'        => count($inactivos),
            'inactivos'              => $inactivos,
            'respuesta'              => count($inactivos) . " operador(es) sin actividad hace más de {$umbral} min hoy: {$nombres}.",
            'card'                   => $cb->tarjeta(),
        ];
    }
}
