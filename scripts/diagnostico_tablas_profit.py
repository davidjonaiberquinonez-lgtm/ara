# -*- coding: utf-8 -*-
"""Diagnóstico Information Schema — Tablas de Notas y Clientes en Profit SQL.

OBJETIVO: identificar con exactitud las tablas de cabecera de notas
(not_ent / sNotaEntre / ...) y de clientes (clientes / sCliente / ...) en
CADA base de datos del servidor Profit (CRISTM25 = San Cristóbal,
PROFIT_BQTO = Barquisimeto) y verificar por qué `co_cli` / `razon_social`
pueden llegar vacíos en las sucursales.

Uso:
  python scripts/diagnostico_tablas_profit.py

Configuración (variables de entorno, sin valores sensibles en salida):
  PROFIT_SQL_SERVER/PORT/USER/PASSWORD/DATABASE  (precedencia alta)
  PROFIT_DB_HOST/PORT/USER/PASS/NAME/DRIVER      (config CRISTM25 conocida)
"""

import os
import sys

import pyodbc

DRIVER = os.environ.get("PROFIT_DB_DRIVER", "SQL Server")
HOST = os.environ.get("PROFIT_SQL_SERVER") or os.environ.get("PROFIT_DB_HOST", "192.168.4.20")
PORT = os.environ.get("PROFIT_SQL_PORT") or os.environ.get("PROFIT_DB_PORT", "1433")
USER = os.environ.get("PROFIT_SQL_USER") or os.environ.get("PROFIT_DB_USER", "profit")
PASS = os.environ.get("PROFIT_SQL_PASSWORD") or os.environ.get("PROFIT_DB_PASS", "profit")
DB_INICIAL = (
    os.environ.get("PROFIT_SQL_DATABASE")
    or os.environ.get("PROFIT_DB_NAME", "CRISTM25")
)
TIMEOUT = int(os.environ.get("PROFIT_DB_TIMEOUT_S", "8"))

# Tablas de notas conocidas del esquema Profit Plus (no contienen la
# cadena 'nota', por eso se inspeccionan explícitamente además del LIKE).
TABLAS_NOTAS_CONOCIDAS = [
    "not_ent", "not_dep", "reng_nd", "reng_ndd", "reng_nde", "reng_fac",
    "sNotaEntre", "NotaEn", "sNota", "not_venta",
]

# Números de nota de referencia para el JOIN de verificación.
NOTAS_PRUEBA = ["72160754", "467959", "72160454"]

RELEVANTES = ("%nota%", "%clien%", "%vent%", "%pedid%", "%factura%")


def conectar(database: str):
    conn = pyodbc.connect(
        f"DRIVER={{{DRIVER}}};SERVER={HOST},{PORT};DATABASE={database};"
        f"UID={USER};PWD={PASS}",
        timeout=TIMEOUT,
    )
    conn.timeout = TIMEOUT
    return conn


def listar_bases(conn):
    cur = conn.cursor()
    filas = cur.execute(
        "SELECT name FROM sys.databases WHERE name LIKE '%CRISTM%' "
        "OR name LIKE '%PROFIT%' OR name LIKE '%profit%' OR name LIKE '%bqto%' "
        "ORDER BY name"
    ).fetchall()
    return [r[0] for r in filas]


def tablas_notas_clientes(cur, bd: str):
    print(f"\n{'=' * 70}\n[1] TABLAS con 'nota/cliente/venta/pedido/factura' en {bd}\n{'=' * 70}")
    filas = cur.execute(
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_TYPE = 'BASE TABLE' "
        "AND (TABLE_NAME LIKE ? OR TABLE_NAME LIKE ? OR TABLE_NAME LIKE ? "
        "     OR TABLE_NAME LIKE ? OR TABLE_NAME LIKE ?) "
        "ORDER BY TABLE_NAME",
        *RELEVANTES,
    ).fetchall()
    tablas = [r[0] for r in filas]
    for t in TABLAS_NOTAS_CONOCIDAS:
        if t not in tablas:
            existe = cur.execute(
                "SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = ?", t
            ).fetchone()
            if existe:
                tablas.append(t)
    print(f"  ({len(tablas)} tablas)")
    for t in sorted(tablas):
        print(f"    - {t}")
    return sorted(tablas)


def columnas_de(cur, bd: str, tablas):
    print(f"\n[2] COLUMNAS de las tablas detectadas en {bd}")
    if not tablas:
        print("    (sin tablas detectadas)")
        return
    marks = ",".join("?" * len(tablas))
    filas = cur.execute(
        f"SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE "
        f"FROM INFORMATION_SCHEMA.COLUMNS "
        f"WHERE TABLE_NAME IN ({marks}) ORDER BY TABLE_NAME, ORDINAL_POSITION",
        *tablas,
    ).fetchall()
    agrupado: dict = {}
    for t, c, d in filas:
        agrupado.setdefault(t, []).append(f"{c}:{d}")
    for t, cols in agrupado.items():
        print(f"  [{t}] ({len(cols)} columnas)")
        print("    " + ", ".join(cols))


def verificar_join(cur, bd: str):
    print(f"\n[3] VERIFICACIÓN JOIN not_ent LEFT JOIN clientes en {bd}")
    # 3a. Vacíos en la cabecera.
    try:
        fila = cur.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN co_cli IS NULL OR LTRIM(RTRIM(co_cli)) = '' THEN 1 ELSE 0 END) AS sin_co_cli "
            "FROM not_ent"
        ).fetchone()
        print(f"    not_ent: {fila[0]} filas | con co_cli vacío: {fila[1]}")
    except Exception as e:
        print(f"    (error contando not_ent: {e})")
        return

    # 3b. Muestra de la tabla clientes (existencia de cli_des).
    try:
        fila = cur.execute(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = 'clientes' AND COLUMN_NAME IN ('cli_des', 'co_cli')"
        ).fetchone()
        print(f"    clientes con co_cli+cli_des: {'SÍ' if fila[0] == 2 else f'parcial ({fila[0]})'}")
    except Exception:
        pass

    # 3c. Pruebas puntuales por número de nota.
    for num in NOTAS_PRUEBA:
        fila = cur.execute(
            "SELECT TOP 1 LTRIM(RTRIM(CAST(n.fact_num AS NVARCHAR(40)))) AS num_nota, "
            "LTRIM(RTRIM(CAST(n.co_cli AS NVARCHAR(30)))) AS co_cli, "
            "LTRIM(RTRIM(CAST(c.cli_des AS NVARCHAR(200)))) AS razon_social "
            "FROM not_ent n LEFT JOIN clientes c ON n.co_cli = c.co_cli "
            "WHERE LTRIM(RTRIM(CAST(n.fact_num AS NVARCHAR(40)))) = ?",
            num,
        ).fetchone()
        if fila:
            print(f"    nota {num}: co_cli='{fila[1]}' razon_social='{fila[2]}' "
                  f"({'OK' if fila[1] and fila[2] else '⚠️ VACÍO'})")
        else:
            print(f"    nota {num}: no existe en {bd}")


def main():
    print(f"Conectando a {HOST}:{PORT} (db inicial: {DB_INICIAL}) con driver {DRIVER}...")
    try:
        conn = conectar(DB_INICIAL)
    except Exception as e:
        print(f"❌ No se pudo conectar: {e}")
        print("  Verifique PROFIT_DB_HOST/PORT/USER/PASS/NAME (o PROFIT_SQL_*) en .env")
        sys.exit(1)

    cur = conn.cursor()
    print("\n[0] BASES DE DATOS del servidor Profit:")
    bases = listar_bases(conn)
    for b in bases:
        print(f"    - {b}")
    if not bases:
        bases = [DB_INICIAL]
        print(f"    (ninguna con patrón CRISTM/PROFIT/BQTO → probando {DB_INICIAL})")

    for bd in bases:
        try:
            cur.execute(f"USE [{bd}]")
        except Exception as e:
            print(f"  ⚠️ No se pudo usar {bd}: {e}")
            continue
        tablas = tablas_notas_clientes(cur, bd)
        columnas_de(cur, bd, tablas)
        verificar_join(cur, bd)

    conn.close()
    print(f"\n{'=' * 70}\nDIAGNÓSTICO COMPLETO\n{'=' * 70}")


if __name__ == "__main__":
    main()
