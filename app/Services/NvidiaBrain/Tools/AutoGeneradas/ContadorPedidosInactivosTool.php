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
final class ContadorPedidosInactivosTool implements AgentToolInterface
{
    private const QUERY = 'SELECT TOP 1 CONVERT(date, f.fec_emis) AS ultimo_dia, COUNT(DISTINCT f.co_cli) AS clientes_inactivos, COUNT(DISTINCT r.co_art) AS sku_distintos FROM factura f INNER JOIN clientes c ON f.co_cli = c.co_cli INNER JOIN reng_fac r ON f.fact_num = r.fact_num WHERE c.inactivo = :inactivo AND f.anulada = :anulada AND r.anulado = :anulado GROUP BY CONVERT(date, f.fec_emis) ORDER BY ultimo_dia DESC';

    public function getName(): string
    {
        return 'contar_clientes_inactivos_y_sku';
    }

    public function getDescription(): string
    {
        return 'Esta función cuenta la cantidad de clientes inactivos que realizan pedidos diariamente y el número de SKU distintos involucrados en esos pedidos, excluyendo aquellos que estén anulados.';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'inactivo' => ['type' => 'number', 'description' => 'Valor para el filtro inactivo.'],
                'anulada' => ['type' => 'number', 'description' => 'Valor para el filtro anulada.'],
                'anulado' => ['type' => 'number', 'description' => 'Valor para el filtro anulado.'],
            ],
            'required' => ['inactivo', 'anulada', 'anulado'],
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
            $stmt->bindValue(':anulado', (float) ($arguments['anulado'] ?? 0));
            $stmt->execute();
            $filas = $stmt->fetchAll();
            $total = count($filas);
            $cb = CardBuilder::iniciar('🤖', 'Esta función cuenta la cantidad de clientes inactivos que realizan pedidos diariamente y el número de SKU distintos involucrados en esos pedidos, excluyendo aquellos que estén anulados.');
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
