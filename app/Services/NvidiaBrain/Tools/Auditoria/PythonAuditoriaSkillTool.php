<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Auditoria;

use App\Services\NvidiaBrain\Adapters\PythonSkillExecutor;
use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;

require_once __DIR__ . '/../../Adapters/PythonSkillExecutor.php';
require_once __DIR__ . '/../Common/CardBuilder.php';

/**
 * Activador/superconector PHP de las Skills de Python del departamento
 * Auditoría (C:\ARA_PROYECT\skills\auditoria\), v4.1.
 *
 * Skills envueltas:
 *   - detector_malsurtido.py    → detector de mal surtido en logs de bulto
 *                                 cerrado (analizar_log_surtido,
 *                                 emitir_alerta, bloquear_cierre_nota,
 *                                 vigilar_log_surtido).
 *   - discrepancia_traslados.py → acta de discrepancia en traslados
 *                                 inter-sedes (auditar_traslado_intersedes).
 *
 * Los parámetros viajan en JSON por stdin del subproceso Python y la
 * respuesta llega estandarizada: {"success": true, "data": ...}.
 *
 * Uso:
 *   $registry->registerTool(new PythonAuditoriaSkillTool());
 */
final class PythonAuditoriaSkillTool implements AgentToolInterface
{
    /** @var array<string,string> Acción por defecto por script. */
    private const ACCIONES_DEFECTO = [
        'detector_malsurtido'    => 'analizar_log_surtido',
        'discrepancia_traslados' => 'auditar_traslado_intersedes',
    ];

    public function getName(): string
    {
        return 'python_auditoria_skill';
    }

    public function getDescription(): string
    {
        return 'Activa las Skills de Python del departamento Auditoria '
             . '(superconector v4.1). Script detector_malsurtido: analiza '
             . 'logs LOG_REPORTE de bulto cerrado y reporta mal surtidos '
             . '(acciones: analizar_log_surtido, emitir_alerta, '
             . 'bloquear_cierre_nota, vigilar_log_surtido). Script '
             . 'discrepancia_traslados: audita traslados inter-sedes '
             . '(accion: auditar_traslado_intersedes). Retorna JSON '
             . '{"success": true, "data": ...}.';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'script' => [
                    'type'        => 'string',
                    'enum'        => ['detector_malsurtido', 'discrepancia_traslados'],
                    'description' => 'Script de la skill de Auditoria a ejecutar (default: detector_malsurtido).',
                ],
                'accion' => [
                    'type'        => 'string',
                    'description' => 'Funcion a invocar dentro del script. Default segun script: analizar_log_surtido / auditar_traslado_intersedes.',
                ],
                'ruta_log' => [
                    'type'        => 'string',
                    'description' => 'Ruta del LOG_REPORTE (archivo o directorio) para analizar_log_surtido / vigilar_log_surtido.',
                ],
                'numero_nota' => [
                    'type'        => 'string',
                    'description' => 'Numero de nota para bloquear_cierre_nota.',
                ],
                'evento' => [
                    'type'        => 'object',
                    'description' => 'Evento de discrepancia {operario_id, timestamp, sku_pedido, sku_escaneado, estacion} para emitir_alerta / bloquear_cierre_nota.',
                ],
                'sonora' => [
                    'type'        => 'boolean',
                    'description' => 'Si emitir_alerta debe reproducir el beep de alerta (default true).',
                ],
                'cod_traslado' => [
                    'type'        => 'string',
                    'description' => 'Codigo del traslado inter-sedes para auditar_traslado_intersedes.',
                ],
                'modo_real' => [
                    'type'        => 'boolean',
                    'description' => 'Si auditar_traslado_intersedes escribe el acta y marca EN_RECLAMO (default false = dry-run).',
                ],
            ],
            'required' => [],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $script = (string) ($arguments['script'] ?? 'detector_malsurtido');
        if (!isset(self::ACCIONES_DEFECTO[$script])) {
            return [
                'success' => false,
                'error'   => 'Script invalido: "' . $script . '". Usa detector_malsurtido o discrepancia_traslados.',
            ];
        }

        $accion = trim((string) ($arguments['accion'] ?? ''));
        if ($accion === '') {
            $accion = self::ACCIONES_DEFECTO[$script];
        }

        $params = [];
        if ($script === 'detector_malsurtido') {
            if (isset($arguments['ruta_log'])) {
                $params['ruta_log'] = (string) $arguments['ruta_log'];
            }
            if (isset($arguments['numero_nota'])) {
                $params['numero_nota'] = (string) $arguments['numero_nota'];
            }
            if (isset($arguments['evento']) && is_array($arguments['evento'])) {
                $params['evento'] = $arguments['evento'];
            }
            if (isset($arguments['sonora'])) {
                $params['sonora'] = (bool) $arguments['sonora'];
            }
        } else {
            if (isset($arguments['cod_traslado'])) {
                $params['cod_traslado'] = (string) $arguments['cod_traslado'];
            }
            if (isset($arguments['modo_real'])) {
                $params['modo_real'] = (bool) $arguments['modo_real'];
            }
        }

        $res = PythonSkillExecutor::runSkill('auditoria/' . $script, $params, $accion);
        if ($res['success'] ?? false) {
            $res['card'] = CardBuilder::resumir(
                is_array($res['data'] ?? null) ? $res['data'] : [],
                'SKILL PYTHON: AUDITORÍA (' . $accion . ')',
                '🔎',
                'Fuente: skills/auditoria/' . $script
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
