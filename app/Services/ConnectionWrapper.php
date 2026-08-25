<?php

declare(strict_types=1);

namespace App\Services;

use PDO;
use PDOException;

/**
 * Wrapper de conexión SOLO LECTURA a Profit Plus (SQL Server PRUEB25).
 *
 * Orden de Ejecución Fase 2.1 (Pata 1 — v4.14): protección de conexión
 * SQL Server + optimización de skills directas.
 * Fase 2.5 (T2/T3 — v4.16): query timeout 30s (SQL_ATTR_QUERY_TIMEOUT) y
 * circuit breaker para SP inexistente (2812).
 *
 * Reglas innegociables implementadas:
 *  - SELECT-only estricto (isSelectOnly()): cualquier query con
 *    INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/EXEC/TRUNCATE/MERGE/GRANT/REVOKE,
 *    stored procedures (sp_) o extensiones peligrosas (xp_) se rechaza con
 *    excepción ANTES de tocar el servidor.
 *  - WITH (NOLOCK) forzado automáticamente en cada FROM/JOIN de tabla (no se
 *    duplica si el query ya lo trae; subconsultas parentizadas se ignoran).
 *  - Query timeout configurable (env PROFIT_QUERY_TIMEOUT, default 30s, tope
 *    600s): se aplica a nivel de conexión con SQL_ATTR_QUERY_TIMEOUT (vía
 *    odbc_setoption en el backend ODBC nativo) y, si hay driver sqlsrv, con
 *    PDO::SQLSRV_ATTR_QUERY_TIMEOUT. + SET LOCK_TIMEOUT en ms.
 *  - Timeout de CONEXIÓN (login) separado (env PROFIT_CONNECT_TIMEOUT,
 *    default 5s) para fast-fail de red.
 *  - Circuit breaker de SP: 3 fallos SQLSTATE 2812 en 60s → circuito abierto
 *    300s (ConnectionWrapperException) para no golpear el servidor con SPs
 *    inexistentes. La comprobación expone spBloqueado()/registrarFalloSP().
 *  - Cierre garantizado de la conexión en bloque finally (SIEMPRE).
 *  - Cache local SQLite (tabla cache_profit_queries) para evitar golpear
 *    Profit por la misma consulta en menos del TTL (env PROFIT_CACHE_TTL,
 *    default 60 segundos). queryProfit() salta la cache (dato ultra-fresco).
 *
 * Compatibilidad: las Skills construyen SQL dinámico con nombres de tabla y
 * columna resueltos vía INFORMATION_SCHEMA; el wrapper expone los helpers
 * resolverTabla()/resolverColumnas()/qPara() para que la refactorización no
 * rompa la lógica de negocio ni el fallback progresivo de parámetros.
 *
 * Credenciales: env PROFIT_SQL_* → PROFIT_DB_* → defaults del legado
 * (192.168.4.20:1433 / PRUEB25 / profit / profit). NADA hardcodeado nuevo.
 *
 * Uso:
 *   $wrapper = new ConnectionWrapper();
 *   $filas = $wrapper->querySafe(
 *       'SELECT co_art, descripcion FROM art WHERE co_art LIKE ?',
 *       ['%evigax%'],
 *       60
 *   );
 */
final class ConnectionWrapper
{
    /** TTL de cache por defecto (segundos). */
    public const DEFAULT_CACHE_TTL = 60;

    /** Timeout de consulta por defecto (segundos) — Fase 2.5: 30s. */
    public const DEFAULT_QUERY_TIMEOUT = 30;

    /** Tope máximo de query timeout (segundos). */
    public const MAX_QUERY_TIMEOUT = 600;

    /** Timeout de conexión/login por defecto (segundos) — fast-fail de red. */
    public const DEFAULT_CONNECT_TIMEOUT = 5;

    /** Ventana de detección del circuit breaker (segundos). */
    public const CB_VENTANA_S = 60;

    /** Umbral de fallos 2812 para abrir el circuito. */
    public const CB_UMBRAL_FALLOS = 3;

    /** Duración del circuito abierto (segundos). */
    public const CB_ABIERTA_S = 300;

    /** Tabla de cache local. */
    public const CACHE_TABLE = 'cache_profit_queries';

    /** @var string Ruta del archivo SQLite de cache. */
    private string $cacheDb;

    /** @var int TTL de cache (segundos). */
    private int $cacheTtl;

    /** @var int Timeout de consulta (segundos). */
    private int $timeoutS;

    /** @var int Timeout de consulta activo (segundos, SQL_ATTR_QUERY_TIMEOUT). */
    private int $queryTimeout;

    /** @var array<string,array{fallos:int,primera:int,abierto_hasta:int}> Fallos 2812 por SP (circuit breaker). */
    private array $failureLog = [];

    /** @var bool Si la cache está habilitada. */
    private bool $cacheEnabled;

    /** @var PDO|null Conexión SQLite de cache (abierta bajo demanda). */
    private ?PDO $cachePdo = null;

    /** @var bool Flag de inicialización de la tabla de cache. */
    private bool $cacheInit = false;

    /** @var PDO|null Conexión activa a SQL Server (HOTFIX v4.17.1: trackeada para forceDisconnect). */
    private ?PDO $profitPdo = null;

    /** @var int Instancias vivas de ConnectionWrapper (detección de fugas). */
    private static int $instanciasVivas = 0;

    public function __construct(
        ?string $cacheDb = null,
        ?int $cacheTtl = null,
        ?int $timeoutS = null,
        ?bool $cacheEnabled = null,
        ?int $queryTimeout = null
    ) {
        $this->cacheDb = $cacheDb ?: $this->env('PROFIT_CACHE_DB', $this->defaultCachePath());
        $this->cacheTtl = $cacheTtl ?? max(1, (int) $this->env('PROFIT_CACHE_TTL', (string) self::DEFAULT_CACHE_TTL));
        $this->timeoutS = $timeoutS ?? max(1, (int) $this->env('PROFIT_CONNECT_TIMEOUT', (string) self::DEFAULT_CONNECT_TIMEOUT));
        $this->queryTimeout = $queryTimeout ?? $this->envQueryTimeout();
        $this->cacheEnabled = $cacheEnabled ?? (strtolower($this->env('PROFIT_CACHE_ENABLED', '1')) !== '0');
        self::$instanciasVivas++;
        // HOTFIX v4.17.1: aviso si hay múltiples wrappers vivos (fuga de conexiones).
        if (self::$instanciasVivas > 1) {
            error_log(sprintf(
                '[HOTFIX] ConnectionWrapper: %d instancias vivas (posible fuga) en PID %d',
                self::$instanciasVivas,
                getmypid()
            ));
        }
    }

    /**
     * Destructor (HOTFIX v4.17.1): fuerza el cierre de TODAS las conexiones
     * (SQL Server y cache SQLite) y colecta ciclos. No deja procesos vivos.
     */
    public function __destruct()
    {
        $this->forceDisconnect();
    }

    /**
     * HOTFIX v4.17.1: cierra la conexión activa a SQL Server (si existe), la
     * conexión SQLite de cache y colecta ciclos. Los POOLING se desactivan en
     * conectarProfit()/ejecutarOdbc() para que el cierre sea REAL en el
     * servidor (192.168.4.20) y no quede un SPID dormido de KICKSERVER.
     */
    public function forceDisconnect(): void
    {
        $this->profitPdo = null;  // cierra la conexión SQL Server trackeada
        $this->cachePdo = null;   // cierra la conexión SQLite de cache
        gc_collect_cycles();      // liberación final de recursos PHP
    }

    /**
     * HOTFIX v4.17.1: número de instancias vivas de ConnectionWrapper en el
     * proceso (para detectar fugas de conexiones en logs).
     */
    public static function getConnectionCount(): int
    {
        return self::$instanciasVivas;
    }

    /**
     * Ajusta el timeout de consulta (segundos) en caliente.
     *
     * Fase 2.5 T2: default 30s, tope 600s (igual que la orden). Se aplica en
     * la siguiente conexión vía SQL_ATTR_QUERY_TIMEOUT (ODBC nativo) o
     * PDO::SQLSRV_ATTR_QUERY_TIMEOUT (driver sqlsrv).
     */
    public function setQueryTimeout(int $seconds): void
    {
        $this->queryTimeout = max(1, min(self::MAX_QUERY_TIMEOUT, $seconds));
    }

    /** Timeout de consulta activo (segundos). */
    public function getQueryTimeout(): int
    {
        return $this->queryTimeout;
    }

    /**
     * Circuit breaker de SP (Fase 2.5 T3): consulta si el circuito de un
     * procedimiento está ABIERTO (≥3 fallos 2812 en 60s → 300s de espera).
     */
    public function spBloqueado(string $sp): bool
    {
        $k = strtolower(trim($sp));
        if (!isset($this->failureLog[$k])) {
            return false;
        }
        $e = $this->failureLog[$k];
        if ($e['abierto_hasta'] > time()) {
            return true;
        }
        // Solo se rearma cuando un circuito ABIERTO expiró (abierto_hasta>0).
        if ($e['abierto_hasta'] > 0) {
            unset($this->failureLog[$k]);
        }
        return false;
    }

    /**
     * Circuit breaker de SP: registra un fallo de procedimiento (SQLSTATE
     * 2812). Al acumular 3 fallos en 60s abre el circuito 300s.
     */
    public function registrarFalloSP(string $sp): void
    {
        $k = strtolower(trim($sp));
        $ahora = time();
        $e = $this->failureLog[$k] ?? null;

        // Primera vez: arranca el contador.
        if ($e === null) {
            $this->failureLog[$k] = ['fallos' => 1, 'primera' => $ahora, 'abierto_hasta' => 0];
            return;
        }

        if ($e['abierto_hasta'] > $ahora) {
            return; // ya estaba abierto
        }
        // Ventana vencida → reset de contador.
        if ($ahora - $e['primera'] > self::CB_VENTANA_S) {
            $e = ['fallos' => 0, 'primera' => $ahora, 'abierto_hasta' => 0];
        }

        $e['fallos']++;
        if ($e['fallos'] >= self::CB_UMBRAL_FALLOS) {
            $e['abierto_hasta'] = $ahora + self::CB_ABIERTA_S;
        }
        $this->failureLog[$k] = $e;
    }

    /**
     * Circuit breaker de SP: limpia el historial de fallos de un SP (éxito).
     */
    public function resetFalloSP(string $sp): void
    {
        unset($this->failureLog[strtolower(trim($sp))]);
    }

    /**
     * Valida la configuración SQL de entorno (Fase 2.5 T4).
     *
     * Regla de la orden Fase 2.4: la BD SQL configurada NO puede ser CRISTM25
     * (producción). Si PROFIT_SQL_NAME/PROFIT_DB_NAME resuelve a CRISTM25 se
     * lanza EnvironmentException con la variable culpable.
     *
     * @throws EnvironmentException Si la BD configurada es CRISTM25.
     */
    public function validarEntorno(): void
    {
        $sqlName = $this->env('PROFIT_SQL_NAME', '');
        $dbName = $this->env('PROFIT_DB_NAME', '');
        $activo = $sqlName !== '' ? $sqlName : ($dbName !== '' ? $dbName : 'PRUEB25');

        if (strcasecmp(trim($activo), 'CRISTM25') === 0) {
            $culpable = $sqlName !== '' ? 'PROFIT_SQL_NAME' : 'PROFIT_DB_NAME';
            throw new EnvironmentException(
                'Entorno prohibido: ' . $culpable . '="' . trim($activo)
                . '". Desde la Fase 2.4 el stack PHP solo lee de la BD de '
                . 'pruebas PRUEB25 (CRISTM25 es producción). Corrija la variable '
                . 'en .env o en el servicio antes de continuar.'
            );
        }
    }

    // ───────────────────────────────────────────────────────────────────────
    // API PÚBLICA (contrato de la orden)
    // ───────────────────────────────────────────────────────────────────────

    /**
     * Método maestro: valida SELECT-only, fuerza NOLOCK, usa cache local y
     * ejecuta contra SQL Server con timeout y cierre garantizado.
     *
     * @param string $sql          Consulta SELECT (bindings con ? o :nombre).
     * @param array  $bindings     Parámetros posicionales o nombrados.
     * @param int    $cacheSeconds TTL de cache (default 60s; 0 = sin cache).
     *
     * @return array<int,array<string,mixed>> Filas FETCH_ASSOC.
     *
     * @throws \InvalidArgumentException Si la consulta no es SELECT puro.
     * @throws PDOException Fallo de conexión/ejecución (el Tool degrada).
     */
    public function querySafe(string $sql, array $bindings = [], int $cacheSeconds = self::DEFAULT_CACHE_TTL): array
    {
        $this->asegurarSelect($sql);

        $sqlFinal = $this->inyectarNolock($sql);
        $usarCache = $this->cacheEnabled && $cacheSeconds > 0;

        if ($usarCache) {
            $clave = $this->claveCache($sql, $bindings);
            $hit = $this->leerCache($clave, $cacheSeconds);
            if ($hit !== null) {
                return $hit;
            }
        }

        $tInicio = microtime(true);
        $filas = $this->ejecutar($sqlFinal, $bindings);
        $duracionMs = (int) round((microtime(true) - $tInicio) * 1000);

        if ($usarCache) {
            $this->escribirCache($clave, $filas, $duracionMs, $sql);
        }

        return $filas;
    }

    /**
     * Método directo SIN cache (dato ultra-fresco). Mismas validaciones:
     * SELECT-only, NOLOCK forzado, query timeout 30s, cierre en finally.
     *
     * @param string $sql      Consulta SELECT (bindings con ? o :nombre).
     * @param array  $bindings Parámetros posicionales o nombrados.
     *
     * @return array<int,array<string,mixed>> Filas FETCH_ASSOC.
     *
     * @throws \InvalidArgumentException Si la consulta no es SELECT puro.
     * @throws PDOException Fallo de conexión/ejecución.
     */
    public function queryProfit(string $sql, array $bindings = []): array
    {
        $this->asegurarSelect($sql);
        $sqlFinal = $this->inyectarNolock($sql);

        return $this->ejecutar($sqlFinal, $bindings);
    }

    /**
     * Validación estricta: la consulta debe comenzar con SELECT (case-
     * insensitive, con espacios/blancos opcionales) y NO contener ninguna
     * sentencia o patrón peligroso.
     */
    public function isSelectOnly(string $sql): bool
    {
        $t = trim($sql);
        if (preg_match('/^\s*SELECT\b/i', $t) !== 1) {
            return false;
        }
        if (preg_match(
            '/\b(?:INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|MERGE|'
            . 'GRANT|REVOKE|DENY|EXEC|EXECUTE|DECLARE|SET|USE|BACKUP|RESTORE)\b/i',
            $t
        ) === 1) {
            return false;
        }
        // Stored procedures y extensiones peligrosas (sp_ / xp_).
        if (preg_match('/\bsp_[\w]|\bxp_[\w]/i', $t) === 1) {
            return false;
        }
        // Múltiples sentencias separadas por ';' seguido de SQL activo.
        if (preg_match('/;\s*\w/i', $t) === 1) {
            return false;
        }
        return true;
    }

    // ───────────────────────────────────────────────────────────────────────
    // HELPERS DE ESQUEMA (INFORMATION_SCHEMA) — compatibilidad de las Skills
    // ───────────────────────────────────────────────────────────────────────

    /**
     * Primera tabla candidata existente en el esquema (nombre real,
     * case-insensitive). Ejecuta vía querySafe (SELECT puro).
     *
     * @param array<int,string> $candidatas
     */
    public function resolverTabla(array $candidatas): ?string
    {
        try {
            $filas = $this->querySafe(
                "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                . "WHERE TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME",
                [],
                3600 // el esquema cambia muy poco: TTL largo
            );
            $mapa = [];
            foreach ($filas as $fila) {
                $nombre = (string) ($fila['TABLE_NAME'] ?? '');
                if ($nombre !== '') {
                    $mapa[strtolower($nombre)] = $nombre;
                }
            }
            foreach ($candidatas as $cand) {
                if (isset($mapa[strtolower($cand)])) {
                    return $mapa[strtolower($cand)];
                }
            }
        } catch (PDOException $e) {
            return null;
        } catch (\InvalidArgumentException $e) {
            return null;
        }
        return null;
    }

    /**
     * Columnas reales de una tabla (case-insensitive, por orden posicional).
     *
     * @return array<int,string>
     */
    public function listarColumnas(string $tabla): array
    {
        try {
            $filas = $this->querySafe(
                'SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS '
                . 'WHERE TABLE_NAME = ? ORDER BY ORDINAL_POSITION',
                [$tabla],
                3600
            );
            $out = [];
            foreach ($filas as $fila) {
                $nombre = (string) ($fila['COLUMN_NAME'] ?? '');
                if ($nombre !== '') {
                    $out[] = $nombre;
                }
            }
            return $out;
        } catch (PDOException $e) {
            return [];
        } catch (\InvalidArgumentException $e) {
            return [];
        }
    }

    /**
     * Resuelve en lote columnas candidatas de una misma tabla.
     *
     * @param string                          $tabla
     * @param array<string,array<int,string>> $roles rol => candidatas
     *
     * @return array<string,string|null>
     */
    public function resolverColumnas(string $tabla, array $roles): array
    {
        $mapa = [];
        foreach ($this->listarColumnas($tabla) as $c) {
            $mapa[strtolower($c)] = $c;
        }
        $resultado = [];
        foreach ($roles as $rol => $candidatas) {
            $resultado[$rol] = null;
            foreach ($candidatas as $cand) {
                if (isset($mapa[strtolower($cand)])) {
                    $resultado[$rol] = $mapa[strtolower($cand)];
                    break;
                }
            }
        }
        return $resultado;
    }

    /**
     * Escapa un identificador con corchetes de SQL Server (nunca backticks).
     * Lanza RuntimeException si el identificador no es seguro.
     */
    public function qPara(string $identificador): string
    {
        if (preg_match('/^[A-Za-z_][A-Za-z0-9_]*$/', $identificador) !== 1) {
            throw new \RuntimeException('Identificador SQL inválido: ' . $identificador);
        }
        return '[' . $identificador . ']';
    }

    // ───────────────────────────────────────────────────────────────────────
    // INTERNO
    // ───────────────────────────────────────────────────────────────────────

    /**
     * Rechaza la consulta si no es SELECT puro.
     *
     * @throws \InvalidArgumentException
     */
    private function asegurarSelect(string $sql): void
    {
        if (!$this->isSelectOnly($sql)) {
            throw new \InvalidArgumentException(
                'ConnectionWrapper: solo se permiten consultas SELECT. '
                . 'Operación rechazada (INSERT/UPDATE/DELETE/DDL/EXEC/procedimientos '
                . 'almacenados no están permitidos). SQL: ' . substr($sql, 0, 120)
            );
        }
    }

    /**
     * Inyecta WITH (NOLOCK) en cada FROM/JOIN de tabla que no lo tenga.
     *
     * Soporta nombres con corchetes [tabla], prefijos de esquema (dbo.tabla) y
     * alias ('FROM art a' → 'FROM art WITH (NOLOCK) a'). Las subconsultas
     * parentizadas ('FROM (SELECT ...)') se ignoran.
     */
    public function inyectarNolock(string $sql): string
    {
        return preg_replace_callback(
            '/\b(FROM|JOIN)\s+((?:\[[^\]]+\]|[A-Za-z_][\w]*)(?:\s*\.\s*(?:\[[^\]]+\]|[A-Za-z_][\w]*))?)(\s+WITH\s*\(\s*NOLOCK\s*\))?/i',
            static function (array $m): string {
                $prefijo = $m[1];
                $tabla = $m[2];
                $yaTiene = isset($m[3]) && trim((string) $m[3]) !== '';
                return $yaTiene
                    ? $prefijo . ' ' . $tabla . $m[3]
                    : $prefijo . ' ' . $tabla . ' WITH (NOLOCK)';
            },
            $sql
        );
    }

    /**
     * Ejecuta una SELECT con query timeout y cierre garantizado (Fase 2.5 T2).
     *
     * Dispatch por driver disponible:
     *  - sqlsrv (producción): PDO con PDO::SQLSRV_ATTR_QUERY_TIMEOUT.
     *  - ODBC (este entorno): backend nativo odbc_* con
     *    SQL_ATTR_QUERY_TIMEOUT (odbc_setoption) — el único mecanismo probado
     *    que corta WAITFOR DELAY en el driver ODBC legacy. Los bindings
     *    nombrados (:c) se traducen a posicionales (?).
     *
     * @throws ConnectionWrapperException Si la consulta excede el query timeout.
     * @throws PDOException                Cualquier otro fallo de red/servidor.
     */
    private function ejecutar(string $sqlFinal, array $bindings): array
    {
        $drivers = PDO::getAvailableDrivers();
        $filas = in_array('sqlsrv', $drivers, true)
            ? $this->ejecutarSqlSrv($sqlFinal, $bindings)
            : $this->ejecutarOdbc($sqlFinal, $bindings);
        return self::normalizarFilasUtf8($filas);
    }

    /**
     * BUG REAL detectado en vivo (18/08): ni el driver PDO sqlsrv ni el
     * nativo odbc_* convierten el texto de Profit — columnas con tildes/Ñ
     * llegan en CP1252 crudo (1 byte por carácter: Ñ = 0xD1), NO en UTF-8.
     * Si ese texto crudo se concatena sin convertir dentro de una tarjeta
     * (junto con emojis/etiquetas que sí son UTF-8 válido), el resultado es
     * una mezcla de encodings — inválida como UTF-8 en conjunto — y el
     * saneamiento de última instancia que existe en el borde de salida
     * (r_normalizar_utf8 en bin/ejecutar_tool_cli.php, normalizarUtf8 en
     * ToolRegistry.php) detecta la CADENA COMPLETA como inválida y la
     * reinterpreta ENTERA como CP1252, corrompiendo también las partes que
     * ya estaban bien codificadas (ese fue el bug real: "RAZÓN SOCIAL" con
     * tilde de verdad se volvía basura junto con el dato mal codificado).
     *
     * Se corrige en la raíz: cada valor de texto se convierte a UTF-8 AQUÍ,
     * campo por campo, en el único punto por el que pasan TODAS las lecturas
     * de Profit (ejecutarSqlSrv + ejecutarOdbc) — así cuando una tool arma
     * la tarjeta, cada pieza ya llega limpia y el saneamiento de salida deja
     * de tener trabajo que hacer (y de tener la oportunidad de romper algo).
     *
     * @param array<int,array<string,mixed>> $filas
     *
     * @return array<int,array<string,mixed>>
     */
    private static function normalizarFilasUtf8(array $filas): array
    {
        foreach ($filas as &$fila) {
            foreach ($fila as $col => $valor) {
                if (is_string($valor) && !mb_check_encoding($valor, 'UTF-8')) {
                    $fila[$col] = mb_convert_encoding($valor, 'UTF-8', 'Windows-1252');
                }
            }
        }
        unset($fila);
        return $filas;
    }

    /**
     * Ruta PDO + driver sqlsrv (producción). El query timeout lo impone
     * PDO::SQLSRV_ATTR_QUERY_TIMEOUT (segundos) desde conectarProfit().
     *
     * HOTFIX v4.17.1: la conexión queda trackeada (pooling desactivado) y su
     * cierre real lo realiza forceDisconnect()/__destruct (no aquí).
     */
    private function ejecutarSqlSrv(string $sqlFinal, array $bindings): array
    {
        $pdo = $this->conectarProfit();
        $stmt = $pdo->prepare($sqlFinal);
        $stmt->execute($bindings);
        return $stmt->fetchAll(PDO::FETCH_ASSOC);
    }

    /**
     * Backend nativo ODBC con SQL_ATTR_QUERY_TIMEOUT (corta WAITFOR DELAY).
     *
     * Abre odbc_connect, aplica odbc_setoption(conn, SQL_CONNECT, 0, seg) y
     * SET LOCK_TIMEOUT, traduce bindings nombrados a posicionales y ejecuta
     * con odbc_prepare/odbc_execute. Cierra la conexión en finally (SIEMPRE).
     *
     * @throws ConnectionWrapperException SQLSTATE S1T00 (query timeout).
     * @throws PDOException                Fallo de conexión/prepare/ejecución.
     */
    private function ejecutarOdbc(string $sqlFinal, array $bindings): array
    {
        [$sqlPos, $params] = $this->traducirBindings($sqlFinal, $bindings);

        $host = $this->env('PROFIT_SQL_HOST', $this->env('PROFIT_DB_HOST', '192.168.4.20'));
        $port = $this->env('PROFIT_SQL_PORT', $this->env('PROFIT_DB_PORT', '1433'));
        $user = $this->env('PROFIT_SQL_USER', $this->env('PROFIT_DB_USER', 'profit'));
        $pass = $this->env('PROFIT_SQL_PASS', $this->env('PROFIT_DB_PASS', 'profit'));
        $name = $this->env('PROFIT_SQL_NAME', $this->env('PROFIT_DB_NAME', 'PRUEB25'));
        $driverOdbc = $this->env('PROFIT_SQL_DRIVER', $this->env('PROFIT_DB_DRIVER', 'SQL Server'));

        $dsn = 'Driver={' . $driverOdbc . '};Server=' . $host . ',' . $port
             . ';Database=' . $name . ';Connection Timeout=' . $this->timeoutS;

        $conn = @odbc_connect($dsn, $user, $pass);
        if ($conn === false) {
            throw new PDOException(
                'ConnectionWrapper: falló la conexión ODBC: ' . odbc_errormsg()
            );
        }

        try {
            // SQL_ATTR_QUERY_TIMEOUT a nivel conexión (segundos).
            odbc_setoption($conn, 1, 0, $this->queryTimeout);
            // Defensa adicional: LOCK_TIMEOUT en ms para bloqueos de tabla.
            @odbc_exec($conn, 'SET LOCK_TIMEOUT ' . ($this->queryTimeout * 1000));

            $stmt = @odbc_prepare($conn, $sqlPos);
            if ($stmt === false) {
                throw new PDOException(
                    'ConnectionWrapper: odbc_prepare falló: ' . odbc_errormsg($conn)
                );
            }

            if (!@odbc_execute($stmt, $params)) {
                $estado = (string) odbc_error($conn);
                $mensaje = (string) odbc_errormsg($conn);
                if ($estado === 'S1T00'
                    || stripos($mensaje, 'tiempo de espera') !== false
                    || stripos($mensaje, 'timeout') !== false
                    || stripos($mensaje, 'expir') !== false) {
                    throw new ConnectionWrapperException(
                        'Query timeout after ' . $this->queryTimeout . 's',
                        ConnectionWrapperException::SQLSTATE_TIMEOUT
                    );
                }
                throw new PDOException(
                    'ConnectionWrapper: ' . $mensaje . ' [' . $estado . ']'
                );
            }

            $filas = [];
            while (($fila = odbc_fetch_array($stmt)) !== false) {
                $filas[] = $fila;
            }
            @odbc_free_result($stmt);
            return $filas;
        } finally {
            @odbc_close($conn);
        }
    }

    /**
     * Traduce bindings nombrados (:param) a posicionales (?). Las listas
     * posicionales pasan tal cual (odbc_prepare solo acepta ?).
     *
     * @return array{0:string,1:list<mixed>} [sqlPosicional, params].
     */
    private function traducirBindings(string $sql, array $bindings): array
    {
        if (array_is_list($bindings)) {
            return [$sql, array_values($bindings)];
        }

        $params = [];
        $sqlPos = preg_replace_callback(
            '/:([A-Za-z_][A-Za-z0-9_]*)/',
            static function (array $m) use ($bindings, &$params): string {
                $clave = $m[1];
                $params[] = $bindings[$clave] ?? $bindings[':' . $clave] ?? null;
                return '?';
            },
            $sql
        );

        return [$sqlPos, $params];
    }

    /**
     * Conexión PDO a SQL Server PRUEB25 (solo lectura por convención).
     *
     * HOTFIX v4.17.1: se desactiva el connection pooling (el driver lo hace
     * por defecto) para que cada cierre sea real en el servidor y no queden
     * SPIDs dormidos desde KICKSERVER. La conexión se reutiliza y se trackea
     * en $this->profitPdo para poder forzar su cierre con forceDisconnect().
     *
     * @throws PDOException Fallo de conexión (red/credenciales/driver).
     */
    private function conectarProfit(): PDO
    {
        if ($this->profitPdo instanceof PDO) {
            return $this->profitPdo;
        }

        $host = $this->env('PROFIT_SQL_HOST', $this->env('PROFIT_DB_HOST', '192.168.4.20'));
        $port = $this->env('PROFIT_SQL_PORT', $this->env('PROFIT_DB_PORT', '1433'));
        $user = $this->env('PROFIT_SQL_USER', $this->env('PROFIT_DB_USER', 'profit'));
        $pass = $this->env('PROFIT_SQL_PASS', $this->env('PROFIT_DB_PASS', 'profit'));
        $name = $this->env('PROFIT_SQL_NAME', $this->env('PROFIT_DB_NAME', 'PRUEB25'));

        $drivers = PDO::getAvailableDrivers();
        if (in_array('sqlsrv', $drivers, true)) {
            $dsn = 'sqlsrv:Server=' . $host . ',' . $port . ';Database=' . $name
                 . ';ConnectionPooling=0';
        } else {
            $driverOdbc = $this->env('PROFIT_SQL_DRIVER', $this->env('PROFIT_DB_DRIVER', 'SQL Server'));
            $dsn = 'odbc:Driver={' . $driverOdbc . '};Server=' . $host . ',' . $port
                 . ';Database=' . $name . ';Connection Timeout=' . $this->timeoutS;
        }

        $opts = [
            PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_TIMEOUT            => $this->timeoutS,
            PDO::ATTR_PERSISTENT         => false,
        ];
        // Query timeout vía driver sqlsrv (segundos) si está disponible.
        if (defined('PDO::SQLSRV_ATTR_QUERY_TIMEOUT')) {
            $opts[PDO::SQLSRV_ATTR_QUERY_TIMEOUT] = $this->queryTimeout;
        }
        $this->profitPdo = new PDO($dsn, $user, $pass, $opts);
        return $this->profitPdo;
    }

    // ── CACHE LOCAL SQLite ──────────────────────────────────────────────────

    /**
     * Ruta por defecto del archivo de cache (carpeta data del proyecto).
     */
    private function defaultCachePath(): string
    {
        $raiz = dirname(__DIR__, 2);
        return $raiz . '/ara/ARA_Brain/data/cache_profit_queries.db';
    }

    /**
     * Clave MD5 estable de la consulta + bindings.
     */
    private function claveCache(string $sql, array $bindings): string
    {
        try {
            $bind = json_encode($bindings, JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
        } catch (\JsonException $e) {
            $bind = serialize($bindings);
        }
        return md5(strtolower(trim($sql)) . '|' . $bind);
    }

    /**
     * Abre la conexión SQLite de cache y garantiza la tabla.
     */
    private function cachePdo(): PDO
    {
        if ($this->cachePdo !== null) {
            return $this->cachePdo;
        }
        $dir = dirname($this->cacheDb);
        if (!is_dir($dir) && !@mkdir($dir, 0777, true)) {
            $this->cacheEnabled = false;
            throw new PDOException('ConnectionWrapper: no se puede crear la carpeta de cache: ' . $dir);
        }
        $pdo = new PDO('sqlite:' . $this->cacheDb);
        $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        $pdo->exec('PRAGMA journal_mode=WAL');
        $pdo->exec('PRAGMA synchronous=NORMAL');
        if (!$this->cacheInit) {
            $pdo->exec(
                'CREATE TABLE IF NOT EXISTS ' . self::CACHE_TABLE . ' ('
                . 'hash VARCHAR(32) PRIMARY KEY,'
                . 'resultado_json TEXT NOT NULL,'
                . 'creado_en INTEGER NOT NULL,'
                . 'tabla_consultada VARCHAR(100),'
                . 'duracion_ms INTEGER)'
            );
            $this->cacheInit = true;
        }
        $this->cachePdo = $pdo;
        return $pdo;
    }

    /**
     * Lee del cache si la entrada es reciente. null si no hay hit.
     *
     * @return array<int,array<string,mixed>>|null
     */
    private function leerCache(string $clave, int $ttlSegundos): ?array
    {
        try {
            $pdo = $this->cachePdo();
            $stmt = $pdo->prepare(
                'SELECT resultado_json FROM ' . self::CACHE_TABLE . ' WHERE hash = ? AND creado_en >= ?'
            );
            $stmt->execute([$clave, time() - $ttlSegundos]);
            $fila = $stmt->fetch(PDO::FETCH_ASSOC);
            if (!is_array($fila) || !isset($fila['resultado_json'])) {
                return null;
            }
            $decoded = json_decode((string) $fila['resultado_json'], true);
            return is_array($decoded) ? $decoded : null;
        } catch (PDOException $e) {
            $this->cacheEnabled = false; // cache degradada: no rompe las queries
            return null;
        }
    }

    /**
     * Escribe (upsert) el resultado en el cache local.
     *
     * @param array<int,array<string,mixed>> $filas
     */
    private function escribirCache(string $clave, array $filas, int $duracionMs, string $sql): void
    {
        try {
            $json = json_encode($filas, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
        } catch (\JsonException $e) {
            return;
        }
        $tabla = $this->primeraTabla($sql);
        try {
            $pdo = $this->cachePdo();
            $stmt = $pdo->prepare(
                'INSERT OR REPLACE INTO ' . self::CACHE_TABLE
                . ' (hash, resultado_json, creado_en, tabla_consultada, duracion_ms)'
                . ' VALUES (?, ?, ?, ?, ?)'
            );
            $stmt->execute([$clave, $json, time(), $tabla, $duracionMs]);
        } catch (PDOException $e) {
            $this->cacheEnabled = false; // cache degradada: no rompe las queries
        }
    }

    /**
     * Nombre de la primera tabla del FROM (para la columna de cache).
     */
    private function primeraTabla(string $sql): string
    {
        if (preg_match('/\bFROM\s+((?:\[[^\]]+\]|[A-Za-z_][\w]*)(?:\s*\.\s*(?:\[[^\]]+\]|[A-Za-z_][\w]*))?)/i', $sql, $m) === 1) {
            return trim($m[1], '[]');
        }
        return '';
    }

    /**
     * Variable de entorno con default (compat PROFIT_SQL_* / PROFIT_DB_*).
     */
    private function env(string $clave, string $default): string
    {
        $valor = getenv($clave);
        if (is_string($valor) && trim($valor) !== '') {
            return trim($valor);
        }
        return $default;
    }

    /**
     * Query timeout desde env PROFIT_QUERY_TIMEOUT (default 30s, tope 600s).
     */
    private function envQueryTimeout(): int
    {
        $v = (int) $this->env('PROFIT_QUERY_TIMEOUT', (string) self::DEFAULT_QUERY_TIMEOUT);
        return max(1, min(self::MAX_QUERY_TIMEOUT, $v));
    }
}
