<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Auditoria;

use App\Services\ConnectionWrapper;
use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;
use PDOException;

require_once __DIR__ . '/../Common/CardBuilder.php';

/**
 * Herramienta "clientes_sin_pedido_semana": detecta clientes FRECUENTES
 * (que sí venían comprando seguido) que NO han hecho ningún pedido en los
 * últimos 7 días corridos — señal de caída real, no clientes dormidos de
 * siempre.
 *
 * Regla de negocio (a pedido del usuario, definida vía aclaración explícita):
 *  - "Frecuente" = tuvo >= umbral_pedidos_previos facturas activas en la
 *    ventana de 60 días ANTERIOR a los últimos 7 días (o sea, días -67 a -7).
 *  - "Esta semana" = últimos 7 días corridos (ventana móvil, no semana
 *    calendario) desde hoy.
 *  - Se excluyen los que SÍ facturaron algo en esos últimos 7 días.
 *
 * Fuente: Profit `factura` (fec_emis, co_cli, anulada) vía ConnectionWrapper
 * (SELECT-only, NOLOCK, PRUEB25).
 *
 * Contrato de retorno (SIEMPRE array serializable, sin excepciones):
 *   ['success' => true,  'data' => [...]]      → éxito
 *   ['success' => false, 'error' => 'mensaje'] → error controlado
 */
final class ClientesSinPedidoSemanaTool implements AgentToolInterface
{
    private const LIMITE = 30;
    private const VENTANA_PREVIA_DIAS = 60;
    private const UMBRAL_PEDIDOS_PREVIOS = 3;

    public function getName(): string
    {
        return 'clientes_sin_pedido_semana';
    }

    public function getDescription(): string
    {
        return 'Detecta clientes FRECUENTES (que venían comprando seguido) que '
             . 'NO han hecho ningún pedido/factura en los últimos 7 días corridos '
             . '— señal de caída real, no clientes que casi nunca compran. '
             . '"Frecuente" = tuvo varias facturas en los 60 días previos a esta '
             . 'semana. Úsala cuando el usuario pida detectar clientes que dejaron '
             . 'de pedir o bajaron su frecuencia de compra. Parámetros: limite '
             . '(default 30, máx 100) y umbral_pedidos_previos (mínimo de '
             . 'facturas previas para contar como "frecuente", default 3).';
    }

    public function getParameters(): array
    {
        return [
            'type' => 'object',
            'properties' => [
                'limite' => [
                    'type'        => 'integer',
                    'description' => 'Máximo de clientes a devolver (default 30, máx 100).',
                ],
                'umbral_pedidos_previos' => [
                    'type'        => 'integer',
                    'description' => 'Mínimo de facturas en los 60 días previos para considerar al cliente "frecuente" (default 3).',
                ],
            ],
            'required' => [],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $limite = max(1, min(100, (int) ($arguments['limite'] ?? self::LIMITE)));
        $umbral = max(1, min(1000, (int) ($arguments['umbral_pedidos_previos'] ?? self::UMBRAL_PEDIDOS_PREVIOS)));
        $ventanaTotal = self::VENTANA_PREVIA_DIAS + 7;

        $wrapper = new ConnectionWrapper();
        try {
            $wrapper->queryProfit('SELECT 1');
        } catch (PDOException $e) {
            return ['success' => false, 'error' => 'No se pudo conectar a SQL Server: ' . self::aUtf8($e->getMessage())];
        }

        // Sin CTE (el validador SELECT-only exige que la consulta EMPIECE con
        // SELECT): subquery "frecuentes" (facturó seguido antes) LEFT JOIN
        // subquery "esta_semana" (facturó en los últimos 7 días) — se quedan
        // solo los que NO aparecen en esta_semana. umbral/días se embeben como
        // literales (validados como int arriba, nunca texto libre del usuario).
        $sql = 'SELECT TOP ' . $limite . ' frecuentes.co_cli, frecuentes.pedidos_previos'
             . ' FROM ('
             . '   SELECT co_cli, COUNT(*) AS pedidos_previos'
             . '   FROM factura'
             . '   WHERE ISNULL(anulada, 0) = 0'
             . '     AND fec_emis >= DATEADD(DAY, -' . $ventanaTotal . ', GETDATE())'
             . '     AND fec_emis <  DATEADD(DAY, -7, GETDATE())'
             . '   GROUP BY co_cli'
             . '   HAVING COUNT(*) >= ' . $umbral
             . ' ) frecuentes'
             . ' LEFT JOIN ('
             . '   SELECT DISTINCT co_cli'
             . '   FROM factura'
             . '   WHERE ISNULL(anulada, 0) = 0'
             . '     AND fec_emis >= DATEADD(DAY, -7, GETDATE())'
             . ' ) esta_semana ON esta_semana.co_cli = frecuentes.co_cli'
             . ' WHERE esta_semana.co_cli IS NULL'
             . ' ORDER BY frecuentes.pedidos_previos DESC';
        try {
            $filas = $wrapper->querySafe($sql, []);
        } catch (\InvalidArgumentException $e) {
            return ['success' => false, 'error' => 'Error al consultar clientes sin pedido: ' . $e->getMessage()];
        } catch (PDOException $e) {
            return ['success' => false, 'error' => 'Error al consultar clientes sin pedido: ' . self::aUtf8($e->getMessage())];
        }

        $codigos = array_map(static fn (array $f): string => trim((string) $f['co_cli']), $filas);
        $nombres = $this->resolverNombresClientes($wrapper, $codigos);

        $clientes = [];
        foreach ($filas as $fila) {
            $coCli = trim((string) $fila['co_cli']);
            $clientes[] = [
                'co_cli'          => $coCli,
                'nombre'          => $nombres[$coCli] ?? '',
                'pedidos_previos' => (int) $fila['pedidos_previos'],
            ];
        }

        return [
            'success' => true,
            'data'    => [
                'fuente'                  => 'profit_factura (PRUEB25)',
                'ventana_previa_dias'     => self::VENTANA_PREVIA_DIAS,
                'umbral_pedidos_previos'  => $umbral,
                'total_clientes'          => count($clientes),
                'clientes'                => $clientes,
            ],
            'card'    => $this->card($clientes, $umbral),
        ];
    }

    /** Tarjeta de clientes frecuentes sin pedido reciente (CardBuilder). */
    private function card(array $clientes, int $umbral): string
    {
        if ($clientes === []) {
            return CardBuilder::iniciar('✅', 'SIN CLIENTES FRECUENTES CAÍDOS')
                ->linea('Todos los clientes frecuentes (>= ' . $umbral . ' facturas previas) pidieron algo en los últimos 7 días.')
                ->footer('Fuente: profit_factura (PRUEB25)')
                ->tarjeta();
        }
        $card = CardBuilder::iniciar('⚠️', 'CLIENTES SIN PEDIDO ESTA SEMANA')
            ->campo('UMBRAL FRECUENCIA', '>= ' . $umbral . ' fact. previas')
            ->campo('CLIENTES CAÍDOS', count($clientes), 'numero')
            ->seccion('Por frecuencia previa (mayor a menor)');
        foreach (array_slice($clientes, 0, 15) as $c) {
            $nombre = $c['nombre'] !== '' ? $c['nombre'] : 'N/D';
            $card->linea('⚠️ ' . $c['co_cli'] . ' · ' . $nombre . ' · ' . $c['pedidos_previos'] . ' fact. en los últimos 60 días previos');
        }
        $ocultos = count($clientes) - 15;
        if ($ocultos > 0) {
            $card->linea('⏩ +' . $ocultos . ' cliente(s) más (usa "limite" para ver más).');
        }
        return $card
            ->footer('Fuente: profit_factura (PRUEB25, NOLOCK)')
            ->tarjeta();
    }

    /**
     * Nombres de clientes vía tabla `clientes` (best-effort, nunca falla).
     *
     * @param array<int,string> $codigos
     *
     * @return array<string,string> co_cli => nombre
     */
    private function resolverNombresClientes(ConnectionWrapper $wrapper, array $codigos): array
    {
        $codigos = array_values(array_unique(array_filter($codigos, static fn (string $c) => $c !== '')));
        if ($codigos === []) {
            return [];
        }
        try {
            $colNombre = $wrapper->resolverColumnas('clientes', [
                'nombre' => ['cli_des', 'nombre', 'razon_social'],
            ])['nombre'] ?? null;
            if ($colNombre === null) {
                return [];
            }
            $marks = implode(',', array_fill(0, count($codigos), '?'));
            $filas = $wrapper->querySafe(
                'SELECT co_cli, ' . self::q($colNombre) . ' AS nombre FROM clientes'
                . ' WHERE RTRIM(LTRIM(co_cli)) IN (' . $marks . ')',
                $codigos
            );
            $mapa = [];
            foreach ($filas as $fila) {
                $mapa[trim((string) $fila['co_cli'])] = trim(self::aUtf8((string) ($fila['nombre'] ?? '')));
            }
            return $mapa;
        } catch (\Throwable $e) {
            return [];
        }
    }

    public function getDefinition(): array
    {
        return [
            'name'        => $this->getName(),
            'description' => $this->getDescription(),
            'parameters'  => $this->getParameters(),
        ];
    }

    /** Envuelve un identificador validado en corchetes de SQL Server. */
    private static function q(string $identificador): string
    {
        if (preg_match('/^[A-Za-z_][A-Za-z0-9_]*$/', $identificador) !== 1) {
            throw new \RuntimeException('Identificador SQL inválido: ' . $identificador);
        }
        return '[' . $identificador . ']';
    }

    /** Normaliza texto del ERP a UTF-8 (defensa adicional). */
    private static function aUtf8(string $texto): string
    {
        $limpio = trim($texto);
        if (mb_check_encoding($limpio, 'UTF-8')) {
            return $limpio;
        }
        $convertido = mb_convert_encoding($limpio, 'UTF-8', 'CP1252');
        return is_string($convertido) ? $convertido : $limpio;
    }
}
