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
final class NotaEntrega2345Tool implements AgentToolInterface
{
    private const QUERY = 'SELECT * FROM notas_entrega WHERE numero_nota LIKE :numero_nota';

    public function getName(): string
    {
        return 'obtener_nota_entrega_2_3_4_5';
    }

    public function getDescription(): string
    {
        return 'Consulta el tiempo de vida de una nota de entrega específica por su número.';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'numero_nota' => ['type' => 'string', 'description' => 'Valor para el filtro numero_nota.'],
            ],
            'required' => ['numero_nota'],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $pdo = null;
        try {
        $pdo = new PDO('sqlite:C:\\ARA_PROYECT\\ara\\ARA_Brain\\data\\proyecto_ara.db', null, null, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
            $stmt = $pdo->prepare(self::QUERY);
            $stmt->bindValue(':numero_nota', '%' . (string) ($arguments['numero_nota'] ?? '') . '%');
            $stmt->execute();
            $filas = $stmt->fetchAll();
            $total = count($filas);
            $cb = CardBuilder::iniciar('🤖', 'Consulta el tiempo de vida de una nota de entrega específica por su número.');
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
            $cb->footer('SQLite ARA Warehouse — skill auto-generada por ARA Coder');
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
