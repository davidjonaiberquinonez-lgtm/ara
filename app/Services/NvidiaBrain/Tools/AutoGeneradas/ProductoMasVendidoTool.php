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
final class ProductoMasVendidoTool implements AgentToolInterface
{
    private const QUERY = 'SELECT TOP 10 r.co_art, a.art_des, SUM(r.total_art) AS cantidad_vendida
FROM reng_fac r
INNER JOIN factura f ON r.fact_num = f.fact_num
INNER JOIN art a ON r.co_art = a.co_art
WHERE f.fec_emis >= \'2026-08-24\' AND f.anulada = :anulada AND r.anulado = :anulado
GROUP BY r.co_art, a.art_des
ORDER BY cantidad_vendida DESC';

    public function getName(): string
    {
        return 'producto_mas_vendido';
    }

    public function getDescription(): string
    {
        return 'Obtiene el producto más vendido en una semana específica sumando cantidades de renglones de facturas no anuladas.';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'anulada' => ['type' => 'number', 'description' => 'Valor para el filtro anulada.'],
                'anulado' => ['type' => 'number', 'description' => 'Valor para el filtro anulado.'],
            ],
            'required' => ['anulada', 'anulado'],
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
            $stmt->bindValue(':anulada', (float) ($arguments['anulada'] ?? 0));
            $stmt->bindValue(':anulado', (float) ($arguments['anulado'] ?? 0));
            $stmt->execute();
            $filas = $stmt->fetchAll();
            $total = count($filas);
            $cb = CardBuilder::iniciar('🤖', 'Obtiene el producto más vendido en una semana específica sumando cantidades de renglones de facturas no anuladas.');
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
