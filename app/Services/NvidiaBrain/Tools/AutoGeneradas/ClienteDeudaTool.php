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
final class ClienteDeudaTool implements AgentToolInterface
{
    private const QUERY = 'SELECT co_cli, cli_des FROM clientes WHERE co_cli = :co_cli';

    public function getName(): string
    {
        return 'obtener_deuda_cliente';
    }

    public function getDescription(): string
    {
        return 'Obtiene la deuda de un cliente específico por su código, útil para consultas de saldo o estado de cuenta.';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'co_cli' => ['type' => 'string', 'description' => 'Valor para el filtro co_cli.'],
            ],
            'required' => ['co_cli'],
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
            $stmt->bindValue(':co_cli', (string) ($arguments['co_cli'] ?? ''));
            $stmt->execute();
            $filas = $stmt->fetchAll();
            return ['ok' => true, 'filas' => $filas, 'total' => count($filas)];
        } catch (\Throwable $e) {
            return ['ok' => false, 'error' => $e->getMessage()];
        }
    }
}
