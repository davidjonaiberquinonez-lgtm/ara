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
final class RutaGramaNotasDAOTool implements AgentToolInterface
{
    private const QUERY = 'SELECT * FROM rutagrama_notas WHERE factura_num = :factura_num';

    public function getName(): string
    {
        return 'busca_factura';
    }

    public function getDescription(): string
    {
        return 'Busca en la tabla rutagrama_notas la fila donde factura_num sea igual al valor proporcionado, devuelve todos los campos de la fila correspondiente.';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'factura_num' => ['type' => 'number', 'description' => 'Valor para el filtro factura_num.'],
            ],
            'required' => ['factura_num'],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        try {
        $pdo = new PDO('sqlite:C:\\ARA_PROYECT\\ara\\ARA_Brain\\data\\proyecto_ara.db', null, null, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
            $stmt = $pdo->prepare(self::QUERY);
            $stmt->bindValue(':factura_num', (float) ($arguments['factura_num'] ?? 0));
            $stmt->execute();
            $filas = $stmt->fetchAll();
            return ['ok' => true, 'filas' => $filas, 'total' => count($filas)];
        } catch (\Throwable $e) {
            return ['ok' => false, 'error' => $e->getMessage()];
        }
    }
}
