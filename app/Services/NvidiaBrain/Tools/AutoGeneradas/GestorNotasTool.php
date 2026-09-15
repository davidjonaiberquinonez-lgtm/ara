<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\AutoGeneradas;

use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;
use PDO;

require_once __DIR__ . '/../Common/CardBuilder.php';

/**
 * Tool auto-generada por ARA Coder (servicio independiente, puerto 8010).
 * NO editar a mano — se regenera sola cuando ARA Coder resuelve una
 * consulta similar de nuevo. Pregunta que la originó (referencia, no se
 * usa en tiempo de ejecución): ver catálogo en generated_skills/catalog.json.
 */
final class GestorNotasTool implements AgentToolInterface
{
    private const QUERY = 'SELECT numero, nombre FROM usuarios WHERE numero IN (22, 38, 15)';

    public function getName(): string
    {
        return 'consultar_nota';
    }

    public function getDescription(): string
    {
        return 'Recupera los detalles de una nota específica por su número identificador cuando se requiere verificar su información.';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [

            ],
            'required' => [],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $pdo = null;
        try {
        $pdo = new PDO(
            'mysql:host=192.168.4.148;port=3306;dbname=barquisimeto;charset=utf8mb4',
            'jonaiber', 'Crist2026.',
            [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC]
        );
            $stmt = $pdo->prepare(self::QUERY);
            // Sin parámetros: consulta fija.
            $stmt->execute();
            $filas = $stmt->fetchAll();
            $total = count($filas);
            $cb = CardBuilder::iniciar('🤖', 'Recupera los detalles de una nota específica por su número identificador cuando se requiere verificar su información.');
            if ($total > 0) {
                $cb->seccion('Resultados (' . $total . ')');
                $cb->fila(array_keys($filas[0]));
                foreach (array_slice($filas, 0, 11) as $fila) {
                    $cb->fila(array_values(array_map('strval', $fila)));
                }
                if ($total > 11) {
                    $cb->linea('+' . ($total - 11) . ' fila(s) más (no mostradas).');
                }
            } else {
                $cb->linea('Sin resultados.');
            }
            $cb->footer('MySQL legacy (barquisimeto, 192.168.4.148) — skill auto-generada por ARA Coder');
            $card = $cb->tarjeta();
            return ['ok' => true, 'filas' => $filas, 'total' => $total, 'card' => $card];
        } catch (\Throwable $e) {
            return ['ok' => false, 'error' => $e->getMessage()];
        } finally {
            $pdo = null;
            gc_collect_cycles();
        }
    }
}
