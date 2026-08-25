<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Despacho;

use App\Services\ConnectionWrapper;
use App\Services\NvidiaBrain\Contracts\AgentToolInterface;
use App\Services\NvidiaBrain\Tools\Almacen\AlmacenDbTrait;
use App\Services\NvidiaBrain\Tools\Common\CardBuilder;
use DateTimeImmutable;
use PDO;
use PDOException;

/**
 * Herramienta "consultar_cliente": perfil maestro + estado de cuenta de un
 * cliente directamente desde Profit SQL Server (PRUEB25) — directiva CTO v4.11.
 *
 * Conexión directa (NO HTTP ficticio): la tabla `clientes` de PRUEB25 se lee
 * con WITH (NOLOCK), solo lectura, timeout 5s y cierre garantizado en finally.
 *
 * Resolución del parámetro (fallback progresivo clave → posición → regex):
 *  1. co_cli exacto: WHERE RTRIM(LTRIM(co_cli)) = ?            (clave exacta)
 *  2. co_cli parcial: WHERE RTRIM(LTRIM(co_cli)) LIKE ?        (comodín)
 *  3. busqueda:       WHERE cli_des LIKE '%termino%'           (regex LIKE)
 *
 * El estado de cuenta (documentos pendientes) se completa de forma opcional
 * consultando el API ARA real (env ARA_ERP_URL) si está disponible; si no lo
 * está, la tarjeta queda igualmente servida con el maestro de Profit.
 *
 * Salida: SIEMPRE tarjeta Markdown en 'card' (nunca JSON crudo al chat).
 */
final class ConsultarClienteTool implements AgentToolInterface
{
    use AlmacenDbTrait;

    /** @var string URL base del API ARA (opcional, para el estado de cuenta). */
    private string $baseUrl;

    /** @var int Timeout total de la consulta SQL/HTTP en segundos. */
    private int $timeoutS;

    /** @var int Límite de candidatos devueltos por búsqueda. */
    private int $limite;

    public function __construct(?string $baseUrl = null, int $timeoutS = 5, int $limite = 100)
    {
        $env = getenv('ARA_ERP_URL');
        $this->baseUrl = rtrim($baseUrl ?: (is_string($env) && $env !== '' ? $env : ''), '/');
        $this->timeoutS = max(1, $timeoutS);
        $this->limite = max(1, min(200, $limite));
    }

    public function getName(): string
    {
        return 'consultar_cliente';
    }

    public function getDescription(): string
    {
        return 'Consulta el perfil maestro de un cliente (código, razón social, '
             . 'rif, nit, teléfono, límite de crédito, vendedor, saldo) directo '
             . 'de Profit SQL (PRUEB25) y su estado de cuenta (documentos '
             . 'pendientes) si el ERP responde. Úsala cuando el usuario pregunte '
             . 'por un cliente, su saldo, crédito o documentos por cobrar. Se '
             . 'invoca con co_cli (código exacto o parcial) o con busqueda '
             . '(texto parcial de la razón social).';
    }

    public function getParameters(): array
    {
        return [
            'type'       => 'object',
            'properties' => [
                'co_cli' => [
                    'type'        => 'string',
                    'description' => 'Código del cliente en Profit (ej. "FAR01361", "01361" o "FAR0"). Opcional si se envía busqueda.',
                ],
                'busqueda' => [
                    'type'        => 'string',
                    'description' => 'Texto parcial de la razón social para localizar el cliente (ej. "FARMACIA BOTIMARKET"). Opcional si se envía co_cli.',
                ],
                'telefono' => [
                    'type'        => 'string',
                    'description' => 'Número de teléfono del cliente (acepta con o sin +58, espacios o guiones — se compara solo por dígitos). Opcional si se envía co_cli o busqueda.',
                ],
            ],
            'required'   => [],
        ];
    }

    public function execute(array $arguments, array $contexto): array
    {
        $coCli = trim((string) ($arguments['co_cli'] ?? ''));
        $busqueda = trim((string) ($arguments['busqueda'] ?? ''));
        $telefono = trim((string) ($arguments['telefono'] ?? ''));

        if ($coCli === '' && $busqueda === '' && $telefono === '') {
            return [
                'ok'    => false,
                'error' => 'Debe enviar "co_cli" (código del cliente), "busqueda" (texto parcial de la razón social) o "telefono".',
            ];
        }

        $inicio = microtime(true);
        try {
            $clientes = $this->buscarEnProfit($coCli, $busqueda, $telefono);
        } catch (\Throwable $e) {
            return [
                'ok'       => false,
                'error'    => 'No se pudo consultar Profit (PRUEB25): ' . $e->getMessage(),
                'origen'   => 'profit_sql',
                'tiempo_ms' => (int) round((microtime(true) - $inicio) * 1000),
            ];
        }
        $tiempoMs = (int) round((microtime(true) - $inicio) * 1000);

        // ── Modo búsqueda: candidatos para que el LLM resuelva el co_cli. ──
        if ($coCli === '') {
            $card = $this->cardBusqueda($busqueda !== '' ? $busqueda : $telefono, $clientes, $tiempoMs);
            return [
                'ok'          => true,
                'es_busqueda' => true,
                'mensaje'     => sprintf('Se encontraron %d cliente(s). Elija uno con su co_cli.', count($clientes)),
                'clientes'    => $clientes,
                'card'        => $card,
                'origen'      => 'profit_sql',
                'tiempo_ms'   => $tiempoMs,
            ];
        }

        if ($clientes === []) {
            return [
                'ok'       => false,
                'mensaje'  => sprintf('Cliente "%s" no encontrado en Profit (PRUEB25).', $coCli),
                'origen'   => 'profit_sql',
                'card'     => (new CardBuilder())
                    ->iniciar('🚫', 'CLIENTE NO ENCONTRADO')
                    ->campo('CÓDIGO', $coCli)
                    ->footer('Fuente: Profit PRUEB25 (NOLOCK) · ' . $tiempoMs . ' ms')
                    ->tarjeta(),
                'tiempo_ms' => $tiempoMs,
            ];
        }

        // ── Modo ficha completa (maestro Profit + estado de cuenta opcional). ─
        $cliente = $clientes[0];
        $pendientes = [];
        $saldoTotal = 0.0;
        $estadoOrigen = 'no_disponible';

        if ($this->baseUrl !== '') {
            $estado = $this->estadoCuentaApi($cliente['co_cli']);
            if ($estado !== null) {
                $pendientes = $estado['documentos_pendientes'] ?? [];
                $saldoTotal = (float) ($estado['saldo_total_pendiente'] ?? 0);
                $estadoOrigen = 'ara_api';
            }
        }

        // ── AD7: últimas consultas del cliente (app_log_consultas, MySQL) ──
        $advertencias = [];
        $ultimasConsultas = $this->ultimasConsultas($cliente['co_cli'], $contexto, $advertencias);

        $card = $this->cardFicha($cliente, $pendientes, $saldoTotal, $ultimasConsultas, $tiempoMs, $estadoOrigen);

        $resumen = sprintf(
            'Cliente %s (%s). Saldo %s; %d documento(s) pendiente(s) con saldo acumulado de %s.',
            $cliente['co_cli'],
            $cliente['razon_social'] !== '' ? $cliente['razon_social'] : 'sin razón social',
            number_format($cliente['saldo'], 2, ',', '.'),
            count($pendientes),
            number_format($saldoTotal, 2, ',', '.')
        );

        return [
            'status'                => 'OK',
            'co_cli'                => $cliente['co_cli'],
            'razon_social'          => $cliente['razon_social'],
            'rif'                   => $cliente['rif'],
            'nit'                   => $cliente['nit'],
            'telefono'              => $cliente['telefono'],
            'telefonos'             => $cliente['telefonos'],
            'direccion'             => $cliente['direccion'],
            'email'                 => $cliente['email'],
            'limite_credito'        => $cliente['limite_credito'],
            'saldo'                 => $cliente['saldo'],
            'saldo_actual'          => $cliente['saldo_actual'],
            'credito_disponible'    => $cliente['credito_disponible'],
            'vendedor'              => $cliente['vendedor'],
            'inactivo'              => $cliente['inactivo'],
            'documentos_pendientes' => $pendientes,
            'saldo_total_pendiente' => $saldoTotal,
            'ultimas_consultas'     => $ultimasConsultas,
            'advertencias'          => $advertencias,
            'resumen'               => $resumen,
            'card'                  => $card,
            'origen'                => 'profit_sql',
            'estado_cuenta_origen'  => $estadoOrigen,
            'tiempo_ms'             => $tiempoMs,
            'ok'                    => true,
            'cliente'               => $cliente['razon_social'],
        ];
    }

    /**
     * Fecha/hora actual del servidor SQL Server (GETDATE) — AD7.
     */
    private function fechaServidor(): DateTimeImmutable
    {
        try {
            $wrapper = new ConnectionWrapper(timeoutS: 5);
            $filas = $wrapper->querySafe('SELECT GETDATE() AS ahora', [], 0);
            $fila = $filas[0] ?? null;
            if (is_array($fila) && !empty($fila['ahora'])) {
                $dt = DateTimeImmutable::createFromFormat(
                    'Y-m-d H:i:s',
                    (string) substr((string) $fila['ahora'], 0, 19)
                );
                if ($dt !== false) {
                    return $dt;
                }
            }
        } catch (\Throwable $e) {
            // fallback a la hora local del proceso.
        }
        return new DateTimeImmutable();
    }

    /**
     * Últimas consultas del cliente en app_log_consultas (MySQL legacy) con
     * filtro fecha_consulta >= (ahora servidor - 2 meses). Registra la
     * consulta actual best-effort. AD7 / v4.17.
     *
     * @param array<int,string> $advertencias (por referencia)
     *
     * @return array<int,array<string,mixed>>
     */
    private function ultimasConsultas(string $coCli, array $contexto, array &$advertencias): array
    {
        try {
            $pdo = $this->conectarMySQL();
        } catch (PDOException $e) {
            $advertencias[] = 'MySQL legacy no disponible para historial de consultas: ' . self::aUtf8($e->getMessage());
            return [];
        }
        try {
            $pdo->exec(
                'CREATE TABLE IF NOT EXISTS `app_log_consultas` ('
                . '`id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, `co_cli` VARCHAR(20) NOT NULL,'
                . '`usuario` VARCHAR(80) NOT NULL DEFAULT "", `fecha_consulta` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,'
                . '`detalle` VARCHAR(255) NULL,'
                . 'KEY `idx_app_log_consultas_cli` (`co_cli`), KEY `idx_app_log_consultas_fecha` (`fecha_consulta`))'
                . ' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'
            );
        } catch (PDOException $e) {
            $pdo = null;
            $advertencias[] = 'No se pudo garantizar app_log_consultas: ' . self::aUtf8($e->getMessage());
            return [];
        }

        // Corte: ahora del servidor SQL Server menos 2 meses (AD7).
        $corte = $this->fechaServidor()->modify('-2 months')->format('Y-m-d H:i:s');
        try {
            $stmt = $pdo->prepare(
                'SELECT `usuario`, `fecha_consulta`, `detalle` FROM `app_log_consultas`'
                . ' WHERE `co_cli` = ? AND `fecha_consulta` >= ?'
                . ' ORDER BY `fecha_consulta` DESC LIMIT 10'
            );
            $stmt->execute([$coCli, $corte]);
            $filas = $stmt->fetchAll(PDO::FETCH_ASSOC);
        } catch (PDOException $e) {
            $pdo = null;
            $advertencias[] = 'Historial de consultas no consultable: ' . self::aUtf8($e->getMessage());
            return [];
        }

        $resumen = [];
        foreach ($filas as $fila) {
            $resumen[] = [
                'usuario'        => self::aUtf8((string) ($fila['usuario'] ?? '')),
                'fecha_consulta' => (string) ($fila['fecha_consulta'] ?? ''),
                'detalle'        => self::aUtf8((string) ($fila['detalle'] ?? '')),
            ];
        }

        // Registrar la consulta actual (best-effort, sin romper la lectura).
        $usuario = trim((string) ($contexto['usuario'] ?? $contexto['user'] ?? ''));
        try {
            $ins = $pdo->prepare(
                'INSERT INTO `app_log_consultas` (`co_cli`, `usuario`, `detalle`) VALUES (?, ?, ?)'
            );
            $ins->execute([$coCli, $usuario, 'Consulta de ficha desde adaptador consultar_cliente']);
        } catch (PDOException $e) {
            $advertencias[] = 'Consulta no registrada en app_log_consultas: ' . self::aUtf8($e->getMessage());
        }
        $pdo = null;
        return $resumen;
    }

    /**
     * Lectura directa de la tabla `clientes` de PRUEB25 con NOLOCK.
     *
     * Fallback de parámetros: co_cli exacto → co_cli comodín → razón social.
     *
     * @return list<array<string,mixed>>
     */
    /** Últimos N dígitos del teléfono, como patrón LIKE con comodín entre
     *  cada dígito para tolerar separadores en la columna (guiones, espacios,
     *  '+58' vs '58' vs '0' de prefijo local — todo cae fuera del núcleo). */
    private function patronTelefono(string $telefono, int $largo = 10): string
    {
        $digitos = preg_replace('/\D+/', '', $telefono) ?? '';
        $core = substr($digitos, -$largo);
        if ($core === '') {
            return '%';
        }
        return '%' . implode('%', str_split($core)) . '%';
    }

    private function buscarEnProfit(string $coCli, string $busqueda, string $telefono = ''): array
    {
        $wrapper = new ConnectionWrapper(timeoutS: $this->timeoutS);
        // AD7: dirección resuelta dinámicamente (no rompe si no existe).
        $colDir = $wrapper->resolverColumnas('clientes', [
            'direccion' => ['direccion', 'direc1', 'direccion1', 'dir1', 'direccion_fiscal'],
        ])['direccion'] ?? null;

        $select = 'SELECT TOP(' . $this->limite . ') '
            . 'co_cli, cli_des, rif, nit, telefonos, email, mont_cre, saldo, '
            . 'co_ven, inactivo, estado '
            . ($colDir !== null ? ', ' . $colDir . ' AS direccion' : '')
            . ' FROM clientes WITH (NOLOCK) ';
        try {
            if ($coCli !== '') {
                $filas = $wrapper->querySafe($select . 'WHERE RTRIM(LTRIM(co_cli)) = :c ORDER BY cli_des', [':c' => $coCli], 0);
                if ($filas === []) {
                    $filas = $wrapper->querySafe($select . 'WHERE RTRIM(LTRIM(co_cli)) LIKE :c ORDER BY cli_des', [':c' => '%' . $coCli . '%'], 0);
                }
            } elseif ($telefono !== '') {
                // La columna telefonos guarda formato libre ("0412-1234567",
                // "+58 412 1234567", etc.) sin normalizar — no hay forma
                // limpia de "quitar separadores" en T-SQL sin CLR/REGEXP. Se
                // usan los últimos 10 dígitos (código de operadora + número,
                // el tramo estable entre formato local y con +58) y se arma
                // un LIKE con comodín entre cada dígito para tolerar
                // cualquier separador intermedio real en la columna.
                //
                // BÚSQUEDA EN 2 NIVELES (a pedido, dado el desastre real de
                // cómo quedan registrados los teléfonos en Profit — dígitos
                // de menos, prefijo mal puesto, un guion en el lugar
                // equivocado, etc.): primero se intenta con los últimos 10
                // dígitos (preciso, pocos falsos positivos). Si NO hay
                // resultados, se repite con solo los últimos 5 dígitos —
                // mucho más laxo (puede traer varios candidatos), pero
                // encuentra al cliente aunque el resto del número esté mal
                // cargado. Nunca al revés: el nivel preciso siempre se
                // prueba primero para no diluir un match bueno con ruido.
                $filas = $wrapper->querySafe($select . 'WHERE telefonos LIKE :t ORDER BY cli_des', [':t' => $this->patronTelefono($telefono, 10)], 0);
                if ($filas === []) {
                    $filas = $wrapper->querySafe($select . 'WHERE telefonos LIKE :t ORDER BY cli_des', [':t' => $this->patronTelefono($telefono, 5)], 0);
                }
            } else {
                $filas = $wrapper->querySafe($select . 'WHERE cli_des LIKE :b ORDER BY cli_des', [':b' => '%' . $busqueda . '%'], 0);
            }
        } finally {
            $wrapper = null;
        }

        $clientes = [];
        foreach ($filas as $f) {
            $telefono = trim((string) ($f['telefonos'] ?? ''));
            $limite = (float) ($f['mont_cre'] ?? 0);
            $saldo = (float) ($f['saldo'] ?? 0);
            $clientes[] = [
                'co_cli'             => trim((string) ($f['co_cli'] ?? '')),
                'razon_social'       => trim((string) ($f['cli_des'] ?? '')),
                'rif'                => trim((string) ($f['rif'] ?? '')),
                'nit'                => trim((string) ($f['nit'] ?? '')),
                'telefono'           => $telefono,
                'telefonos'          => $this->listaTelefonos($telefono),
                'email'              => trim((string) ($f['email'] ?? '')),
                'direccion'          => $colDir !== null ? trim((string) ($f['direccion'] ?? '')) : '',
                'limite_credito'     => $limite,
                'saldo'              => $saldo,
                'saldo_actual'       => $saldo,
                'credito_disponible' => max(0.0, $limite - $saldo),
                'vendedor'           => trim((string) ($f['co_ven'] ?? '')),
                'inactivo'           => (int) ($f['inactivo'] ?? 0) === 1,
                'estado'             => trim((string) ($f['estado'] ?? '')),
            ];
        }
        return $clientes;
    }

    /**
     * Divide el campo telefonos (separadores , ; /) en un arreglo (AD7).
     *
     * @return array<int,string>
     */
    private function listaTelefonos(string $telefono): array
    {
        $telefono = trim($telefono);
        if ($telefono === '') {
            return [];
        }
        $partes = preg_split('/[,;\s\/]+/', $telefono) ?: [$telefono];
        $limpio = [];
        foreach ($partes as $p) {
            $p = trim($p);
            if ($p !== '') {
                $limpio[] = $p;
            }
        }
        return $limpio;
    }

    /**
     * Estado de cuenta opcional vía API ARA (documentos pendientes).
     * Fallo silencioso: devuelve null si el API no responde o falla.
     *
     * @return array<string,mixed>|null
     */
    private function estadoCuentaApi(string $coCli): ?array
    {
        if (!function_exists('curl_init')) {
            return null;
        }
        $ch = curl_init();
        if ($ch === false) {
            return null;
        }
        try {
            curl_setopt_array($ch, [
                CURLOPT_URL            => $this->baseUrl . '/api/cliente/estado_cuenta?co_cli=' . rawurlencode($coCli),
                CURLOPT_RETURNTRANSFER => true,
                CURLOPT_TIMEOUT        => $this->timeoutS,
                CURLOPT_CONNECTTIMEOUT => 3,
                CURLOPT_HTTPHEADER     => ['Accept: application/json'],
            ]);
            $raw = curl_exec($ch);
            $status = (int) curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
            if ($raw === false || $status < 200 || $status >= 300) {
                return null;
            }
            $datos = json_decode((string) $raw, true);
            if (!is_array($datos) || ($datos['status'] ?? '') !== 'success') {
                return null;
            }
            return [
                'documentos_pendientes'  => $datos['documentos_pendientes'] ?? [],
                'saldo_total_pendiente'  => (float) ($datos['saldo_total_pendiente'] ?? 0),
            ];
        } finally {
            curl_close($ch);
        }
    }

    /** Tarjeta del modo búsqueda (candidatos). */
    private function cardBusqueda(string $termino, array $clientes, int $tiempoMs): string
    {
        $card = CardBuilder::iniciar('🔎', 'BÚSQUEDA DE CLIENTES' . ($termino !== '' ? ': "' . $termino . '"' : ''))
            ->seccion('Candidatos (usa su co_cli)');
        foreach (array_slice($clientes, 0, 12) as $c) {
            $linea = $c['co_cli'] . ' — ' . $c['razon_social']
                . ($c['rif'] !== '' ? ' (' . $c['rif'] . ')' : '')
                . ($c['inactivo'] ? ' ⚠️' : '');
            $card->linea($linea);
        }
        $ocultos = count($clientes) - 12;
        if ($ocultos > 0) {
            $card->linea('⏩ +' . $ocultos . ' cliente(s) más en la consulta.');
        }
        return $card
            ->footer('Fuente: Profit PRUEB25 (NOLOCK) · ' . $tiempoMs . ' ms')
            ->tarjeta();
    }

    /** Tarjeta de la ficha completa del cliente. */
    private function cardFicha(array $c, array $pendientes, float $saldoTotal, array $ultimasConsultas, int $tiempoMs, string $estadoOrigen): string
    {
        $card = CardBuilder::iniciar('🏢', 'FICHA DEL CLIENTE ' . $c['co_cli'])
            ->seccion('Datos Maestros')
            ->campo('RAZÓN SOCIAL', $c['razon_social'])
            ->campo('RIF', $c['rif'])
            ->campo('NIT', $c['nit'])
            ->campo('TELÉFONO', $c['telefono'])
            ->campo('EMAIL', $c['email'])
            ->campo('LÍMITE CRÉDITO', $c['limite_credito'], 'moneda')
            ->campo('SALDO', $c['saldo'], 'moneda')
            ->campo('CRÉDITO DISPONIBLE', $c['credito_disponible'], 'moneda')
            ->campo('VENDEDOR', $c['vendedor'] !== '' ? $c['vendedor'] : 'N/A')
            ->campo('ESTADO', $c['inactivo'] ? '⚠️ INACTIVO' : 'ACTIVO');
        if (($c['direccion'] ?? '') !== '') {
            $card->campo('DIRECCIÓN', (string) $c['direccion'], 'texto');
        }
        if (($c['telefonos'] ?? []) !== []) {
            $card->campo('TELÉFONOS', implode(', ', $c['telefonos']), 'texto');
        }

        if ($pendientes !== []) {
            $card->seccion('Estado de Cuenta');
            foreach (array_slice($pendientes, 0, 8) as $d) {
                $card->campo(
                    $d['tipo'] . ' ' . $d['numero'],
                    $d['fecha'] . ' · saldo ' . number_format((float) $d['saldo'], 2, ',', '.'),
                    'texto'
                );
            }
            $card->campo('SALDO ACUMULADO', $saldoTotal, 'moneda');
        } elseif ($estadoOrigen === 'ara_api') {
            $card->seccion('Estado de Cuenta')->campo('DOCUMENTOS PENDIENTES', 0, 'numero');
        }

        if ($ultimasConsultas !== []) {
            $card->seccion('Últimas consultas (2 meses)');
            foreach (array_slice($ultimasConsultas, 0, 5) as $uc) {
                $card->linea('🕒 ' . ($uc['fecha_consulta'] !== '' ? $uc['fecha_consulta'] : 's/fecha')
                    . ($uc['usuario'] !== '' ? ' · ' . $uc['usuario'] : ''));
            }
        }

        return $card
            ->footer('Fuente: Profit PRUEB25 (NOLOCK) · ' . $tiempoMs . ' ms')
            ->tarjeta();
    }
}
