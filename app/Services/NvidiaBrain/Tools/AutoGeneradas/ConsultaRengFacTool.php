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
final class ConsultaRengFacTool implements AgentToolInterface
{
    private const QUERY = 'SELECT * FROM reng_fac WHERE num_doc = :num_doc';

    public function getName(): string
    {
        return 'buscar_reng_fac_por_num_doc';
    }

    public function getDescription(): string
    {
        return 'Busca registros en la tabla reng_fac por número de documento.';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'num_doc' => ['type' => 'number', 'description' => 'Valor para el filtro num_doc.'],
            ],
            'required' => ['num_doc'],
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
            $stmt->bindValue(':num_doc', (float) ($arguments['num_doc'] ?? 0));
            $stmt->execute();
            $filas = $stmt->fetchAll();
            return ['ok' => true, 'filas' => $filas, 'total' => count($filas)];
        } catch (\Throwable $e) {
            return ['ok' => false, 'error' => $e->getMessage()];
        }
    }
}
