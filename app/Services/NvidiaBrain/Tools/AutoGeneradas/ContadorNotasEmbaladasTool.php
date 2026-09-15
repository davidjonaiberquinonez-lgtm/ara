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
final class ContadorNotasEmbaladasTool implements AgentToolInterface
{
    private const QUERY = 'SELECT DATE(hora) AS fecha, COUNT(*) AS total, SUM(CASE WHEN verifi_emb = :verifi_emb THEN 1 ELSE 0 END) AS embaladas FROM gestion WHERE cd_barr LIKE :cd_barr GROUP BY DATE(hora) ORDER BY fecha DESC LIMIT 10';

    public function getName(): string
    {
        return 'contar_notas_embaladas';
    }

    public function getDescription(): string
    {
        return 'Cuenta las notas de una sede que empiezan con un prefijo y están embaladas, agrupadas por fecha.';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'cd_barr' => ['type' => 'string', 'description' => 'Valor para el filtro cd_barr.'],
                'verifi_emb' => ['type' => 'string', 'description' => 'Valor para el filtro verifi_emb.'],
            ],
            'required' => ['cd_barr', 'verifi_emb'],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $pdo = null;
        try {
        $pdo = new PDO(
            'mysql:host=192.168.4.148;port=3306;dbname=barquisimeto;charset=utf8mb4',
            'jonaiber', 'Crist2026.',
            [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC]
        );
            $stmt = $pdo->prepare(self::QUERY);
            $stmt->bindValue(':cd_barr', (string) ($arguments['cd_barr'] ?? '') . '%');
            $stmt->bindValue(':verifi_emb', (string) ($arguments['verifi_emb'] ?? ''));
            $stmt->execute();
            $filas = $stmt->fetchAll();
            $total = count($filas);
            $cb = CardBuilder::iniciar('🤖', 'Cuenta las notas de una sede que empiezan con un prefijo y están embaladas, agrupadas por fecha.');
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
            $cb->footer('MySQL legacy (barquisimeto, 192.168.4.148) — skill auto-generada por ARA Coder');
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
