<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Almacen;

use PDO;
use PDOException;

/**
 * Trait compartido de acceso a datos para los Tools de Almacén de NvidiaBrain.
 *
 * Fuentes de datos (dual, resolución dinámica vía INFORMATION_SCHEMA):
 *  1. SQL Server Profit PRUEB25 (@ PROFIT_DB_HOST:1433) — reng_nde/reng_ndd/
 *     reng_dev/articulos: renglones de notas, despachos, devoluciones y
 *     catálogo de artículos (motor ODBC "SQL Server" o pdo_sqlsrv).
 *  2. MySQL legacy de gestión (@ MYSQL_HOST:3306) — rep_not (cabecera de
 *     nota), gestion (trazabilidad física: preparación/chequeo/embalaje),
 *     asignaciones_preparacion (mesa), devoluciones, cajas_embalaje: el flujo
 *     de gestion.php (motor pdo_mysql, MySQL ODBC o mysqli).
 *
 * Reglas del proyecto:
 *  - Nunca se asume el esquema: tablas y columnas se resuelven por candidatas
 *    (case-insensitive) contra INFORMATION_SCHEMA. En MySQL el usuario no
 *    tiene BD por defecto → se resuelve la BD funcional con las candidatas y
 *    se ejecuta USE antes de operar.
 *  - Los identificadores se validan con regex antes de interpolarlos en SQL
 *    (q()): cero riesgo de inyección.
 *  - Best-effort absoluto: los Tools NUNCA lanzan excepción por datos
 *    ausentes; degradan con diagnóstico honesto de requisitos.
 *  - Los helpers de conexión son estáticos para que cada Tool sea autónomo y
 *    el ToolRegistry pueda instanciarlo sin dependencias.
 */
trait AlmacenDbTrait
{
    /**
     * Conexión PDO al MySQL legacy de gestión (192.168.4.148).
     *
     * Orden de drivers: pdo_mysql → ODBC MySQL (Unicode/ANSI 8.0 o 5.3).
     * Si ningún driver existe en la instalación, se lanza PDOException con el
     * diagnóstico para que el Tool degrade honesto.
     *
     * @throws PDOException Sin driver MySQL disponible.
     */
    private function conectarMySQL(): PDO
    {
        // Defaults alineados con el legacy (chequeo/registro.php): la BD de
        // gestión del sistema en producción. Las env MYSQL_* siguen siendo la
        // vía de override para entornos distintos.
        $host = self::env('MYSQL_HOST', '192.168.4.148');
        $port = (int) self::env('MYSQL_PORT', '3306');
        $user = self::env('MYSQL_USER', 'jonaiber');
        $pass = self::env('MYSQL_PASSWORD', 'Crist2026.');
        $name = self::env('MYSQL_DATABASE', 'barquisimeto');

        $drivers = PDO::getAvailableDrivers();
        if (in_array('mysql', $drivers, true)) {
            return new PDO(
                'mysql:host=' . $host . ';port=' . $port . ';dbname=' . $name . ';charset=utf8mb4',
                $user,
                $pass,
                [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_TIMEOUT => 8]
            );
        }
        foreach (['MySQL ODBC 8.0 Unicode Driver', 'MySQL ODBC 8.0 ANSI Driver', 'MySQL ODBC 5.3 Unicode Driver'] as $driver) {
            $dsn = 'odbc:Driver={' . $driver . '};Server=' . $host . ',' . $port . ';Database=' . $name . ';charset=utf8mb4;';
            try {
                return new PDO($dsn, $user, $pass, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_TIMEOUT => 8]);
            } catch (PDOException $e) {
                continue;
            }
        }
        throw new PDOException('Sin driver MySQL disponible en esta instalación (pdo_mysql, MySQL ODBC o mysqli requerido).');
    }

    /**
     * Conexión PDO a SQL Server Profit PRUEB25 (solo lectura por convención).
     *
     * @throws PDOException Fallo de conexión (red/credenciales/driver).
     */
    private function conectarProfit(): PDO
    {
        $host = self::env('PROFIT_SQL_HOST', self::env('PROFIT_DB_HOST', '192.168.4.20'));
        $port = self::env('PROFIT_SQL_PORT', self::env('PROFIT_DB_PORT', '1433'));
        $user = self::env('PROFIT_SQL_USER', self::env('PROFIT_DB_USER', 'profit'));
        $pass = self::env('PROFIT_SQL_PASS', self::env('PROFIT_DB_PASS', 'profit'));
        $name = self::env('PROFIT_SQL_NAME', self::env('PROFIT_DB_NAME', 'PRUEB25'));

        $drivers = PDO::getAvailableDrivers();
        if (in_array('sqlsrv', $drivers, true)) {
            $dsn = 'sqlsrv:Server=' . $host . ',' . $port . ';Database=' . $name;
        } else {
            $dsn = 'odbc:Driver={' . self::env('PROFIT_SQL_DRIVER', self::env('PROFIT_DB_DRIVER', 'SQL Server')) . '};Server=' . $host . ',' . $port . ';Database=' . $name;
        }
        return new PDO($dsn, $user, $pass, [
            PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_TIMEOUT            => 8,
        ]);
    }

    /**
     * Nombre del motor para los helpers: 'sqlserver' | 'mysql'.
     */
    private function motorDe(PDO $pdo): string
    {
        try {
            $driver = $pdo->getAttribute(PDO::ATTR_DRIVER_NAME);
            if (is_string($driver) && ($driver === 'mysql' || $driver === 'sqlsrv' || $driver === 'odbc')) {
                // ODBC no distingue: se detecta por la DSN del servidor.
                return $driver === 'mysql' ? 'mysql' : 'sqlserver';
            }
        } catch (PDOException $e) {
            // fall through
        }
        return 'sqlserver';
    }

    /**
     * Todas las tablas del esquema activo.
     *
     * MySQL: si el usuario no tiene BD por defecto (DATABASE() NULL), devuelve
     * [] — se debe pasar por resolverBDMySQL()/resolverTabla() primero.
     *
     * @return array<int,string>
     */
    private function listarTablas(PDO $pdo): array
    {
        try {
            if ($this->motorDe($pdo) === 'mysql') {
                $sql = "SELECT table_name FROM information_schema.tables "
                     . "WHERE table_schema = DATABASE() ORDER BY table_name";
            } else {
                $sql = "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                     . "WHERE TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME";
            }
            $filas = $pdo->query($sql)->fetchAll(PDO::FETCH_COLUMN);
            return array_map('strval', $filas);
        } catch (PDOException $e) {
            return [];
        }
    }

    /**
     * Columnas reales de una tabla del esquema activo.
     *
     * @return array<int,string>
     */
    private function listarColumnas(PDO $pdo, string $tabla): array
    {
        try {
            if ($this->motorDe($pdo) === 'mysql') {
                $stmt = $pdo->prepare(
                    'SELECT column_name FROM information_schema.columns '
                    . 'WHERE table_schema = DATABASE() AND table_name = ? ORDER BY ordinal_position'
                );
            } else {
                $stmt = $pdo->prepare(
                    'SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS '
                    . 'WHERE TABLE_NAME = ? ORDER BY ORDINAL_POSITION'
                );
            }
            $stmt->execute([$tabla]);
            $filas = $stmt->fetchAll(PDO::FETCH_COLUMN);
            return array_map('strval', $filas);
        } catch (PDOException $e) {
            return [];
        }
    }

    /**
     * Resuelve la BD MySQL funcional que contiene alguna tabla candidata y
     * ejecuta USE sobre la conexión (el usuario legacy no tiene BD por
     * defecto). Cero efectos si ninguna candidata existe.
     *
     * @param array<int,string> $candidatas
     *
     * @return string|null Nombre de la BD activada o null si no se encontró.
     */
    private function resolverBDMySQL(PDO $pdo, array $candidatas): ?string
    {
        try {
            $stmt = $pdo->prepare(
                'SELECT table_schema, table_name FROM information_schema.tables '
                . 'WHERE table_schema NOT IN '
                . "('information_schema','mysql','performance_schema','sys') "
                . 'ORDER BY table_schema, table_name'
            );
            $stmt->execute();
            $filas = $stmt->fetchAll(PDO::FETCH_ASSOC);
            $bajas = [];
            foreach ($candidatas as $cand) {
                $bajas[strtolower($cand)] = true;
            }
            foreach ($filas as $fila) {
                $tabla = (string) ($fila['table_name'] ?? '');
                if (isset($bajas[strtolower($tabla)])) {
                    $bd = (string) ($fila['table_schema'] ?? '');
                    if (preg_match('/^[A-Za-z0-9_]+$/', $bd) !== 1) {
                        return null;
                    }
                    $pdo->exec('USE `' . $bd . '`');
                    return $bd;
                }
            }
        } catch (PDOException $e) {
            return null;
        }
        return null;
    }

    /**
     * Primera tabla candidata existente en el esquema (retorna el nombre real,
     * case-insensitive). En MySQL resuelve primero la BD funcional.
     *
     * @param array<int,string> $candidatas
     */
    private function resolverTabla(PDO $pdo, array $candidatas): ?string
    {
        if ($this->motorDe($pdo) === 'mysql') {
            $this->resolverBDMySQL($pdo, $candidatas);
        }
        $tablas = [];
        foreach ($this->listarTablas($pdo) as $t) {
            $tablas[strtolower($t)] = $t;
        }
        foreach ($candidatas as $cand) {
            if (isset($tablas[strtolower($cand)])) {
                return $tablas[strtolower($cand)];
            }
        }
        return null;
    }

    /**
     * Primera columna candidata existente en la tabla (nombre real real).
     *
     * @param array<int,string> $candidatas
     */
    private function resolverColumna(PDO $pdo, string $tabla, array $candidatas): ?string
    {
        $mapa = [];
        foreach ($this->listarColumnas($pdo, $tabla) as $c) {
            $mapa[strtolower($c)] = $c;
        }
        foreach ($candidatas as $cand) {
            if (isset($mapa[strtolower($cand)])) {
                return $mapa[strtolower($cand)];
            }
        }
        return null;
    }

    /**
     * Resuelve en lote varias columnas candidatas de una misma tabla.
     *
     * @param array<string,array<int,string>> $roles  rol => candidatas
     *
     * @return array<string,string|null> rol => columna real (o null)
     */
    private function resolverColumnas(PDO $pdo, string $tabla, array $roles): array
    {
        $mapa = [];
        foreach ($this->listarColumnas($pdo, $tabla) as $c) {
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
     * Escapa un identificador SQL (valida contra inyección y devuelve entre
     * comillas de la BD destino). Si es inválido, lanza RuntimeException — los
     * Tools lo capturan y degradan honesto.
     */
    private static function q(string $identificador): string
    {
        return self::qIdent($identificador, '`');
    }

    /**
     * Escapa un identificador adaptado al motor de la conexión: backticks
     * para MySQL, corchetes para SQL Server. Úsalo SIEMPRE en consultas
     * contra PRUEB25 (Profit) — SQL Server no acepta backticks.
     */
    private function qPara(PDO $pdo, string $identificador): string
    {
        $apertura = $this->motorDe($pdo) === 'mysql' ? '`' : '[';
        $cierre = $this->motorDe($pdo) === 'mysql' ? '`' : ']';
        return self::qIdent($identificador, [$apertura, $cierre]);
    }

    /**
     * Valida el identificador y lo envuelve entre las comillas dadas.
     *
     * @param string|array{0:string,1:string} $comillas
     */
    private static function qIdent(string $identificador, $comillas): string
    {
        if (preg_match('/^[A-Za-z_][A-Za-z0-9_]*$/', $identificador) !== 1) {
            throw new \RuntimeException('Identificador SQL inválido: ' . $identificador);
        }
        if (is_array($comillas)) {
            return $comillas[0] . $identificador . $comillas[1];
        }
        return $comillas . $identificador . $comillas;
    }

    /**
     * Convierte texto de la BD (Windows-1252 en Profit) a UTF-8 limpio.
     */
    private static function aUtf8(string $texto): string
    {
        $limpio = trim($texto);
        if (mb_check_encoding($limpio, 'UTF-8')) {
            return $limpio;
        }
        $convertido = mb_convert_encoding($limpio, 'UTF-8', 'CP1252');
        return is_string($convertido) ? $convertido : $limpio;
    }

    /**
     * Variable de entorno con default (compat MYSQL_* / PROFIT_*).
     */
    private static function env(string $clave, string $default): string
    {
        $valor = getenv($clave);
        if (is_string($valor) && trim($valor) !== '') {
            return trim($valor);
        }
        return $default;
    }

    /**
     * Primer valor NO vacío entre claves candidatas (null/vacío = ausente).
     *
     * @param array<string,mixed> $data
     * @param array<int,string>   $claves
     */
    private static function primerValor(array $data, array $claves, string $default): string
    {
        foreach ($claves as $k) {
            $v = $data[$k] ?? null;
            if ($v !== null && trim((string) $v) !== '') {
                return (string) $v;
            }
        }
        return $default;
    }

    /**
     * Timestamp con microsegundos (métricas del estándar NvidiaBrain Tools).
     */
    private static function micro(): float
    {
        return microtime(true);
    }

    /**
     * Formato numérico seguro: null/vacío → 0.
     */
    private static function fNum(mixed $valor): float
    {
        return $valor === null || $valor === '' ? 0.0 : (float) $valor;
    }
}
