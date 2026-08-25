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
final class NotaProfitTool implements AgentToolInterface
{
    private const QUERY = 'SELECT fact_num, reng_num, tipo_doc, num_doc, co_art, total_art, anulado FROM reng_fac WHERE tipo_doc = :tipo_doc AND num_doc = :num_doc';

    public function getName(): string
    {
        return 'buscar_nota_profit';
    }

    public function getDescription(): string
    {
        return 'Busca una nota en Profit usando reng_fac con tipo_doc \'E\' y num_doc específico.';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'tipo_doc' => ['type' => 'string', 'description' => 'Valor para el filtro tipo_doc.'],
                'num_doc' => ['type' => 'string', 'description' => 'Valor para el filtro num_doc.'],
            ],
            'required' => ['tipo_doc', 'num_doc'],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        try {
        $pdo = new PDO(
            'odbc:Driver={SQL Server};Server=192.168.4.20,1433;Database=PRUEB25',
            'profit', 'profit',
            [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC]
        );
            $stmt = $pdo->prepare(self::QUERY);
            $stmt->bindValue(':tipo_doc', (string) ($arguments['tipo_doc'] ?? ''));
            $stmt->bindValue(':num_doc', (string) ($arguments['num_doc'] ?? ''));
            $stmt->execute();
            $filas = $stmt->fetchAll();
            return ['ok' => true, 'filas' => $filas, 'total' => count($filas)];
        } catch (\Throwable $e) {
            return ['ok' => false, 'error' => $e->getMessage()];
        }
    }
}
