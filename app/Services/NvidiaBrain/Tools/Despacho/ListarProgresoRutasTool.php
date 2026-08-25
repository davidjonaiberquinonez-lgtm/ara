<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Despacho;

use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\FlaskApiTrait;

require_once __DIR__ . '/../Common/FlaskApiTrait.php';

/**
 * Herramienta "listar_progreso_rutas": estado de TODAS las Macro-Rutas que
 * tengan al menos un operador trabajándolas ahora mismo — sin necesitar
 * saber el nombre exacto de cada una (a diferencia de "progreso_ruta", que
 * exige el nombre). Complementa a esa tool para la pregunta "¿cómo van las
 * rutas?"/"dame el estado de todas las rutas".
 *
 * Fuente: SQLite de ARA_Brain (`sesiones_ruta_activa`) vía el endpoint
 * GET /api/rutas/progreso_todas (rutas/infrastructure/web/route_router.py).
 */
final class ListarProgresoRutasTool implements AgentToolInterface
{
    use FlaskApiTrait;

    public function __construct(string $baseUrl = '', int $timeoutS = 10)
    {
        $this->initFlaskApi($baseUrl, $timeoutS);
    }

    public function getName(): string
    {
        return 'listar_progreso_rutas';
    }

    public function getDescription(): string
    {
        return 'Lista el estado de TODAS las rutas (Macro-Rutas) que tengan '
             . 'al menos un operador trabajándolas ahora mismo: cuántas cajas '
             . 'llevan escaneadas hasta el momento (0-0 sin escanear, 1-0 caja '
             . 'escaneada/falta factura, 1-1 verificada) por cada ruta, con '
             . 'desglose por sede (San Cristóbal / Barquisimeto) cuando ambas '
             . 'tienen operadores en la misma ruta. Úsala cuando pregunten por '
             . 'el estado/progreso de las rutas EN GENERAL, sin nombrar una '
             . 'ruta específica (para una ruta puntual por nombre, usa '
             . '"progreso_ruta"). Sin parámetros.';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => (object) [],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        try {
            $data = $this->flaskGetJson('/api/rutas/progreso_todas');
        } catch (\Throwable $e) {
            return ['ok' => false, 'error' => 'No se pudo consultar el estado de las rutas: ' . $e->getMessage()];
        }

        if (($data['status'] ?? '') !== 'success') {
            return ['ok' => false, 'error' => (string) ($data['mensaje'] ?? 'Error del servidor.')];
        }

        $rutas = $data['rutas'] ?? [];
        if (empty($rutas)) {
            return [
                'ok' => true,
                'total_rutas' => 0,
                'rutas' => [],
                'respuesta' => (string) ($data['mensaje'] ?? 'No hay ninguna ruta con sesión activa en este momento.'),
            ];
        }

        // BUG corregido (a pedido del usuario, 21/08): el % completado se
        // queda en 0% mientras el chofer sigue escaneando cajas y aún no
        // verifica ninguna factura contra el sistema (1-1 sigue en 0), dando
        // la falsa impresión de "sin avance" cuando en realidad ya lleva
        // varias cajas escaneadas (1-0). Se reemplaza por el conteo real de
        // cajas ya escaneadas hasta el momento (1-0 + 1-1), sin porcentaje.
        $lineaRuta = static function (array $r): string {
            $linea = sprintf(
                '%s: %d/%d cajas escaneadas hasta el momento, %d operador(es)',
                (string) ($r['ruta_macro'] ?? '—'),
                (int) ($r['cajas_escaneadas'] ?? ((int) ($r['caja_escaneada_1_0'] ?? 0) + (int) ($r['verificado_1_1'] ?? 0))),
                (int) ($r['total_items'] ?? 0),
                (int) ($r['total_operadores'] ?? 0)
            );
            // Desglose por sede: la misma Macro-Ruta puede tener operadores
            // trabajando desde San Cristóbal Y desde Barquisimeto a la vez —
            // el nombre de la ruta se muestra unificado, pero el avance de
            // cajas se separa por sede. Si solo hay una sede, no se agrega
            // el detalle (sería redundante con la línea principal).
            $porSede = $r['por_sede'] ?? [];
            if (is_array($porSede) && count($porSede) > 1) {
                $partes = [];
                foreach ($porSede as $sede => $s) {
                    $etiqueta = $sede === 'SC' ? 'San Cristóbal' : ($sede === 'BQTO' ? 'Barquisimeto' : (string) $sede);
                    $partes[] = $etiqueta . ': ' . (int) ($s['cajas_escaneadas'] ?? 0) . '/' . (int) ($s['total_items'] ?? 0);
                }
                $linea .= "\n  (" . implode(' · ', $partes) . ')';
            }
            return $linea;
        };

        // Agrupado por turno (decisión de negocio del usuario, 2026-08-20):
        // mañana = CARACAS/ARAGUA/CARABOBO/ZULIA/TRUJILLO/PORTUGUESA,
        // noche = MERIDA/FRONTERA-TACHIRA/APURE/BARINAS/BARQUISIMETO/FALCON.
        $turnos = $data['turnos'] ?? null;
        if (is_array($turnos)) {
            $bloques = [];
            foreach ([['mañana', '🌅 Turno mañana'], ['noche', '🌙 Turno noche'], ['sin_turno', '❔ Sin turno asignado']] as [$clave, $titulo]) {
                $grupo = $turnos[$clave] ?? [];
                if (empty($grupo)) {
                    continue;
                }
                $lineasGrupo = array_map($lineaRuta, $grupo);
                $bloques[] = $titulo . ":\n- " . implode("\n- ", $lineasGrupo);
            }
            return [
                'ok' => true,
                'total_rutas' => (int) ($data['total_rutas'] ?? count($rutas)),
                'turnos' => $turnos,
                'rutas' => $rutas,
                'respuesta' => "Estado de las rutas activas:\n\n" . implode("\n\n", $bloques),
            ];
        }

        $lineas = array_map($lineaRuta, $rutas);
        return [
            'ok' => true,
            'total_rutas' => (int) ($data['total_rutas'] ?? count($rutas)),
            'rutas' => $rutas,
            'respuesta' => "Estado de las rutas activas:\n- " . implode("\n- ", $lineas),
        ];
    }
}
