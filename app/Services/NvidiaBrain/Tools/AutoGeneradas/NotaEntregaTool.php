<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\AutoGeneradas;

use App\Services\NvidiaBrain\Contracts\AgentToolInterface;

/**
 * Tool auto-generada por ARA Coder (servicio independiente, puerto 8010).
 * NO editar a mano — se regenera sola cuando ARA Coder resuelve una
 * consulta similar de nuevo. Pregunta que la originó (referencia, no se
 * usa en tiempo de ejecución): ver catálogo en generated_skills/catalog.json.
 */
final class NotaEntregaTool implements AgentToolInterface
{
    private const QUERY = 'SELECT numero_nota, cliente, co_cli, estado FROM notas_entrega WHERE numero_nota LIKE \'%80007967%\'';

    public function getName(): string
    {
        return 'buscar_nota_entrega';
    }

    public function getDescription(): string
    {
        return 'Busca la nota de entrega asociada a una factura por su número.';
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
        try {
        $pdo = new PDO('sqlite:C:\\ARA_PROYECT\\ara\\ARA_Brain\\data\\proyecto_ara.db', null, null, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
            $stmt = $pdo->prepare(self::QUERY);
            // Sin parámetros: consulta fija.
            $stmt->execute();
            $filas = $stmt->fetchAll();
            return ['ok' => true, 'filas' => $filas, 'total' => count($filas)];
        } catch (\Throwable $e) {
            return ['ok' => false, 'error' => $e->getMessage()];
        }
    }
}
