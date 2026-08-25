<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Almacen;

use App\Services\NvidiaBrain\Adapters\PythonSkillExecutor;
use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;

require_once __DIR__ . '/../../Adapters/PythonSkillExecutor.php';
require_once __DIR__ . '/../Common/CardBuilder.php';

/**
 * Activador/superconector PHP de las Skills de Python del departamento
 * Almacén (C:\ARA_PROYECT\skills\stock_bulto_cerrado\), v4.1.
 *
 * Skills envueltas:
 *   - surtido_prioritario.py     → cola de surtido de bulto cerrado
 *                                  (obtener_cola_surtido: artículos con
 *                                  estante en 0 y depósito > 0, prioridad
 *                                  ALTA/MEDIA/BAJA por terciles de rotación).
 *   - reporte_quiebres_compras.py→ reporte de quiebres para compras
 *                                  (generar_reporte_compras_laboratorio:
 *                                  Tier 1/2 por laboratorio, Excel/PDF/CSV).
 *
 * Los parámetros viajan en JSON por stdin del subproceso Python y la
 * respuesta llega estandarizada: {"success": true, "data": ...}.
 *
 * Uso:
 *   $registry->registerTool(new PythonStockBultoCerradoSkillTool());
 */
final class PythonStockBultoCerradoSkillTool implements AgentToolInterface
{
    /** @var array<string,string> Acción por defecto por script. */
    private const ACCIONES_DEFECTO = [
        'surtido_prioritario'       => 'obtener_cola_surtido',
        'reporte_quiebres_compras'  => 'generar_reporte_compras_laboratorio',
    ];

    public function getName(): string
    {
        return 'python_stock_bulto_cerrado_skill';
    }

    public function getDescription(): string
    {
        return 'Activa las Skills de Python de Stock/Bulto Cerrado del '
             . 'Almacen (superconector v4.1). Script surtido_prioritario: '
             . 'genera la cola de surtido prioritaria por sede (SC/BQTO) con '
             . 'estante en 0 y deposito > 0, ordenada por rotacion y '
             . 'clasificada ALTA/MEDIA/BAJA (accion: obtener_cola_surtido). '
             . 'Script reporte_quiebres_compras: quiebres totales por '
             . 'laboratorio con Tier 1/2 y sugerencia de compra (accion: '
             . 'generar_reporte_compras_laboratorio). Retorna JSON '
             . '{"success": true, "data": ...}.';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'script' => [
                    'type'        => 'string',
                    'enum'        => ['surtido_prioritario', 'reporte_quiebres_compras'],
                    'description' => 'Script de la skill a ejecutar (default: surtido_prioritario).',
                ],
                'accion' => [
                    'type'        => 'string',
                    'description' => 'Funcion a invocar dentro del script. Default segun script: obtener_cola_surtido / generar_reporte_compras_laboratorio.',
                ],
                'sede' => [
                    'type'        => 'string',
                    'enum'        => ['SC', 'BQTO'],
                    'description' => 'Sede para la cola de surtido (default: SC).',
                ],
                'dias_rotacion' => [
                    'type'        => 'integer',
                    'description' => 'Ventana de dias para calcular la rotacion (default: 30).',
                ],
                'formato' => [
                    'type'        => 'string',
                    'enum'        => ['excel', 'pdf', 'csv'],
                    'description' => 'Formato del reporte de quiebres para compras (default: excel).',
                ],
            ],
            'required' => [],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $script = (string) ($arguments['script'] ?? 'surtido_prioritario');
        if (!isset(self::ACCIONES_DEFECTO[$script])) {
            return [
                'success' => false,
                'error'   => 'Script invalido: "' . $script . '". Usa surtido_prioritario o reporte_quiebres_compras.',
            ];
        }

        $accion = trim((string) ($arguments['accion'] ?? ''));
        if ($accion === '') {
            $accion = self::ACCIONES_DEFECTO[$script];
        }

        $params = [];
        if ($script === 'surtido_prioritario') {
            if (isset($arguments['sede'])) {
                $params['sede'] = strtoupper((string) $arguments['sede']);
            }
            if (isset($arguments['dias_rotacion'])) {
                $params['dias_rotacion'] = (int) $arguments['dias_rotacion'];
            }
        } else {
            if (isset($arguments['formato'])) {
                $params['formato'] = (string) $arguments['formato'];
            }
        }

        $res = PythonSkillExecutor::runSkill('stock_bulto_cerrado/' . $script, $params, $accion);
        if ($res['success'] ?? false) {
            $res['card'] = CardBuilder::resumir(
                is_array($res['data'] ?? null) ? $res['data'] : [],
                'SKILL PYTHON: ' . $script,
                '🤖',
                'Fuente: skills/stock_bulto_cerrado (' . $accion . ')'
            );
        } else {
            $res['card'] = CardBuilder::iniciar('🚫', 'SKILL PYTHON NO EJECUTADA')
                ->linea((string) ($res['error'] ?? 'Error desconocido'))
                ->tarjeta();
        }
        return $res;
    }

    public function getDefinition(): array
    {
        return [
            'name'        => $this->getName(),
            'description' => $this->getDescription(),
            'parameters'  => $this->getParameters(),
        ];
    }
}
