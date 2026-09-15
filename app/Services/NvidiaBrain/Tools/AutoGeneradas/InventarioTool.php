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
final class InventarioTool implements AgentToolInterface
{
    private const QUERY = 'SELECT co_art, art_des, stock_act, prec_vta1, co_prov, ubicacion FROM art WHERE art_des LIKE :art_des OR art_des LIKE :art_des_2 OR art_des LIKE :art_des_3';

    public function getName(): string
    {
        return 'buscar_productos';
    }

    public function getDescription(): string
    {
        return 'Busca artículos en el inventario por su nombre o descripción. Úsala para obtener detalles como stock, precio y ubicación de un producto específico.';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'art_des' => ['type' => 'string', 'description' => 'Valor para el filtro art_des.'],
                'art_des_2' => ['type' => 'string', 'description' => 'Valor para el filtro art_des.'],
                'art_des_3' => ['type' => 'string', 'description' => 'Valor para el filtro art_des.'],
            ],
            'required' => ['art_des', 'art_des_2', 'art_des_3'],
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
            $stmt->bindValue(':art_des', '%' . (string) ($arguments['art_des'] ?? '') . '%');
            $stmt->bindValue(':art_des_2', '%' . (string) ($arguments['art_des_2'] ?? '') . '%');
            $stmt->bindValue(':art_des_3', '%' . (string) ($arguments['art_des_3'] ?? '') . '%');
            $stmt->execute();
            $filas = $stmt->fetchAll();
            $total = count($filas);
            $cb = CardBuilder::iniciar('🤖', 'Busca artículos en el inventario por su nombre o descripción. Úsala para obtener detalles como stock, precio y ubicación de un producto específico.');
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
