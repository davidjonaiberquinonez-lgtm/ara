#!/usr/bin/env python3
"""Espeja tablas de PRUEB25 (SQL Server remoto, Capa 1) hacia el mirror local
en la GB10 (Capa 2). SOLO LECTURA sobre el remoto.

Sigue el mismo patrón que ara/ARA_Brain/vigilar_datos.py: conexión pyodbc
CORTA (abre -> fetchall -> cierra, nunca persistente) y WITH (NOLOCK) en las
lecturas, para no bloquear el ERP en producción.

Uso:
    python mirror_sqlserver_pruebas25.py --schema-only   # solo crea tablas
    python mirror_sqlserver_pruebas25.py                 # esquema + datos
"""
import argparse
import os
import sys

import pyodbc
from dotenv import load_dotenv

load_dotenv(".env.gb10")

TABLAS = [t.strip() for t in os.getenv("MIRROR_TABLES_SQLSERVER", "").split(",") if t.strip()]

REMOTO = dict(
    driver=os.getenv("PROFIT_SQL_DRIVER", "SQL Server"),
    host=os.getenv("PROFIT_SQL_HOST_REMOTO", "192.168.4.20"),
    port=os.getenv("PROFIT_SQL_PORT_REMOTO", "1433"),
    db=os.getenv("PROFIT_SQL_NAME_REMOTO", "PRUEB25"),
    user=os.getenv("PROFIT_SQL_USER_REMOTO", "profit"),
    password=os.getenv("PROFIT_SQL_PASS_REMOTO", ""),
)
LOCAL = dict(
    driver=os.getenv("PROFIT_SQL_DRIVER", "SQL Server"),
    host=os.getenv("PROFIT_SQL_HOST_LOCAL", "127.0.0.1"),
    port=os.getenv("PROFIT_SQL_PORT_LOCAL", "1433"),
    db=os.getenv("PROFIT_SQL_NAME_LOCAL", "PRUEB25_LOCAL"),
    user=os.getenv("PROFIT_SQL_USER_LOCAL", "sa"),
    password=os.getenv("PROFIT_SQL_PASS_LOCAL", ""),
)


def conn_str(cfg):
    return (
        f"DRIVER={{{cfg['driver']}}};SERVER={cfg['host']},{cfg['port']};"
        f"DATABASE={cfg['db']};UID={cfg['user']};PWD={cfg['password']}"
    )


def conectar(cfg, timeout=8):
    return pyodbc.connect(conn_str(cfg), timeout=timeout)


def copiar_esquema(tabla):
    conn = conectar(REMOTO)
    try:
        cur = conn.cursor()
        col_defs = cur.columns(table=tabla)
        columnas = [(c.column_name, c.type_name, c.column_size) for c in col_defs]
    finally:
        conn.close()
    if not columnas:
        raise RuntimeError(f"Tabla '{tabla}' no encontrada en {REMOTO['db']} — verificar nombre real")

    def sql_type(type_name, size):
        tn = type_name.lower()
        if "char" in tn or "text" in tn:
            return f"NVARCHAR({size if size and size < 4000 else 'MAX'})"
        if "int" in tn:
            return "BIGINT"
        if "decimal" in tn or "numeric" in tn or "money" in tn:
            return "DECIMAL(18,4)"
        if "date" in tn or "time" in tn:
            return "DATETIME2"
        if "float" in tn or "real" in tn:
            return "FLOAT"
        return "NVARCHAR(MAX)"

    cols_sql = ", ".join(f"[{n}] {sql_type(t, s)}" for n, t, s in columnas)
    local = conectar(LOCAL)
    try:
        cur = local.cursor()
        cur.execute(
            f"IF OBJECT_ID('dbo.{tabla}', 'U') IS NOT NULL DROP TABLE dbo.{tabla}"
        )
        cur.execute(f"CREATE TABLE dbo.{tabla} ({cols_sql})")
        local.commit()
    finally:
        local.close()
    print(f"[schema] {tabla}: {len(columnas)} columnas replicadas")
    return [c[0] for c in columnas]


def copiar_datos(tabla, columnas):
    remoto = conectar(REMOTO)
    try:
        cur = remoto.cursor()
        cols_sql = ", ".join(f"[{c}]" for c in columnas)
        filas = cur.execute(f"SELECT {cols_sql} FROM dbo.{tabla} WITH (NOLOCK)").fetchall()
    finally:
        remoto.close()

    local = conectar(LOCAL)
    try:
        cur = local.cursor()
        cur.execute(f"TRUNCATE TABLE dbo.{tabla}")
        if filas:
            placeholders = ", ".join("?" * len(columnas))
            cols_sql = ", ".join(f"[{c}]" for c in columnas)
            cur.executemany(
                f"INSERT INTO dbo.{tabla} ({cols_sql}) VALUES ({placeholders})",
                [tuple(f) for f in filas],
            )
        local.commit()
    finally:
        local.close()
    print(f"[data] {tabla}: {len(filas)} filas copiadas")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-only", action="store_true")
    args = parser.parse_args()

    if not TABLAS:
        print("MIRROR_TABLES_SQLSERVER vacío en .env.gb10 — nada que espejar. Ver README.md.")
        sys.exit(1)

    for tabla in TABLAS:
        columnas = copiar_esquema(tabla)
        if not args.schema_only:
            copiar_datos(tabla, columnas)

    print("Mirror SQL Server (PRUEB25 -> local) completo.")


if __name__ == "__main__":
    main()
