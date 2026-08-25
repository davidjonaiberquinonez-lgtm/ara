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
final class NotaFacturaConsultaTool implements AgentToolInterface
{
    private const QUERY = 'SELECT num_nota, factura_num FROM rutagrama_notas WHERE num_nota = :num_nota';

    public function getName(): string
    {
        return 'obtener_nota_y_factura';
    }

    public function getDescription(): string
    {
        return 'Obtiene la nota y la factura asociada a partir de un número de nota específico';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'num_nota' => ['type' => 'number', 'description' => 'Valor para el filtro num_nota.'],
            ],
            'required' => ['num_nota'],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        try {
        $pdo = new PDO('sqlite:C:\\ARA_PROYECT\\ara\\ARA_Brain\\data\\proyecto_ara.db', null, null, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
            $stmt = $pdo->prepare(self::QUERY);
            $stmt->bindValue(':num_nota', (float) ($arguments['num_nota'] ?? 0));
            $stmt->execute();
            $filas = $stmt->fetchAll();
            return ['ok' => true, 'filas' => $filas, 'total' => count($filas)];
        } catch (\Throwable $e) {
            return ['ok' => false, 'error' => $e->getMessage()];
        }
    }
}
