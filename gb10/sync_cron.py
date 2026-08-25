#!/usr/bin/env python3
"""Sync periódico Capa 1 -> Capa 2 (SQL Server PRUEB25 + MySQL barquisimeto).
Corre en loop cada SYNC_INTERVAL_SECONDS. Un fallo puntual de red no detiene
el proceso (mismo criterio de resiliencia que vigilar_datos.py).
"""
import os
import time
from datetime import datetime

from dotenv import load_dotenv

load_dotenv(".env.gb10")

import mirror_mysql_barquisimeto as mysql_mirror
import mirror_sqlserver_pruebas25 as sqlserver_mirror

INTERVAL = int(os.getenv("SYNC_INTERVAL_SECONDS", 300))


def ciclo():
    for tabla in sqlserver_mirror.TABLAS:
        columnas = sqlserver_mirror.copiar_esquema(tabla)
        sqlserver_mirror.copiar_datos(tabla, columnas)
    for tabla in mysql_mirror.TABLAS:
        mysql_mirror.copiar_esquema(tabla)
        mysql_mirror.copiar_datos(tabla)


def main():
    while True:
        try:
            ciclo()
            print(f"[{datetime.now()}] Sync OK")
        except Exception as e:
            print(f"[{datetime.now()}] Sync ERROR: {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
