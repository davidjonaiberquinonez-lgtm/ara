#!/usr/bin/env python3
"""Valida conectividad de las 3 capas antes de arrancar ara_server.py en la GB10."""
import os
import sqlite3
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv(".env.gb10")


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def check_sqlserver(host, port, db, user, password, driver, etiqueta):
    import pyodbc

    conn_str = f"DRIVER={{{driver}}};SERVER={host},{port};DATABASE={db};UID={user};PWD={password}"
    try:
        conn = pyodbc.connect(conn_str, timeout=8)
        conn.cursor().execute("SELECT 1")
        conn.close()
        log(f"OK {etiqueta} ({host}:{port}/{db})")
        return True
    except Exception as e:
        log(f"FALLO {etiqueta}: {e}")
        return False


def check_mysql(host, port, db, user, password, etiqueta):
    import pymysql

    try:
        conn = pymysql.connect(host=host, port=port, database=db, user=user, password=password)
        conn.cursor().execute("SELECT 1")
        conn.close()
        log(f"OK {etiqueta} ({host}:{port}/{db})")
        return True
    except Exception as e:
        log(f"FALLO {etiqueta}: {e}")
        return False


def check_sqlite(path):
    try:
        conn = sqlite3.connect(path)
        tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        esperadas = {"contexto_llm", "cache_adaptadores", "logs_operacion", "circuit_breakers", "embeddings"}
        faltan = esperadas - tablas
        if faltan:
            log(f"FALLO CAPA 3: faltan tablas {faltan} — correr init_ara_llm.py")
            return False
        log(f"OK CAPA 3 ({path})")
        return True
    except Exception as e:
        log(f"FALLO CAPA 3: {e}")
        return False


def main():
    log("=== CAPA 1 (remotos, solo lectura) ===")
    c1a = check_sqlserver(
        os.getenv("PROFIT_SQL_HOST_REMOTO", "192.168.4.20"),
        os.getenv("PROFIT_SQL_PORT_REMOTO", "1433"),
        os.getenv("PROFIT_SQL_NAME_REMOTO", "PRUEB25"),
        os.getenv("PROFIT_SQL_USER_REMOTO", "profit"),
        os.getenv("PROFIT_SQL_PASS_REMOTO", ""),
        os.getenv("PROFIT_SQL_DRIVER", "SQL Server"),
        "PRUEB25 (SQL Server remoto)",
    )
    c1b = check_mysql(
        os.getenv("MYSQL_HOST_REMOTO", "192.168.4.148"),
        int(os.getenv("MYSQL_PORT_REMOTO", 3306)),
        os.getenv("MYSQL_DB_REMOTO", "barquisimeto"),
        os.getenv("MYSQL_USER_REMOTO", "jonaiber"),
        os.getenv("MYSQL_PASS_REMOTO", ""),
        "barquisimeto (MySQL remoto)",
    )

    log("=== CAPA 2 (mirror local GB10) ===")
    c2a = check_sqlserver(
        os.getenv("PROFIT_SQL_HOST_LOCAL", "127.0.0.1"),
        os.getenv("PROFIT_SQL_PORT_LOCAL", "1433"),
        os.getenv("PROFIT_SQL_NAME_LOCAL", "PRUEB25_LOCAL"),
        os.getenv("PROFIT_SQL_USER_LOCAL", "sa"),
        os.getenv("PROFIT_SQL_PASS_LOCAL", ""),
        os.getenv("PROFIT_SQL_DRIVER", "SQL Server"),
        "PRUEB25_LOCAL (SQL Server mirror)",
    )
    c2b = check_mysql(
        os.getenv("MYSQL_HOST_LOCAL", "127.0.0.1"),
        int(os.getenv("MYSQL_PORT_LOCAL", 3306)),
        os.getenv("MYSQL_DB_LOCAL", "barquisimeto_local"),
        os.getenv("MYSQL_USER_LOCAL", "root"),
        os.getenv("MYSQL_PASS_LOCAL", ""),
        "barquisimeto_local (MySQL mirror)",
    )

    log("=== CAPA 3 (ARA_LLM SQLite) ===")
    c3 = check_sqlite(os.getenv("LLM_DB_PATH", "./ara_llm.db"))

    ok = sum([c1a, c1b, c2a, c2b, c3])
    log(f"RESULTADO: {ok}/5 conexiones operativas")
    if ok == 5:
        log("Todo listo. ARA Brain puede arrancar.")
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
