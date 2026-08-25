<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Adapters;

use App\Services\NvidiaBrain\NvidiaBrainClient;
use App\Services\NvidiaBrain\ToolRegistry;
use App\Services\ConnectionWrapper;
use PDO;
use PDOException;
use Throwable;

/**
 * Adaptador del departamento de Tecnología (métricas e infraestructura).
 *
 * Flujo:
 *   1. Conecta a SQL Server (PROFIT_SQL_* → PROFIT_DB_* → defaults) y
 *      ejecuta el procedimiento almacenado sp_IA_Obtener_Metricas.
 *   2. Itera TODOS los conjuntos de resultados (nextRowset) y sanitiza la
 *      salida con sanitizar_utf8() para json_encode().
 *   3. AUDITORÍA DE ERRORES DE RED: captura excepciones PDO/ODBC y
 *      clasifica el fallo:
 *        - 'red'       → SQLSTATE 08S01 (communication link failure), 08001
 *                        (no connection), 08003, o errores de socket (broken
 *                        pipe, connection reset...).
 *        - 'servidor'  → login/permisos/base de datos (28000, 42000, 08004...).
 *        - 'consulta'  → el SP no existe (2812) o falla al ejecutarse.
 *      Retorna una respuesta estructurada con la recomendación, NUNCA vacía.
 *
 * Si el SP no existe se listan los requisitos para procesar la acción
 * (criterio de aceptación: sin respuestas "No sé").
 */
final class TecnologiaAdapter extends BaseAdapter
{
    /** Nombre del procedimiento almacenado de métricas. */
    public const SP_METRICAS = 'sp_IA_Obtener_Metricas';

    /** SQLSTATE de red ODBC/SQL Server. */
    private const ESTADOS_RED = ['08S01', '08001', '08003', '08004', '08006', '08007'];

    private ?PDO $pdo;

    /** @var ConnectionWrapper|null Circuit breaker de SP (Fase 2.5 T3). */
    private ?ConnectionWrapper $circuitBreaker = null;

    /**
     * @param NvidiaBrainClient|null $client   Cliente LLM (Ollama local).
     * @param ToolRegistry|null      $registry Registro de tools.
     * @param array<string,mixed>    $contexto Contexto de sesión.
     * @param PDO|null               $pdo      Conexión PDO inyectable (tests);
     *                                         si es null se abre bajo demanda.
     */
    public function __construct(
        ?NvidiaBrainClient $client = null,
        ?ToolRegistry $registry = null,
        array $contexto = [],
        ?PDO $pdo = null
    ) {
        parent::__construct($client, $registry, $contexto);
        $this->pdo = $pdo;
    }

    protected function getSystemPrompt(): string
    {
        return 'Eres el monitor de infraestructura de Proyecto ARA. Reporta '
             . 'métricas de rendimiento, errores de red ODBC con Profit Plus y '
             . 'estado de la base de datos de manera técnica y estructurada.';
    }

    /**
     * {@inheritDoc}
     *
     * Contexto opcional:
     *   - consulta (string) filtro textual de métricas (no obligatorio).
     */
    public function procesar(string $mensaje, array $contexto = []): array
    {
        $datos = array_merge($this->contexto, $contexto);
        $consulta = trim((string) ($datos['consulta'] ?? $mensaje));

        // Circuit breaker Fase 2.5 T3: si el SP falló ≥3 veces (2812) en 60s,
        // no se vuelve a golpear el servidor durante 300s (fail-fast).
        if ($this->circuitoAbierto()) {
            return $this->responder(
                'El procedimiento ' . self::SP_METRICAS . ' no está disponible. '
                . 'El circuito de consulta está temporalmente abierto; intente '
                . 'de nuevo en unos minutos.',
                ['procedimiento' => self::SP_METRICAS, 'tipo' => 'consulta'],
                'SP ' . self::SP_METRICAS . ': circuito abierto (3 fallos 2812 '
                    . 'en 60s). Fail-fast sin tocar el servidor.'
            );
        }

        try {
            $pdo = $this->pdo ?? $this->conectar();
        } catch (PDOException $e) {
            return $this->falloConexion($e);
        }

        try {
            $stmt = $pdo->query('EXEC ' . self::SP_METRICAS);
            if ($stmt === false) {
                return $this->responder(
                    'No fue posible ejecutar ' . self::SP_METRICAS . '.',
                    ['procedimiento' => self::SP_METRICAS],
                    'El SP devolvió false sin excepción.'
                );
            }

            $filas = $stmt->fetchAll(PDO::FETCH_ASSOC);
            $conjuntos = [$filas];
            while ($stmt->nextRowset() !== false) {
                $siguiente = $stmt->fetchAll(PDO::FETCH_ASSOC);
                if ($siguiente !== []) {
                    $conjuntos[] = $siguiente;
                }
            }

            // Ejecución exitosa ⇒ el SP existe: reset del circuito.
            $this->circuitBreaker()?->resetFalloSP(self::SP_METRICAS);
        } catch (PDOException $e) {
            if ($this->esSpInexistente($e)) {
                $this->circuitBreaker()?->registrarFalloSP(self::SP_METRICAS);
            }
            return $this->auditarFallo($e, $consulta);
        } catch (Throwable $e) {
            return $this->responder(
                'Ocurrió un error inesperado consultando las métricas. '
                . 'Verifique la conexión y el estado del servidor.',
                ['error_tecnico' => self::sanitizar_utf8($e->getMessage())],
                'Excepción: ' . $e->getMessage()
            );
        }

        $metrics = self::sanitizar_utf8($conjuntos);
        $totalFilas = array_sum(array_map('count', $conjuntos));

        return $this->responder(
            sprintf(
                'Métricas obtenidas de %s: %d conjunto(s) de resultados con '
                . '%d fila(s) en total. Consulte el campo "metricas" para el detalle.',
                self::SP_METRICAS,
                count($conjuntos),
                $totalFilas
            ),
            [
                'procedimiento' => self::SP_METRICAS,
                'conjuntos'     => count($conjuntos),
                'total_filas'   => $totalFilas,
                'metricas'      => $metrics,
            ]
        );
    }

    /**
     * Ejecuta directamente el SP de métricas sin inferencia de IA (Response < 40ms).
     *
     * Lectura PDO pura del Dashboard Ejecutivo: ejecuta
     * sp_IA_Obtener_Metricas e itera TODOS los conjuntos de resultados
     * (nextRowset), fusionándolos en un array plano sanitizado:
     *   {estatus_servicio, base_datos, fecha_servidor, fecha_operacion,
     *    usuario_conexion, fecha, notas_del_dia, facturas_del_dia,
     *    total_clientes, clientes_inactivos, clientes_activos}
     * donde clientes_activos = total_clientes - clientes_inactivos (el KPI
     * del dashboard). Sin llamadas al LLM: solo SQL Server + sanitización.
     *
     * @throws PDOException Si la conexión falla o el SP no responde (el
     *                      llamador decide cómo responder).
     *
     * @return array<string,mixed> Métricas planas (UTF-8 válido).
     */
    public function obtenerMetricasDirectas(): array
    {
        $pdo = $this->pdo ?? $this->conectar();

        $stmt = $pdo->prepare('EXEC ' . self::SP_METRICAS);
        $stmt->execute();

        $metricas = [];
        do {
            $fila = $stmt->fetch(PDO::FETCH_ASSOC);
            if (is_array($fila)) {
                $metricas = array_merge($metricas, $fila);
            }
        } while ($stmt->nextRowset() !== false);
        $stmt->closeCursor();

        $total = isset($metricas['total_clientes']) ? (int) $metricas['total_clientes'] : 0;
        $inactivos = isset($metricas['clientes_inactivos']) ? (int) $metricas['clientes_inactivos'] : 0;
        $metricas['clientes_activos'] = $total - $inactivos;

        return self::sanitizar_utf8($metricas);
    }

    /**
     * Clasifica y reporta un fallo PDO (auditoría de red ODBC/sockets).
     *
     * @param PDOException $e
     */
    private function auditarFallo(PDOException $e, string $consulta): array
    {
        $estado = (string) $e->getCode();
        $mensaje = $e->getMessage();
        $bajo = strtolower($mensaje);

        if (in_array($estado, self::ESTADOS_RED, true)
            || str_contains($bajo, 'broken pipe')
            || str_contains($bajo, 'connection reset')
            || str_contains($bajo, 'communication link')
            || str_contains($bajo, 'socket')) {
            return [
                'success'   => false,
                'respuesta' => 'Falló el enlace de red con Profit Plus (ODBC/SQL '
                    . 'Server). Verifique el estado de la red, el firewall y que '
                    . 'el servidor 192.168.4.20 responda en el puerto 1433.',
                'error'     => 'Error de red ODBC: ' . $estado . ' — ' . $mensaje,
                'data'      => [
                    'codigo_estado'  => $estado,
                    'tipo'           => 'red',
                    'sqlstate_08S01' => $estado === '08S01',
                    'consulta'       => substr($consulta, 0, 200),
                    'recomendacion'  => 'Verificar red/firewall hacia el servidor '
                        . 'Profit y reintentar la consulta.',
                ],
            ];
        }

        if ($estado === '2812'
            || str_contains($estado, '2812')
            || str_contains($mensaje, '2812')
            || stripos($mensaje, 'could not find') !== false
            || stripos($mensaje, 'no se encontr') !== false
            || stripos($mensaje, 'procedimiento almacenado') !== false) {
            return $this->responder(
                'El procedimiento almacenado sp_IA_Obtener_Metricas no está '
                . 'disponible en la base configurada. Se requieren: el SP '
                . 'sp_IA_Obtener_Metricas en la base (PROFIT_SQL_NAME, por '
                . 'defecto PRUEB25) y una conexión válida (PROFIT_SQL_HOST, '
                . 'PROFIT_SQL_USER, PROFIT_SQL_PASS).',
                [
                    'procedimiento' => self::SP_METRICAS,
                    'tipo'          => 'consulta',
                    'requerido'     => [
                        'sp_IA_Obtener_Metricas',
                        'conexion PROFIT_SQL_*',
                    ],
                ],
                'SP no encontrado (2812): ' . $mensaje
            );
        }

        return [
            'success'   => false,
            'respuesta' => 'El servidor de base de datos respondió un error al '
                . 'ejecutar sp_IA_Obtener_Metricas. Revise permisos y estado de '
                . 'la base (PROFIT_SQL_NAME).',
            'error'     => 'Error SQL Server: ' . $estado . ' — ' . $mensaje,
            'data'      => [
                'codigo_estado' => $estado,
                'tipo'          => 'servidor',
                'consulta'      => substr($consulta, 0, 200),
                'recomendacion' => 'Revisar permisos del usuario y estado de la '
                    . 'base de datos configurada.',
            ],
        ];
    }

    /**
     * Reporta un fallo al CONECTAR (antes de poder ejecutar el SP).
     *
     * @param PDOException $e
     */
    private function falloConexion(PDOException $e): array
    {
        $estado = (string) $e->getCode();
        $tipo = in_array($estado, self::ESTADOS_RED, true) ? 'red' : 'servidor';
        return [
            'success'   => false,
            'respuesta' => 'No se pudo establecer conexión con Profit Plus. '
                . 'Verifique que el servidor esté en línea y la configuración '
                . 'PROFIT_SQL_* (host, puerto 1433, usuario y base de datos).',
            'error'     => 'Conexión fallida (' . $estado . '): ' . $e->getMessage(),
            'data'      => [
                'codigo_estado'  => $estado,
                'tipo'           => $tipo,
                'recomendacion'  => 'Revisar conectividad y credenciales '
                    . '(PROFIT_SQL_HOST/USER/PASS/NAME).',
            ],
        ];
    }

    /**
     * Circuit breaker compartido (Fase 2.5 T3). Instancia perezosa.
     */
    private function circuitBreaker(): ConnectionWrapper
    {
        return $this->circuitBreaker ??= new ConnectionWrapper();
    }

    /**
     * ¿El circuito del SP de métricas está abierto (fail-fast sin tocar el
     * servidor)?
     */
    private function circuitoAbierto(): bool
    {
        return $this->circuitBreaker()->spBloqueado(self::SP_METRICAS);
    }

    /**
     * ¿La excepción PDO es "procedimiento almacenado inexistente" (2812)?
     */
    private function esSpInexistente(PDOException $e): bool
    {
        $estado = (string) $e->getCode();
        $mensaje = $e->getMessage();
        if ($estado === '2812' || str_contains($estado, '2812')) {
            return true;
        }
        return str_contains($mensaje, '2812')
            || stripos($mensaje, 'could not find') !== false
            || stripos($mensaje, 'no se encontr') !== false
            || stripos($mensaje, 'procedimiento almacenado') !== false;
    }
}
