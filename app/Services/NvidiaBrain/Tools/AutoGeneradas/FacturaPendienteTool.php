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
final class FacturaPendienteTool implements AgentToolInterface
{
    private const QUERY = 'SELECT fact_num, co_cli, saldo, fec_emis, fec_venc FROM facturas WHERE saldo > 0 LIMIT 5';

    public function getName(): string
    {
        return 'obtener_facturas_pendientes';
    }

    public function getDescription(): string
    {
        return 'Obtiene las primeras 5 facturas con saldo pendiente mayor a cero, incluyendo número, cliente, saldo y fechas.';
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
