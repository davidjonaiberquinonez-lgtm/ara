<?php

declare(strict_types=1);

namespace App\Services\NvidiaBrain\Tools\Almacen;

use App\Services\ConnectionWrapper;
use PDOException;

/**
 * Helper compartido de los Tools departamentales: acceso READ-ONLY al
 * inventario real de Profit (PRUEB25 @ 192.168.4.20).
 *
 * Orden de Ejecución Fase 2.1 (Pata 1 — v4.14): TODO el acceso a SQL Server
 * pasa por ConnectionWrapper (SELECT-only, WITH (NOLOCK) forzado, timeout 5s,
 * cache local SQLite). Los llamadores crean UNA instancia del wrapper por
 * ejecución y la comparten entre helpers para reutilizar la cache.
 *
 * Esquema real (verificado en producción 2026-08-07):
 *   - st_almac : stock por SUB-ALMACÉN. co_alma es el código de sub-almacén:
 *        01 DESPACHO SAN CRISTOBAL (estante picking S/C)
 *        02 DEPOSITO SAN CRISTOBAL  (bulto cerrado S/C)
 *        03 COTIZACIONES
 *        04 DESPACHO BARQUISIMETO   (estante picking BQTO)
 *        05 DEPOSITO BARQUISIMETO   (bulto cerrado BQTO)
 *        06/07/08/9999 otros (devoluciones, consignación, psicotrópicos, traslado)
 *   - art     : maestro de artículos. stock_act = stock consolidado total,
 *        art_des = descripción, co_prov = proveedor (laboratorio best-effort),
 *        anulado ('0' = activo).
 *   - reng_fac / reng_nde : rotación de ventas (co_art, co_alma = sucursal
 *        01 S/C | 02 BQTO, total_art, anulado).
 *
 * Mapeo con la directiva Tools: DESPACHO→01, DEPOSITO→02, DESPACHO_BQTO→04,
 * DEPOSTO_BQTO→05, STOCK_ACT→art.stock_act.
 */
final class ProfitStockHelper
{
    public const SUB_DESPACHO_SC   = '01';
    public const SUB_DEPOSITO_SC   = '02';
    public const SUB_DESPACHO_BQTO = '04';
    public const SUB_DEPOSITO_BQTO = '05';

    /** @return array<int,string> Columnas de la tabla (minúsculas). */
    public static function columnas(ConnectionWrapper $w, string $tabla): array
    {
        return array_map('strtolower', $w->listarColumnas($tabla));
    }

    /**
     * Stock por sub-almacén de los artículos pedidos.
     *
     * @param ConnectionWrapper $w
     * @param array<int,string> $codigos
     * @param array<int,string> $subs    Sub-almacenes a consultar.
     *
     * @return array<string, array<string,float>> Mapa co_art => [sub => stock]
     */
    public static function stockPorSubAlmacen(ConnectionWrapper $w, array $codigos, array $subs = [self::SUB_DESPACHO_SC, self::SUB_DEPOSITO_SC, self::SUB_DESPACHO_BQTO, self::SUB_DEPOSITO_BQTO]): array
    {
        $codigos = array_values(array_unique(array_filter(array_map('trim', $codigos), static fn ($c) => $c !== '')));
        if ($codigos === []) {
            return [];
        }
        $marks = implode(',', array_fill(0, count($codigos), '?'));
        $marksSubs = implode(',', array_fill(0, count($subs), '?'));
        $sql = 'SELECT co_alma, co_art, stock_act FROM st_almac WITH (NOLOCK)'
             . ' WHERE co_art IN (' . $marks . ') AND co_alma IN (' . $marksSubs . ')';
        $mapa = [];
        foreach (self::ejecutar($w, $sql, array_merge($codigos, $subs)) as $fila) {
            $co = strtoupper(trim((string) $fila['co_art']));
            $sub = trim((string) $fila['co_alma']);
            $mapa[$co][$sub] = (float) $fila['stock_act'];
        }
        return $mapa;
    }

    /**
     * Maestros (descripción, stock total, proveedor, estado) de los artículos.
     *
     * @param ConnectionWrapper $w
     * @param array<int,string> $codigos
     *
     * @return array<string,array<string,mixed>> Mapa co_art => fila art
     */
    public static function maestros(ConnectionWrapper $w, array $codigos): array
    {
        $codigos = array_values(array_unique(array_filter(array_map('trim', $codigos), static fn ($c) => $c !== '')));
        if ($codigos === []) {
            return [];
        }
        $marks = implode(',', array_fill(0, count($codigos), '?'));
        $mapa = [];
        foreach (self::ejecutar(
            $w,
            'SELECT co_art, art_des, stock_act, co_prov, anulado, tipo, i_art_des, campo7'
            . ' FROM art WITH (NOLOCK) WHERE co_art IN (' . $marks . ')',
            $codigos
        ) as $fila) {
            $co = strtoupper(trim((string) $fila['co_art']));
            $mapa[$co] = $fila;
        }
        return $mapa;
    }

    /**
     * Rotación de ventas (reng_fac + reng_nde) de los artículos, opcionalmente
     * restringida a una sucursal ('01' S/C | '02' BQTO).
     *
     * @param ConnectionWrapper $w
     * @param array<int,string> $codigos
     */
    public static function rotacion(ConnectionWrapper $w, array $codigos, ?string $sucursal = null): array
    {
        $codigos = array_values(array_unique(array_filter(array_map('trim', $codigos), static fn ($c) => $c !== '')));
        if ($codigos === []) {
            return [];
        }
        $marks = implode(',', array_fill(0, count($codigos), '?'));
        $rotacion = [];
        foreach (['reng_fac', 'reng_nde'] as $tabla) {
            $sql = 'SELECT co_art, COUNT(*) AS veces FROM ' . self::qSrv($tabla) . ' WITH (NOLOCK)'
                 . ' WHERE co_art IN (' . $marks . ') AND anulado = ?';
            $params = array_merge($codigos, ['0']);
            if ($sucursal !== null && $sucursal !== '') {
                $sql .= ' AND co_alma = ?';
                $params[] = $sucursal;
            }
            $sql .= ' GROUP BY co_art';
            try {
                foreach (self::ejecutar($w, $sql, $params) as $fila) {
                    $co = strtoupper(trim((string) $fila['co_art']));
                    $rotacion[$co] = ($rotacion[$co] ?? 0) + (int) $fila['veces'];
                }
            } catch (PDOException $e) {
                continue;
            }
        }
        return $rotacion;
    }

    /**
     * Ejecuta vía ConnectionWrapper; un rechazo SELECT-only se convierte en
     * PDOException para no cambiar el contrato de error de los llamadores.
     */
    private static function ejecutar(ConnectionWrapper $w, string $sql, array $params): array
    {
        try {
            return $w->querySafe($sql, $params);
        } catch (\InvalidArgumentException $e) {
            throw new PDOException('ProfitStockHelper: ' . $e->getMessage(), 0, $e);
        }
    }

    public static function qSrv(string $identificador): string
    {
        if (preg_match('/^[A-Za-z_][A-Za-z0-9_]*$/', $identificador) !== 1) {
            throw new \RuntimeException('Identificador SQL inválido: ' . $identificador);
        }
        return '[' . $identificador . ']';
    }

    public static function aUtf8(string $texto): string
    {
        $limpio = trim($texto);
        if (mb_check_encoding($limpio, 'UTF-8')) {
            return $limpio;
        }
        $convertido = mb_convert_encoding($limpio, 'UTF-8', 'CP1252');
        return is_string($convertido) ? $convertido : $limpio;
    }

    public static function env(string $clave, string $default): string
    {
        $valor = getenv($clave);
        if (is_string($valor) && trim($valor) !== '') {
            return trim($valor);
        }
        return $default;
    }
}
