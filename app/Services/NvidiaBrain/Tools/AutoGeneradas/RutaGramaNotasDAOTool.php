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
final class RutaGramaNotasDAOTool implements AgentToolInterface
{
    private const QUERY = 'SELECT * FROM rutagrama_notas WHERE factura_num = :factura_num';

    public function getName(): string
    {
        return 'busca_factura';
    }

    public function getDescription(): string
    {
        return 'Busca en la tabla rutagrama_notas la fila donde factura_num sea igual al valor proporcionado, devuelve todos los campos de la fila correspondiente.';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'factura_num' => ['type' => 'number', 'description' => 'Valor para el filtro factura_num.'],
            ],
            'required' => ['factura_num'],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $pdo = null;
        try {
        $pdo = new PDO('sqlite:C:\\ARA_PROYECT\\ara\\ARA_Brain\\data\\proyecto_ara.db', null, null, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
            $stmt = $pdo->prepare(self::QUERY);
            $stmt->bindValue(':factura_num', (float) ($arguments['factura_num'] ?? 0));
            $stmt->execute();
            $filas = $stmt->fetchAll();
            $total = count($filas);
            $cb = CardBuilder::iniciar('🤖', 'Busca en la tabla rutagrama_notas la fila donde factura_num sea igual al valor proporcionado, devuelve todos los campos de la fila correspondiente.');
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
            $cb->footer('SQLite ARA Warehouse — skill auto-generada por ARA Coder');
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
