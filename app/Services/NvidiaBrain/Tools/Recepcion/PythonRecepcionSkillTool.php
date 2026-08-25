<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Recepcion;

use App\Services\NvidiaBrain\Adapters\PythonSkillExecutor;
use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;

require_once __DIR__ . '/../../Adapters/PythonSkillExecutor.php';
require_once __DIR__ . '/../Common/CardBuilder.php';

/**
 * Activador/superconector PHP de la Skill de Python del departamento
 * Recepción (C:\ARA_PROYECT\skills\recepcion\factura_vision_ocr.py), v4.1.
 *
 * Procesa la foto de una factura/nota de crédito con visión multimodal LOCAL
 * (Ollama + LLaVA) y fallback Tesseract OCR, extrae proveedor/rif/nro/fecha/
 * renglones y genera el PDF normalizado de recepción con nomenclatura
 * PDFs_Recepcion/[LABORATORIO]_[TIPO_DOC]_[NRO_FACTURA]_[FECHA].pdf.
 *
 * Los parámetros viajan en JSON por stdin del subproceso Python y la
 * respuesta llega estandarizada: {"success": true, "data": ...}.
 *
 * Uso:
 *   $registry->registerTool(new PythonRecepcionSkillTool());
 */
final class PythonRecepcionSkillTool implements AgentToolInterface
{
    public function getName(): string
    {
        return 'python_recepcion_skill';
    }

    public function getDescription(): string
    {
        return 'Activa la Skill de Python de Recepcion (superconector v4.1): '
             . 'procesa la foto de una factura o nota de credito con vision '
             . 'multimodal local (Ollama/LLaVA) y fallback Tesseract, extrae '
             . '{tipo_documento, proveedor, rif, nro_factura, fecha_emision, '
             . 'renglones} y genera el PDF normalizado de recepcion. '
             . 'Parametros: ruta_imagen (obligatorio) y laboratorio_override '
             . '(opcional). Retorna JSON {"success": true, "data": ...}.';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'ruta_imagen' => [
                    'type'        => 'string',
                    'description' => 'Ruta absoluta de la fotografia de la factura/nota de credito a procesar.',
                ],
                'laboratorio_override' => [
                    'type'        => 'string',
                    'description' => 'Nombre del laboratorio/proveedor si se quiere forzar sobre el detectado.',
                ],
            ],
            'required' => ['ruta_imagen'],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $rutaImagen = trim((string) ($arguments['ruta_imagen'] ?? ''));
        if ($rutaImagen === '') {
            return ['success' => false, 'error' => 'Parámetro "ruta_imagen" es obligatorio (foto de la factura).'];
        }

        $params = ['ruta_imagen' => $rutaImagen];
        if (isset($arguments['laboratorio_override']) && trim((string) $arguments['laboratorio_override']) !== '') {
            $params['laboratorio_override'] = (string) $arguments['laboratorio_override'];
        }

        $res = PythonSkillExecutor::runSkill('recepcion/factura_vision_ocr', $params, 'procesar_foto_factura');
        if ($res['success'] ?? false) {
            $res['card'] = CardBuilder::resumir(
                is_array($res['data'] ?? null) ? $res['data'] : [],
                'SKILL PYTHON: RECEPCIÓN (factura_vision_ocr)',
                '📸',
                'Fuente: skills/recepcion/factura_vision_ocr'
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
