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
final class ClientesSinPedidosTool implements AgentToolInterface
{
    private const QUERY = 'SELECT TOP 5 co_cli, cli_des FROM clientes WHERE inactivo = :inactivo AND co_cli NOT IN (SELECT co_cli FROM factura WHERE fec_emis >= \'2026-08-24\' AND anulada = :anulada)';

    public function getName(): string
    {
        return 'clientes_sin_pedidos_semana';
    }

    public function getDescription(): string
    {
        return 'Obtiene los primeros 5 clientes activos que no han realizado pedidos en la semana actual.';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'inactivo' => ['type' => 'number', 'description' => 'Valor para el filtro inactivo.'],
                'anulada' => ['type' => 'number', 'description' => 'Valor para el filtro anulada.'],
            ],
            'required' => ['inactivo', 'anulada'],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $pdo = null;
        try {
        $pdo = new PDO(
            'odbc:Driver={SQL Server};Server=192.168.4.20,1433;Database=PRUEB25',
            'profit', 'profit',
            [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC]
        );
            $stmt = $pdo->prepare(self::QUERY);
            $stmt->bindValue(':inactivo', (float) ($arguments['inactivo'] ?? 0));
            $stmt->bindValue(':anulada', (float) ($arguments['anulada'] ?? 0));
            $stmt->execute();
            $filas = $stmt->fetchAll();
            $total = count($filas);
            $cb = CardBuilder::iniciar('🤖', 'Obtiene los primeros 5 clientes activos que no han realizado pedidos en la semana actual.');
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
            $cb->footer('Profit SQL Server (PRUEB25) — skill auto-generada por ARA Coder');
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
