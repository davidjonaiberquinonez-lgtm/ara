"""Skill de Compras — Análisis de Fallas Top 20% y Alertas de Stock 0.

Analiza los quiebres de inventario (stock en 0) para el departamento de
Compras y los clasifica por criticidad:

  * FALLA CRÍTICA (Tier 1): SKUs dentro del Top 20% con mayor volumen de
    salidas históricas (ventana ``dias_rotacion``): son los agotados que
    más están vendiendo → compra inmediata.
  * FALLA CONTROLADA (Tier 2): el resto de los agotados (menor demanda).
  * ALERTA STOCK 0: listado de todos los SKUs con stock en 0 (incluye los
    sin histórico de ventas), para la matriz de reposición.

Reglas documentadas (ajustables por parámetros):

  * Quiebre = stock global (STOCK_ACT) <= 0 sobre la tabla de artículos real
    (art/articulos/productos según INFORMATION_SCHEMA).
  * Umbral Tier 1 = ``fraccion_tier1`` (default 0.20 = Top 20%) del volumen
    total despachado en la ventana por los SKUs agotados.
  * ``minimo_alertar`` (default 0.0): stock máximo para considerar SKU en
    alerta (0 = agotado estricto).

Best-effort absoluto: si el esquema real no expone el mapeo estricto, retorna
diagnóstico honesto con los requisitos; jamás lanza.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from skills import base

DIAS_ROTACION: int = 30
FRACCION_TIER1: float = 0.20
MINIMO_ALERTAR: float = 0.0


def clasificar_falla(volumen: float, umbral_tier1: float) -> str:
    """Criticidad de la falla por demanda: Tier 1 (crítica) si volumen >= umbral."""
    if volumen <= 0:
        return "Tier 2"
    return "Tier 1" if volumen >= umbral_tier1 else "Tier 2"


def _quiebres_stock_cero(
    conn: Any,
    motor: str,
    tabla: str,
    cols: List[str],
    minimo_alertar: float,
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """SKUs con stock <= minimo_alertar (alertas de stock 0) + laboratorio."""
    col_cod = base.resolver_columna(cols, base.CAND_COL_COD_ART)
    col_desc = base.resolver_columna(cols, base.CAND_COL_DESC)
    col_stock = base.resolver_columna(cols, base.CAND_COL_STOCK)
    col_lab = base.resolver_columna(cols, base.CAND_COL_LABORATORIO)

    if col_cod is None or col_stock is None:
        return [], {}

    def q(nombre: str) -> str:
        return f"[{nombre}]" if motor == "sqlserver" else f"`{nombre}`"

    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT " + ", ".join(
                [q(col_cod)]
                + ([q(col_desc)] if col_desc else [])
                + ([q(col_lab)] if col_lab else [])
                + [q(col_stock)]
            ) + " FROM " + q(tabla)
            + " WHERE " + q(col_stock) + " <= " + str(float(minimo_alertar))
        )
        filas = cur.fetchall()
        cur.close()
    except Exception:
        return [], {}

    alertas: List[Dict[str, Any]] = []
    laboratorios: Dict[str, str] = {}
    for fila in filas:
        idx = 0
        cod = base.san(fila[idx]); idx += 1
        desc = base.san(fila[idx]) if col_desc else ""; idx += 1 if col_desc else 0
        lab = base.san(fila[idx]) if col_lab else ""; idx += 1 if col_lab else 0
        stock = base.num(fila[idx]) if len(fila) > idx else 0.0
        if not cod:
            continue
        alertas.append({
            "cod_art": cod,
            "descripcion": desc,
            "laboratorio": lab or "SIN_LABORATORIO",
            "stock_act": stock,
            "alerta": "STOCK_0" if stock <= 0 else "STOCK_BAJO",
        })
        laboratorios[cod] = lab or "SIN_LABORATORIO"
    return alertas, laboratorios


def _consulta_volumenes(conn: Any, motor: str, col_art: str, dias: int) -> Dict[str, float]:
    """Unidades despachadas (reng_fac + reng_nde) por SKU en la ventana."""
    ph = base.placeholder(motor)
    totales: Dict[str, float] = {}
    try:
        cur = conn.cursor()
        for tabla in base.CAND_TABLAS_REGLONES:
            if not base.resolver_tabla(conn, motor, (tabla,)):
                continue
            cols = base.listar_columnas(conn, motor, tabla)
            col_cant = base.resolver_columna(cols, base.CAND_COL_CANTIDAD)
            col_fecha = base.resolver_columna(cols, base.CAND_COL_FECHA)
            if col_cant is None or col_fecha is None:
                continue
            qa = f"[{col_art}]" if motor == "sqlserver" else f"`{col_art}`"
            qc = f"[{col_cant}]" if motor == "sqlserver" else f"`{col_cant}`"
            qt = f"[{tabla}]" if motor == "sqlserver" else f"`{tabla}`"
            cur.execute(
                f"SELECT {qa}, SUM({qc}) FROM {qt} WHERE "
                + base.condicion_desde_fecha(motor, col_fecha, dias)
                + f" GROUP BY {qa}"
            )
            for fila in cur.fetchall():
                cod = base.san(fila[0])
                if cod:
                    totales[cod] = totales.get(cod, 0.0) + base.num(fila[1])
        cur.close()
    except Exception:
        pass
    return totales


def analizar_quiebres_compras(
    dias_rotacion: int = DIAS_ROTACION,
    fraccion_tier1: float = FRACCION_TIER1,
    minimo_alertar: float = MINIMO_ALERTAR,
) -> Dict[str, Any]:
    """Análisis de fallas de stock (Top 20% críticas) y alertas de stock 0.

    Parámetros:
        dias_rotacion (int): ventana de días para el volumen de salidas.
        fraccion_tier1 (float): fracción del volumen total para el umbral
            Tier 1 (default 0.20 = Top 20%).
        minimo_alertar (float): stock máximo considerado en alerta
            (default 0.0 = agotado estricto).

    Retorna dict con ``status``, ``alertas_stock_cero``, ``fallas_tier1``,
    ``fallas_tier2``, ``por_laboratorio`` y ``requisitos``/``aviso`` cuando el
    esquema no expone el mapeo estricto. Best-effort absoluto.
    """
    t0 = time.perf_counter()
    conn, motor, err = base.conectar()
    if conn is None:
        return {"status": "error", "aviso": f"No hay motor de BD disponible: {err}"}

    try:
        tabla = base.resolver_tabla(conn, motor, base.CAND_TABLAS_ARTICULOS)
        if tabla is None:
            return {
                "status": "error",
                "aviso": "Ninguna tabla de artículos encontrada (candidatas: "
                + ", ".join(base.CAND_TABLAS_ARTICULOS) + ").",
            }
        cols = base.listar_columnas(conn, motor, tabla)
        col_cod = base.resolver_columna(cols, base.CAND_COL_COD_ART)
        col_stock = base.resolver_columna(cols, base.CAND_COL_STOCK)

        if col_cod is None or col_stock is None:
            return {
                "status": "error",
                "tabla_articulos": tabla,
                "requisitos": "El esquema debe exponer: cod_art/co_art (código) y stock_act (stock global).",
                "aviso": "Mapeo estricto de columnas no disponible en el esquema actual.",
            }

        alertas, laboratorios = _quiebres_stock_cero(conn, motor, tabla, cols, minimo_alertar)
        volumenes = _consulta_volumenes(conn, motor, col_cod, int(dias_rotacion))

        volumen_total = sum(volumenes.get(a["cod_art"], 0.0) for a in alertas)
        umbral_tier1 = volumen_total * float(fraccion_tier1) if volumen_total > 0 else 0.0

        fallas_tier1: List[Dict[str, Any]] = []
        fallas_tier2: List[Dict[str, Any]] = []
        for a in alertas:
            a["volumen_rotacion"] = volumenes.get(a["cod_art"], 0.0)
            a["falla"] = clasificar_falla(a["volumen_rotacion"], umbral_tier1)
            (fallas_tier1 if a["falla"] == "Tier 1" else fallas_tier2).append(a)

        por_laboratorio: Dict[str, Dict[str, Any]] = {}
        for a in alertas:
            grupo = por_laboratorio.setdefault(a["laboratorio"], {"skus": 0, "tier1": 0, "alertas": 0})
            grupo["skus"] += 1
            if a["falla"] == "Tier 1":
                grupo["tier1"] += 1
            if a["alerta"] == "STOCK_0":
                grupo["alertas"] += 1

        return {
            "status": "ok",
            "motor": motor,
            "tabla_articulos": tabla,
            "dias_rotacion": int(dias_rotacion),
            "umbral_tier1_volumen": round(umbral_tier1, 2),
            "total_alertas_stock": len(alertas),
            "fallas_tier1": len(fallas_tier1),
            "fallas_tier2": len(fallas_tier2),
            "alertas_stock_cero": alertas,
            "fallas_criticas_top20": fallas_tier1,
            "por_laboratorio": [
                {
                    "laboratorio": lab,
                    "total_skus": g["skus"],
                    "tier1": g["tier1"],
                    "alertas_stock_0": g["alertas"],
                }
                for lab, g in sorted(por_laboratorio.items())
            ],
            "tiempo_ms": int((time.perf_counter() - t0) * 1000),
        }
    except Exception as e:
        return {"status": "error", "aviso": f"Error analizando quiebres: {e}"}
    finally:
        base.cerrar(conn)


if __name__ == "__main__":
    import json
    import sys

    dias = int(sys.argv[1]) if len(sys.argv) > 1 else DIAS_ROTACION
    salida = analizar_quiebres_compras(dias_rotacion=dias)
    print(json.dumps(salida, ensure_ascii=False, indent=2, default=str))
