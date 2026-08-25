#!/usr/bin/env python3
"""Espeja tablas de barquisimeto (MySQL remoto, Capa 1) hacia el mirror local
en la GB10 (Capa 2). SOLO LECTURA sobre el remoto.

Uso:
    python mirror_mysql_barquisimeto.py --schema-only   # solo crea tablas
    python mirror_mysql_barquisimeto.py                 # esquema + datos
"""
import argparse
import os
import sys

import pymysql
from dotenv import load_dotenv

load_dotenv(".env.gb10")

TABLAS = [t.strip() for t in os.getenv("MIRROR_TABLES_MYSQL", "").split(",") if t.strip()]

REMOTO = dict(
    host=os.getenv("MYSQL_HOST_REMOTO", "192.168.4.148"),
    port=int(os.getenv("MYSQL_PORT_REMOTO", 3306)),
    db=os.getenv("MYSQL_DB_REMOTO", "barquisimeto"),
    user=os.getenv("MYSQL_USER_REMOTO", "jonaiber"),
    password=os.getenv("MYSQL_PASS_REMOTO", ""),
)
LOCAL = dict(
    host=os.getenv("MYSQL_HOST_LOCAL", "127.0.0.1"),
    port=int(os.getenv("MYSQL_PORT_LOCAL", 3306)),
    db=os.getenv("MYSQL_DB_LOCAL", "barquisimeto_local"),
    user=os.getenv("MYSQL_USER_LOCAL", "root"),
    password=os.getenv("MYSQL_PASS_LOCAL", ""),
)


def conectar(cfg):
    return pymysql.connect(
        host=cfg["host"], port=cfg["port"], database=cfg["db"],
        user=cfg["user"], password=cfg["password"], charset="utf8mb4",
    )


def copiar_esquema(tabla):
    remoto = conectar(REMOTO)
    try:
        cur = remoto.cursor()
        cur.execute(f"SHOW CREATE TABLE {tabla}")
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"Tabla '{tabla}' no encontrada en {REMOTO['db']}")
        create_sql = row[1]
    finally:
        remoto.close()

    local = conectar(LOCAL)
    try:
        cur = local.cursor()
        cur.execute(f"DROP TABLE IF EXISTS {tabla}")
        cur.execute(create_sql)
        local.commit()
    finally:
        local.close()
    print(f"[schema] {tabla}: replicada")


def copiar_datos(tabla):
    remoto = conectar(REMOTO)
    try:
        cur = remoto.cursor()
        cur.execute(f"SELECT * FROM {tabla}")
        filas = cur.fetchall()
        cols = [d[0] for d in cur.description]
    finally:
        remoto.close()

    local = conectar(LOCAL)
    try:
        cur = local.cursor()
        cur.execute(f"TRUNCATE TABLE {tabla}")
        if filas:
            placeholders = ", ".join(["%s"] * len(cols))
            col_names = ", ".join(cols)
            cur.executemany(
                f"INSERT INTO {tabla} ({col_names}) VALUES ({placeholders})", filas
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
        print("MIRROR_TABLES_MYSQL vacío en .env.gb10 — nada que espejar.")
        sys.exit(1)

    for tabla in TABLAS:
        copiar_esquema(tabla)
        if not args.schema_only:
            copiar_datos(tabla)

    print("Mirror MySQL (barquisimeto -> local) completo.")


if __name__ == "__main__":
    main()
