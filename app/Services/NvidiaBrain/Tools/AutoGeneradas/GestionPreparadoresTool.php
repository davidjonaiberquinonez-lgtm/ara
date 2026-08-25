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
final class GestionPreparadoresTool implements AgentToolInterface
{
    private const QUERY = 'SELECT num_prep FROM gestion WHERE cd_barr = :cd_barr';

    public function getName(): string
    {
        return 'obtener_preparadores_por_codigo_barras';
    }

    public function getDescription(): string
    {
        return 'Obtiene los números de preparadores asociados a un código de barras específico.';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'cd_barr' => ['type' => 'number', 'description' => 'Valor para el filtro cd_barr.'],
            ],
            'required' => ['cd_barr'],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        try {
        $pdo = new PDO(
            'mysql:host=192.168.4.148;port=3306;dbname=barquisimeto;charset=utf8mb4',
            'jonaiber', 'Crist2026.',
            [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC]
        );
            $stmt = $pdo->prepare(self::QUERY);
            $stmt->bindValue(':cd_barr', (float) ($arguments['cd_barr'] ?? 0));
            $stmt->execute();
            $filas = $stmt->fetchAll();
            return ['ok' => true, 'filas' => $filas, 'total' => count($filas)];
        } catch (\Throwable $e) {
            return ['ok' => false, 'error' => $e->getMessage()];
        }
    }
}
