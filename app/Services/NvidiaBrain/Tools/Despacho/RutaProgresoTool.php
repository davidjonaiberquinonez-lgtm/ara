<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Despacho;

use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\FlaskApiTrait;

require_once __DIR__ . '/../Common/FlaskApiTrait.php';

/**
 * Herramienta "progreso_ruta": ¿cómo va la ruta X? — progreso agregado de
 * TODOS los operadores que estén trabajando esa Macro-Ruta ahora mismo
 * (matriz de cotejo 0-0/1-0/1-1 sumada, más el detalle por operador).
 *
 * Fuente: SQLite de ARA_Brain (`sesiones_ruta_activa`) vía el endpoint
 * GET /api/rutas/progreso?ruta_macro=X (rutas/infrastructure/web/route_router.py).
 */
final class RutaProgresoTool implements AgentToolInterface
{
    use FlaskApiTrait;

    public function __construct(string $baseUrl = '', int $timeoutS = 10)
    {
        $this->initFlaskApi($baseUrl, $timeoutS);
    }

    public function getName(): string
    {
        return 'progreso_ruta';
    }

    public function getDescription(): string
    {
        return 'Progreso de una ruta por su nombre: cuántas cajas lleva '
             . 'escaneadas hasta el momento (0-0 sin escanear, 1-0 caja '
             . 'escaneada/falta factura, 1-1 verificada), sumando TODOS los '
             . 'operadores que la tengan activa ahora mismo, con desglose por '
             . 'sede (San Cristóbal / Barquisimeto) si ambas están trabajando '
             . 'la misma ruta, más el detalle por operador. Parámetro: '
             . 'ruta_macro (nombre de la ruta, requerido — ej. "CARACAS").';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'ruta_macro' => [
                    'type'        => 'string',
                    'description' => 'Nombre de la ruta/macro-ruta a consultar (requerido).',
                ],
            ],
            'required' => ['ruta_macro'],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $rutaMacro = trim((string) ($arguments['ruta_macro'] ?? ''));
        if ($rutaMacro === '') {
            return ['ok' => false, 'error' => 'Falta el parámetro "ruta_macro" (nombre de la ruta).'];
        }

        try {
            $data = $this->flaskGetJson('/api/rutas/progreso?ruta_macro=' . rawurlencode($rutaMacro));
        } catch (\Throwable $e) {
            return ['ok' => false, 'error' => 'No se pudo consultar el progreso de la ruta: ' . $e->getMessage()];
        }

        if (($data['status'] ?? '') !== 'success') {
            return ['ok' => false, 'error' => (string) ($data['mensaje'] ?? 'Error del servidor.')];
        }

        $totalItems = (int) ($data['total_items'] ?? 0);
        $n11 = (int) ($data['n11'] ?? 0);
        $n10 = (int) ($data['n10'] ?? 0);
        $cajasEscaneadas = (int) ($data['cajas_escaneadas'] ?? ($n10 + $n11));
        $totalOperadores = (int) ($data['total_operadores'] ?? 0);

        // BUG corregido (a pedido del usuario, 21/08, mismo fix que
        // listar_progreso_rutas): el % se queda en 0 mientras el chofer
        // escanea cajas y aún no verifica ninguna factura — se reemplaza por
        // el conteo real de cajas ya escaneadas (1-0 + 1-1).
        $respuesta = "Ruta {$rutaMacro}: {$cajasEscaneadas}/{$totalItems} cajas escaneadas hasta el momento, "
                   . "con {$totalOperadores} operador(es) trabajándola.";

        $porSede = $data['por_sede'] ?? [];
        if (is_array($porSede) && count($porSede) > 1) {
            $partes = [];
            foreach ($porSede as $sede => $s) {
                $etiqueta = $sede === 'SC' ? 'San Cristóbal' : ($sede === 'BQTO' ? 'Barquisimeto' : (string) $sede);
                $partes[] = $etiqueta . ': ' . (int) ($s['cajas_escaneadas'] ?? 0) . '/' . (int) ($s['total_items'] ?? 0);
            }
            $respuesta .= "\n(" . implode(' · ', $partes) . ')';
        }

        return [
            'ok'                     => true,
            'ruta_macro'             => (string) ($data['ruta_macro'] ?? $rutaMacro),
            'total_operadores'       => $totalOperadores,
            'total_items'            => $totalItems,
            'sin_escanear_0_0'       => (int) ($data['n00'] ?? 0),
            'caja_escaneada_1_0'     => $n10,
            'verificado_1_1'         => $n11,
            'cajas_escaneadas'       => $cajasEscaneadas,
            'por_sede'               => $porSede,
            'operadores'             => $data['operadores'] ?? [],
            'respuesta'              => $respuesta,
        ];
    }
}
