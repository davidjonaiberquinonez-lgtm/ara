<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\AutoGeneradas;

use App\Services\ConnectionWrapper;
use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use PDOException;

/**
 * Tool genérica de SQL de solo lectura contra Profit (PRUEB25), para cuando
 * el agente no tiene una tool específica ya escrita para la pregunta.
 *
 * A diferencia del resto de los archivos de esta carpeta, esta NO la generó
 * el motor de auto-síntesis de ARA Coder — está escrita a mano (16/09) para
 * que ARA Coder pueda usarla, resolver la pregunta, y RECIÉN AHÍ sintetizar
 * una tool nueva más específica y parametrizada a partir de la consulta que
 * funcionó (mismo flujo que ya usa con sus tools Python equivalentes:
 * ejecutar_consulta_sql_server_lectura). Vive en AutoGeneradas a propósito,
 * para que quede en el mismo grupo que heredan los demás consumidores del
 * catálogo de tools (ventas, call center) sin tocar código de ese lado.
 *
 * Seguridad: delega TODO el enforcement en ConnectionWrapper (mismo
 * mecanismo que ya protege al resto de ARA_PROYECT) — SELECT-only
 * (isSelectOnly), NOLOCK forzado, cierre garantizado de la conexión
 * (forceDisconnect en el finally). Esta clase solo agrega el tope de filas
 * (TOP) antes de pasarle la consulta al wrapper.
 *
 * Conecta a PRUEB25 (default de ConnectionWrapper), NO a CRISTM25 — decisión
 * explícita del usuario (16/09): esta tool arma SQL libre a partir de lo que
 * pida el LLM, y la regla Fase 2.4 (ConnectionWrapper::validarEntorno)
 * existe justo para que ese tipo de consulta nunca toque producción. Si el
 * dato de PRUEB25 queda desactualizado para alguna pregunta puntual, es un
 * costo aceptado a cambio de no exponer CRISTM25 a SQL sin curar.
 */
final class ConsultarSqlLecturaTool implements AgentToolInterface
{
    private const LIMITE_DEFAULT = 100;
    private const LIMITE_MAX = 500;

    public function getName(): string
    {
        return 'consultar_sql_lectura';
    }

    public function getDescription(): string
    {
        return 'Ejecuta una consulta SQL de SOLO LECTURA (debe empezar con SELECT) '
            . 'contra Profit (SQL Server) para responder preguntas sobre datos que no '
            . 'tienen una herramienta específica todavía — por ejemplo lotes próximos a '
            . 'vencer, reportes puntuales, cruces entre tablas. Tablas principales '
            . 'disponibles: factura (fact_num, co_cli, saldo, fec_emis, fec_venc, numcon), '
            . 'reng_fac (fact_num, reng_num, tipo_doc, num_doc — renglones y relación '
            . 'nota/factura), clientes (co_cli, nombre, ...), art (co_art, descripcion, ...), '
            . 'st_almac (co_art, existencia, ...). Cualquier consulta que no sea un SELECT '
            . 'puro (INSERT/UPDATE/DELETE/DROP/etc.) se rechaza antes de tocar el servidor.';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'sql' => [
                    'type' => 'string',
                    'description' => 'Consulta SELECT completa (sin punto y coma final ni '
                        . 'sentencias encadenadas). No hace falta agregar WITH (NOLOCK): se '
                        . 'inyecta solo.',
                ],
                'limite' => [
                    'type' => 'integer',
                    'description' => 'Tope de filas a devolver. Default 100, máximo 500.',
                ],
            ],
            'required' => ['sql'],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $sql = trim((string) ($arguments['sql'] ?? ''));
        if ($sql === '') {
            return ['ok' => false, 'error' => 'Falta el parámetro sql.'];
        }

        $limite = (int) ($arguments['limite'] ?? self::LIMITE_DEFAULT);
        $limite = max(1, min(self::LIMITE_MAX, $limite));

        $wrapper = new ConnectionWrapper();
        try {
            $filas = $wrapper->queryProfit($this->conTope($sql, $limite));
            return [
                'ok' => true,
                'total_filas' => count($filas),
                'filas' => $filas,
                'fuente' => 'Profit SQL Server (PRUEB25)',
            ];
        } catch (\InvalidArgumentException $e) {
            // Consulta rechazada por isSelectOnly() dentro del wrapper.
            return ['ok' => false, 'error' => 'Consulta rechazada (solo SELECT de lectura): ' . $e->getMessage()];
        } catch (PDOException $e) {
            return ['ok' => false, 'error' => 'Error consultando Profit: ' . $e->getMessage()];
        } finally {
            // Cierre garantizado pase lo que pase (éxito, rechazo o excepción).
            $wrapper->forceDisconnect();
        }
    }

    /**
     * Inyecta un TOP N si el SELECT no trae ya uno explícito (respeta el
     * TOP del usuario si lo puso). No usa DISTINCT/ORDER BY como pista: el
     * único caso que importa evitar es duplicar "TOP" si ya está.
     */
    private function conTope(string $sql, int $limite): string
    {
        if (preg_match('/^\s*SELECT\s+TOP\s+\d+/i', $sql) === 1) {
            return $sql;
        }
        return (string) preg_replace('/^\s*SELECT\b/i', 'SELECT TOP ' . $limite, $sql, 1);
    }
}
