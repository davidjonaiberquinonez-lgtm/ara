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
final class ArticuloServiceTool implements AgentToolInterface
{
    private const QUERY = 'SELECT TOP 3 co_art, art_des FROM art WHERE co_art = :co_art';

    public function getName(): string
    {
        return 'obtener_articulo_por_codigo';
    }

    public function getDescription(): string
    {
        return 'Obtiene los primeros tres registros de un artículo específico por su código, útil para validar existencia o detalles básicos.';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'co_art' => ['type' => 'string', 'description' => 'Valor para el filtro co_art.'],
            ],
            'required' => ['co_art'],
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
            $stmt->bindValue(':co_art', (string) ($arguments['co_art'] ?? ''));
            $stmt->execute();
            $filas = $stmt->fetchAll();
            $total = count($filas);
            $cb = CardBuilder::iniciar('🤖', 'Obtiene los primeros tres registros de un artículo específico por su código, útil para validar existencia o detalles básicos.');
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
