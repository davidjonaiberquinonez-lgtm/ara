<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Despacho;

use App\Services\NvidiaBrain\Adapters\PythonSkillExecutor;
use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;

require_once __DIR__ . '/../../Adapters/PythonSkillExecutor.php';
require_once __DIR__ . '/../Common/CardBuilder.php';

/**
 * Activador/superconector PHP de la Skill de Python del departamento
 * Despacho (C:\ARA_PROYECT\skills\despacho\cierre_despacho.py), v4.2.
 *
 * Validación de bultos del Bulto Cerrado y PRECIERRE transaccional:
 *   - validar_estructura_bultos: gate 1:1 bulto/ítem (pura, sin BD).
 *   - validar_bultos: estado del despacho de la nota en el legacy
 *     (estatus + conteo físico de bultos/cajas).
 *   - precierre_transaccional: plan de precierre (dry-run por defecto;
 *     modo_real=true solo PLANIFICA el UPDATE, nunca lo ejecuta).
 *
 * Los parámetros viajan en JSON por stdin del subproceso Python y la
 * respuesta llega estandarizada: {"success": true, "data": ...}.
 *
 * Uso:
 *   $registry->registerTool(new PythonDespachoSkillTool());
 */
final class PythonDespachoSkillTool implements AgentToolInterface
{
    /** @var array<string,string> Acciones soportadas del script. */
    private const ACCIONES = [
        'validar_estructura_bultos' => 'validar_estructura_bultos',
        'validar_bultos'            => 'validar_bultos',
        'precierre_transaccional'   => 'precierre_transaccional',
    ];

    public function getName(): string
    {
        return 'python_despacho_skill';
    }

    public function getDescription(): string
    {
        return 'Activa la Skill de Python de Despacho (superconector v4.2): '
             . 'valida los bultos del Bulto Cerrado y arma el precierre '
             . 'transaccional del despacho. Acciones: '
             . 'validar_estructura_bultos (gate 1:1 bulto/item, pura), '
             . 'validar_bultos (estado de la nota + bultos registrados en el '
             . 'legacy) y precierre_transaccional (plan de cierre, dry-run '
             . 'por defecto; modo_real=true solo planifica el UPDATE). '
             . 'Parametros: nota, sede (SC/BQTO), bultos, items y modo_real. '
             . 'Retorna JSON {"success": true, "data": ...}.';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'accion' => [
                    'type'        => 'string',
                    'enum'        => ['validar_estructura_bultos', 'validar_bultos', 'precierre_transaccional'],
                    'description' => 'Funcion a invocar (default: precierre_transaccional).',
                ],
                'nota' => [
                    'type'        => 'string',
                    'description' => 'Codigo de la nota de despacho a validar.',
                ],
                'sede' => [
                    'type'        => 'string',
                    'enum'        => ['SC', 'BQTO'],
                    'description' => 'Sede del despacho (default: BQTO).',
                ],
                'modo_real' => [
                    'type'        => 'boolean',
                    'description' => 'Si true, el precierre PLANIFICA el UPDATE (nunca lo ejecuta; la capa ARA_SYNC aplica el cierre real).',
                ],
                'bultos' => [
                    'type'        => 'array',
                    'description' => 'Bultos fisicos del despacho para el gate 1:1: strings o {"codigo": ..., "item": ...}.',
                    'items'       => ['type' => 'string'],
                ],
                'items' => [
                    'type'        => 'array',
                    'description' => 'Codigos de articulo esperados de la nota para el gate 1:1.',
                    'items'       => ['type' => 'string'],
                ],
            ],
            'required' => [],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $accion = trim((string) ($arguments['accion'] ?? ''));
        if ($accion === '') {
            $accion = 'precierre_transaccional';
        }
        if (!isset(self::ACCIONES[$accion])) {
            return [
                'success' => false,
                'error'   => 'Accion invalida: "' . $accion . '". Usa validar_estructura_bultos, validar_bultos o precierre_transaccional.',
            ];
        }

        $params = [];
        if (isset($arguments['nota'])) {
            $params['nota'] = (string) $arguments['nota'];
        }
        if (isset($arguments['sede'])) {
            $params['sede'] = strtoupper((string) $arguments['sede']);
        }
        if (isset($arguments['modo_real'])) {
            $params['modo_real'] = (bool) $arguments['modo_real'];
        }
        if (isset($arguments['bultos']) && is_array($arguments['bultos'])) {
            $params['bultos'] = array_values($arguments['bultos']);
        }
        if (isset($arguments['items']) && is_array($arguments['items'])) {
            $params['items'] = array_values(array_map('strval', $arguments['items']));
        }

        $res = PythonSkillExecutor::runSkill('despacho/cierre_despacho', $params, $accion);
        if ($res['success'] ?? false) {
            $res['card'] = CardBuilder::resumir(
                is_array($res['data'] ?? null) ? $res['data'] : [],
                'SKILL PYTHON: DESPACHO (' . $accion . ')',
                '🤖',
                'Fuente: skills/despacho/cierre_despacho'
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
