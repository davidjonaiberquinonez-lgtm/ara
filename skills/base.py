"""Módulo base compartido de los Skills de IA Local del Almacén (Proyecto ARA).

Centraliza lo común a todos los skills de la carpeta ``skills/``:

* Conexión multi-motor (SQL Server ``CRISTM25`` y MySQL del legacy ``192.168.4.148``),
  con selección automática del motor disponible (env sobre escribibles).
* Resolución dinámica de tablas y columnas vía INFORMATION_SCHEMA (patrón del
  proyecto: nunca se asumen nombres de esquema — `profit_sql_adapter.py`,
  `profit_renglones_sync.py`, `LegacyMySQLConnector`).
* Helpers de fechas y placeholders por motor, sanitización de cadenas y logs.

Reglas del proyecto: best-effort absoluto (los skills NUNCA lanzan excepciones
que rompan el flujo; retornan dicts con `status`/`aviso`), diagnóstico honesto
cuando el esquema real no contiene las columnas del mapeo estricto
(DESPACHO/DEPOSITO/DESPACHO_BQTO/DEPOSTO_BQTO/STOCK_ACT).
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Configuración por entorno (.env → getenv; mismos nombres que el resto del repo)
# ─────────────────────────────────────────────────────────────────────────────

_ENV_SQLSERVER: Dict[str, str] = {
    "host": "PROFIT_DB_HOST",
    "port": "PROFIT_DB_PORT",
    "user": "PROFIT_DB_USER",
    "pass": "PROFIT_DB_PASS",
    "db": "PROFIT_DB_NAME",
}

_ENV_MYSQL: Dict[str, str] = {
    "host": "MYSQL_HOST",
    "user": "MYSQL_USER",
    "pass": "MYSQL_PASSWORD",
    "port": "MYSQL_PORT",
    "db": "MYSQL_DATABASE",
}

_DEFAULTS_SQLSERVER: Dict[str, str] = {
    "host": "192.168.4.20",
    "port": "1433",
    "user": "profit",
    "pass": "profit",
    "db": "CRISTM25",
}

_DEFAULTS_MYSQL: Dict[str, str] = {
    "host": "192.168.4.148",
    "port": "3306",
    "user": "jonaiber",
    "pass": "Crist2026.",
}

# Tablas candidatas por rol funcional (orden de preferencia).
CAND_TABLAS_ARTICULOS: Tuple[str, ...] = ("articulos", "inventario", "art", "productos")
CAND_TABLAS_REGLONES: Tuple[str, ...] = ("reng_fac", "reng_nde", "reng_com")
CAND_TABLAS_TRASLADO: Tuple[str, ...] = ("traslado", "traslados", "manifiesto")

# Columnas candidatas por rol funcional (orden de preferencia).
CAND_COL_COD_ART: Tuple[str, ...] = ("cod_art", "co_art", "codigo", "articulo", "sku", "codart")
CAND_COL_DESC: Tuple[str, ...] = ("descripcion", "art_des", "desc", "nombre", "descripcion_art")
CAND_COL_STOCK: Tuple[str, ...] = ("stock_act", "stock", "stock_total", "existencias", "cantidad_total")
CAND_COL_STOCK_PICKING_SC: Tuple[str, ...] = ("despacho", "stock_despacho", "stock_picking", "almacen_01")
CAND_COL_STOCK_DEPOSITO_SC: Tuple[str, ...] = ("deposito", "stock_deposito", "stock_bulto", "almacen_02")
CAND_COL_STOCK_PICKING_BQTO: Tuple[str, ...] = ("despacho_bqto", "stock_despacho_bqto", "almacen_04")
CAND_COL_STOCK_DEPOSITO_BQTO: Tuple[str, ...] = ("deposto_bqto", "deposito_bqto", "stock_deposito_bqto", "almacen_05")
CAND_COL_LABORATORIO: Tuple[str, ...] = ("laboratorio", "lab", "proveedor", "cod_lab", "desc_lab", "proveedor_nombre")
CAND_COL_UBICACION: Tuple[str, ...] = ("ubicacion", "campo7", "ubicacion_deposito", "ubi")
CAND_COL_FECHA: Tuple[str, ...] = ("fec_emis", "fecha", "fe_emis", "fec_fac", "fecha_emision", "fecha_registro")
CAND_COL_CANTIDAD: Tuple[str, ...] = ("cantidad", "cant", "unidades", "cantidad_entregada", "total_art", "cant_prod")
CAND_COL_NRO_DOC: Tuple[str, ...] = ("fact_num", "num_doc", "nota", "numero_documento", "codigo_documento")


def _cargar_env_defensivo() -> None:
    """Carga claves relevantes del .env del proyecto (setdefault, no pisa)."""
    ruta = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
    )
    if not os.path.exists(ruta):
        return
    claves = set(_ENV_SQLSERVER.values()) | set(_ENV_MYSQL.values())
    try:
        with open(ruta, encoding="utf-8", errors="replace") as f:
            for linea in f:
                linea = linea.strip()
                if not linea or linea.startswith("#") or "=" not in linea:
                    continue
                clave, valor = linea.split("=", 1)
                clave = clave.strip()
                if clave in claves:
                    os.environ.setdefault(clave, valor.strip().strip('"'))
    except Exception:
        pass


_cargar_env_defensivo()


def _config_motor(env_map: Dict[str, str], defaults: Dict[str, str]) -> Dict[str, str]:
    cfg: Dict[str, str] = {}
    for key, env_name in env_map.items():
        valor = os.getenv(env_name, "").strip()
        cfg[key] = valor if valor else defaults.get(key, "")
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# Conexiones
# ─────────────────────────────────────────────────────────────────────────────

def conectar_sqlserver() -> Tuple[Optional[Any], str]:
    """Abre conexión pyodbc a SQL Server (CRISTM25 por defecto).

    Retorna ``(conn, "")`` en éxito o ``(None, "mensaje")`` en fallo
    (nunca lanza). Driver detectado entre los instalados: ODBC 18/17/SQL Server.
    """
    try:
        import pyodbc  # dependencia solo de este camino
    except ImportError as e:
        return None, f"pyodbc no instalado: {e}"
    cfg = _config_motor(_ENV_SQLSERVER, _DEFAULTS_SQLSERVER)
    drivers = [d for d in pyodbc.drivers()]
    candidatos = ["{ODBC Driver 18 for SQL Server}", "{ODBC Driver 17 for SQL Server}", "{SQL Server}"]
    driver = next((c for c in candidatos if c in drivers), None)
    if driver is None:
        return None, "sin driver ODBC de SQL Server instalado"
    conn_str = (
        f"DRIVER={driver};SERVER={cfg['host']},{cfg['port']};"
        f"DATABASE={cfg['db']};UID={cfg['user']};PWD={cfg['pass']};"
        f"Connection Timeout=8;TrustServerCertificate=yes"
    )
    try:
        return pyodbc.connect(conn_str), ""
    except Exception as e:
        return None, str(e)


def conectar_mysql() -> Tuple[Optional[Any], str]:
    """Abre conexión pymysql al MySQL del legacy (192.168.4.148 por defecto).

    Si ``MYSQL_DATABASE`` no está definida, la BD se resuelve por primera
    aparición de una tabla candidata (ver ``resolver_bd_mysql``). Nunca lanza.
    """
    try:
        import pymysql  # dependencia solo de este camino
    except ImportError as e:
        return None, f"pymysql no instalado: {e}"
    cfg = _config_motor(_ENV_MYSQL, _DEFAULTS_MYSQL)
    try:
        port = int(cfg["port"] or 3306)
    except ValueError:
        port = 3306
    try:
        conn = pymysql.connect(
            host=cfg["host"],
            user=cfg["user"],
            password=cfg["pass"],
            port=port,
            database=cfg["db"] or None,
            charset="utf8mb4",
            connect_timeout=8,
            read_timeout=16,
            write_timeout=16,
            autocommit=False,
        )
        return conn, ""
    except Exception as e:
        return None, str(e)


def conectar(engine: str = "auto") -> Tuple[Optional[Any], str, str]:
    """Conexión multi-motor: 'auto' prueba SQL Server y cae a MySQL.

    Retorna ``(conn, motor, err)`` con motor en ``"sqlserver"``/``"mysql"``.
    Nunca lanza; si ambos fallan devuelve ``(None, "", aviso)``.
    """
    if engine in ("auto", "sqlserver"):
        conn, err = conectar_sqlserver()
        if conn is not None:
            return conn, "sqlserver", ""
        if engine == "sqlserver":
            return None, "sqlserver", err
    if engine in ("auto", "mysql"):
        conn, err = conectar_mysql()
        if conn is not None:
            return conn, "mysql", ""
        if engine == "mysql":
            return None, "mysql", err
    return None, "", "ningún motor disponible (SQL Server y MySQL fallaron)"


def placeholder(motor: str) -> str:
    """Placeholder de parámetros por motor: '?' (pyodbc) / '%s' (pymysql)."""
    return "?" if motor == "sqlserver" else "%s"


def cerrar(conn: Optional[Any]) -> None:
    """Cierra la conexión sin lanzar."""
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Esquema dinámico (INFORMATION_SCHEMA)
# ─────────────────────────────────────────────────────────────────────────────

def listar_tablas(conn: Any, motor: str) -> List[str]:
    """Todas las tablas del esquema activo (filtra bases de sistema).

    MySQL: opera sobre la BD activa (``DATABASE()``); si el usuario no tiene
    BD por defecto, llame antes a ``resolver_bd_mysql``/``resolver_tabla``.
    """
    try:
        cur = conn.cursor()
        if motor == "mysql":
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = DATABASE() "
                "ORDER BY table_name"
            )
        else:
            cur.execute(
                "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME"
            )
        filas = [r[0] for r in cur.fetchall()]
        cur.close()
        return [str(x) for x in filas]
    except Exception:
        return []


def listar_columnas(conn: Any, motor: str, tabla: str) -> List[str]:
    """Columnas de la tabla indicada (case-insensitive del lado Python)."""
    try:
        cur = conn.cursor()
        if motor == "mysql":
            cur.execute(
                "SELECT COLUMN_NAME FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = %s "
                "ORDER BY ORDINAL_POSITION",
                (tabla,),
            )
        else:
            cur.execute(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME = ? ORDER BY ORDINAL_POSITION",
                (tabla,),
            )
        filas = [r[0] for r in cur.fetchall()]
        cur.close()
        return [str(x) for x in filas]
    except Exception:
        return []


def resolver_tabla(conn: Any, motor: str, candidatas: Tuple[str, ...]) -> Optional[str]:
    """Primera tabla candidata que exista en el esquema (retorna el nombre real).

    MySQL: primero resuelve la BD concreta que contiene las tablas candidatas
    (``USE``), para que el listado opere sobre la BD funcional correcta y no
    sobre todo el servidor.
    """
    if motor == "mysql":
        resolver_bd_mysql(conn, candidatas)
    tablas = {t.lower(): t for t in listar_tablas(conn, motor)}
    for cand in candidatas:
        if cand.lower() in tablas:
            return tablas[cand.lower()]
    return None


def resolver_columna(columnas: List[str], candidatas: Tuple[str, ...]) -> Optional[str]:
    """Primera columna candidata presente en la lista (retorna el nombre real)."""
    lower = {c.lower(): c for c in columnas}
    for cand in candidatas:
        if cand.lower() in lower:
            return lower[cand.lower()]
    return None


def resolver_bd_mysql(conn: Any, candidatas_tablas: Tuple[str, ...] = CAND_TABLAS_ARTICULOS) -> Optional[str]:
    """Resuelve la BD MySQL del legacy con las tablas candidatas (copia activa)."""
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT table_schema FROM information_schema.tables "
            "WHERE table_schema NOT IN "
            "('information_schema','mysql','performance_schema','sys') "
            "AND table_name IN (%s, %s, %s, %s) "
            "GROUP BY table_schema ORDER BY table_schema",
            tuple(candidatas_tablas[:4]),
        )
        bds = [str(r[0]) for r in cur.fetchall()]
        cur.close()
        if not bds:
            return None
        bd = bds[0]
        try:
            cur = conn.cursor()
            cur.execute(f"USE `{bd}`")
            cur.close()
        except Exception:
            pass
        return bd
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de SQL por motor
# ─────────────────────────────────────────────────────────────────────────────

def condicion_desde_fecha(motor: str, col_fecha: str, dias: int) -> str:
    """Expresión SQL 'col_fecha >= inicio' para los últimos N días, por motor."""
    if motor == "mysql":
        return f"`{col_fecha}` >= DATE_SUB(CURDATE(), INTERVAL {int(dias)} DAY)"
    return f"CAST([{col_fecha}] AS DATE) >= CAST(DATEADD(day, -{int(dias)}, GETDATE()) AS DATE)"


def con_placeholder(motor: str, expr: str) -> str:
    """Sustituye el marcador {ph} por el placeholder del motor."""
    return expr.replace("{ph}", placeholder(motor))


# ─────────────────────────────────────────────────────────────────────────────
# Misceláneos
# ─────────────────────────────────────────────────────────────────────────────

def san(valor: Any) -> str:
    """Cadena segura para reportes (nunca None)."""
    if valor is None:
        return ""
    return str(valor).strip()


def num(valor: Any, default: float = 0.0) -> float:
    """Conversión numérica segura."""
    try:
        return float(valor)
    except (TypeError, ValueError):
        return default


def _print_sync(*args: Any) -> None:
    """Log con prefijo del ecosistema de skills (robusto a consolas sin UTF-8)."""
    try:
        print("[SKILLS]", *args, flush=True)
    except Exception:
        pass


def diagnostico_conexion() -> str:
    """Texto de diagnóstico de conectividad para avisos honestos."""
    conn, motor, err = conectar()
    if conn is None:
        return f"ningún motor disponible: {err}"
    cerrar(conn)
    return f"conexión activa vía {motor}"


if __name__ == "__main__":
    conn, motor, err = conectar()
    if conn is None:
        print("conexión fallida:", err)
        sys.exit(1)
    print("motor:", motor)
    print("tablas de artículos candidatas:", resolver_tabla(conn, motor, CAND_TABLAS_ARTICULOS))
    cerrar(conn)
