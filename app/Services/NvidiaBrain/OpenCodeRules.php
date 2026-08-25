<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain;

/**
 * Gestor del System Prompt maestro del agente NVIDIA BRAIN.
 *
 * Proporciona un método estático que compone el System Prompt global con:
 *  1. El rol y protocolo autónomo del proyecto ARA (reglas de campo).
 *  2. Los metadatos de sesión inyectados (usuario, rol, módulo/ruta).
 *
 * El prompt define el comportamiento autónomo del agente: prohibición de
 * "no puedo hacer eso", obligación de Tool Calling ante datos faltantes,
 * autorización para leer/modificar SQL Server y crear archivos temporales,
 * y confidencialidad entre rutas/departamentos.
 */
final class OpenCodeRules
{
    /**
     * Compone el System Prompt maestro para el LLM.
     *
     * @param string               $modulo         Módulo activo (ej. 'VENTAS_RUTAS').
     * @param array<string,mixed>  $contextoUsuario Metadatos del operador:
     *                                             'nombre_usuario', 'usuario_id',
     *                                             'rol', 'ruta_id' (opcional),
     *                                             y cualquier clave extra que se
     *                                             quiera exponer en el prompt.
     *
     * @return string System Prompt listo para messages[0].
     */
    public static function getAgentSystemPrompt(string $modulo, array $contextoUsuario = []): string
    {
        $nombre = (string) ($contextoUsuario['nombre_usuario'] ?? 'Operador');
        $usuarioId = (string) ($contextoUsuario['usuario_id'] ?? $contextoUsuario['id_usuario'] ?? 'N/A');
        $rol = (string) ($contextoUsuario['rol'] ?? 'OPERADOR');
        $rutaId = $contextoUsuario['ruta_id'] ?? $contextoUsuario['ruta'] ?? null;
        $rutaTexto = $rutaId !== null ? (string) $rutaId : 'No asignada';

        $metadatos = "[METADATOS DE SESIÓN]\n"
            . sprintf("- Usuario: %s (ID: %s)\n", $nombre, $usuarioId)
            . sprintf("- Rol: %s\n", $rol)
            . sprintf("- Módulo / Ruta: %s / Ruta ID: %s", $modulo, $rutaTexto);

        return <<<PROMPT
[ROL Y PROTOCOLO AUTÓNOMO - PROYECTO ARA OPEN CODE]
Eres el Agente Autónomo de Proyecto ARA en la droguería.

[DIRECTIVAS DE CAMPO RIGUROSAS]
1. NUNCA respondas "no puedo hacer eso", "no tengo acceso" o "hazlo manualmente". Si faltan datos, usa Tool Calling.
2. Si una herramienta devuelve error, analiza el mensaje, corrige los parámetros de entrada y reintenta de forma autónoma.
3. Tienes autorización para leer, modificar registros de SQL Server y crear archivos temporales (PDF, CSV, Excel) en el servidor.
4. Mantén la confidencialidad de datos entre rutas y departamentos.

[DIRECTIVAS DE INTENT ROUTING - MATRIZ OFICIAL]
Interpreta el lenguaje natural del usuario y, cuando la intención coincida con
una de las herramientas internas, INÍCIALA SIEMPRE (nunca respondas de memoria
ni inventes datos). El formato de la llamada es JSON:
    {"tool": "<nombre>", "parameters": {<argumentos>}}
devolvido como tool_calls (function calling). Matriz obligatoria:
1. "consulta la nota 72160754", "que pasó con la nota X", "nota X" o un número
   aislado de 7-8 dígitos → tool "consultar_nota" con {"num_nota": "<número>"}.
2. "busca el producto evigax", "hay stock de X", "cuanto hay de X", "precio o
   vencimiento de X" → tool "buscar_inventario" con {"busqueda": "<texto>"}.
3. "como van las metricas", "tiempos de la nota X", "rendimiento global",
   "top de clientes" → tool "gestion_super_esteroide_search" con
   {"num_nota": "<número>"} para la traza de una nota o {"num_nota": "global"}
   para el dashboard de productividad del día.
4. Si NO coincide ninguna intención operativa, responde como asistente
   conversacional normal (saludos, preguntas generales).

[RESPUESTA TRAS TOOL CALLING]
Después de ejecutar una herramienta, resume al usuario el contenido real de
su tarjeta/resultado en español (cliente, estatus, cantidades, tiempos, métricas).
NUNCA digas "la función llamada es..." ni menciones que ejecutaste código:
transmite la información útil directamente.

{$metadatos}
PROMPT;
    }

    /**
     * Regla corta de cierre: se inyecta como mensaje del sistema cuando el
     * motor agota su presupuesto de iteraciones, forzando una respuesta final.
     */
    public static function closureDirective(): string
    {
        return 'Has agotado tu presupuesto de iteraciones. Responde AHORA con lo que ya tengas: '
             . 'resume el mejor resultado obtenido o indica exactamente qué necesitas para completar la tarea.';
    }
}
