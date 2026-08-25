<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Contracts;

/**
 * Contrato estricto de una herramienta (tool) ejecutable por el agente.
 *
 * Cada herramienta que se registre en el ToolRegistry DEBE implementar esta
 * interfaz. El motor lee getName()/getDescription()/getParameters() para
 * construir las definiciones JSON (tools[]) que se envían al LLM en formato
 * OpenAI v1, e invoca execute() cuando el modelo solicita la llamada.
 *
 * Contrato de execute():
 *  - Los $arguments provienen de tool_calls[].function.arguments (JSON).
 *  - El $contexto lo inyecta el motor del agente (usuario, rol, módulo,
 *    ruta_id, y cualquier dato de sesión relevante). Permite que la misma
 *    herramienta opere de forma segura según el operador activo.
 *  - DEBE retornar un array serializable a JSON. Convención de forma:
 *       ['ok' => true,  ...datos]   → éxito
 *       ['ok' => false, 'error' => 'mensaje'] → error controlado
 *    El ToolRegistry garantiza que un \Throwable nunca rompa el bucle del
 *    agente: lo captura y lo devuelve como error estructurado.
 *
 * Desacoplado de frameworks: PHP nativo, encaja en cualquier controlador ARA.
 */
interface AgentToolInterface
{
    /**
     * Nombre único de la herramienta (idéntico a tool_calls[].function.name
     * que emitirá el modelo). Ej.: "buscar_inventario", "crear_reporte_archivo".
     */
    public function getName(): string;

    /**
     * Descripción legible por el LLM: qué hace, cuándo usarla y qué devuelve.
     * Una descripción precisa es decisiva para que el modelo elija bien la
     * herramienta.
     */
    public function getDescription(): string;

    /**
     * Esquema JSON Schema (subset) de los parámetros de la herramienta.
     *
     * Estructura esperada:
     * [
     *     'type'       => 'object',
     *     'properties' => [
     *         'param' => ['type' => 'string', 'description' => '...'],
     *     ],
     *     'required'   => ['param'],
     * ]
     */
    public function getParameters(): array;

    /**
     * Ejecuta la herramienta con los argumentos del modelo y el contexto del
     * operador/módulo.
     *
     * @param array $arguments Argumentos decodificados (array asociativo).
     * @param array $contexto  Contexto de sesión inyectado por el motor:
     *                         usuario_id, nombre_usuario, rol, modulo, ruta_id
     *                         y cualquier extra provisto por el controlador.
     *
     * @return array Resultado serializable a JSON. Forma recomendada:
     *               ['ok' => true, ...] en éxito o
     *               ['ok' => false, 'error' => '...'] en fallo controlado.
     */
    public function execute(array $arguments, array $contexto): array;
}
