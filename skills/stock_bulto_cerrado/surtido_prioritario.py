"""Skill de Consulta y Surtido Prioritario — Cola de Surtido de Bulto Cerrado.

Genera la cola de surtido prioritaria: artículos cuyo estante de picking está
en 0 pero que tienen stock en el Bulto Cerrado del depósito, ordenados de forma
descendente por velocidad de venta (rotación) de los últimos N días.

Mapeo estricto de columnas y almacenes (directiva):

  * Sede 'SC'  : ``DESPACHO == 0`` Y ``DEPOSITO > 0`` (Almacén 01 / 02 S/C).
  * Sede 'BQTO': ``DESPACHO_BQTO == 0`` Y ``DEPOSTO_BQTO > 0`` (Almacén 04 / 05 BQTO).
  * Rotación   : sumatoria de unidades despachadas en ``reng_fac`` + ``reng_nde``
    de los últimos ``dias_rotacion`` días.

Las columnas se resuelven dinámicamente vía INFORMATION_SCHEMA (nunca se
asumen nombres; ver `skills/base.py`). Si el esquema real no contiene las
columnas del mapeo estricto, la función retorna un diagnóstico honesto con los
requisitos (regla del proyecto: jamás respuestas vacías ni inventadas).

Prioridad: ALTA/MEDIA/BAJA por terciles de rotación — el tercio superior de
la cola es ALTA, el intermedio MEDIA y el resto BAJA (regla documentada y
ajustable con ``TERCIL_ALTO``/``TERCIL_BAJO``).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from skills import base

# Terciles de rotación para la clasificación de prioridad (0..1).
TERCIL_ALTO: float = 0.66
TERCIL_BAJO: float = 0.33

# Columnas de stock por rol (orden de preferencia en la resolución).
_COLUMNAS_STOCK_SC: Tuple[str, ...] = base.CAND_COL_STOCK_PICKING_SC
_COLUMNAS_DEPOSITO_SC: Tuple[str, ...] = base.CAND_COL_STOCK_DEPOSITO_SC
_COLUMNAS_STOCK_BQTO: Tuple[str, ...] = base.CAND_COL_STOCK_PICKING_BQTO
_COLUMNAS_DEPOSITO_BQTO: Tuple[str, ...] = base.CAND_COL_STOCK_DEPOSITO_BQTO


def clasificar_prioridad(rotacion: float, max_rotacion: float, min_rotacion: float) -> str:
    """Clasifica el nivel de prioridad por posición relativa de la rotación.

    Regla: rotación >= min + (max-min)*TERCIL_ALTO → ALTA;
    rotación >= min + (max-min)*TERCIL_BAJO → MEDIA; resto BAJA.
    Cola sin rango (todo 0 o un solo artículo) → ALTA si rotación > 0.
    """
    if rotacion <= 0:
        return "BAJA"
    rango = max_rotacion - min_rotacion
    if rango <= 0:
        return "ALTA"
    umbral_alto = min_rotacion + rango * TERCIL_ALTO
    umbral_bajo = min_rotacion + rango * TERCIL_BAJO
    if rotacion >= umbral_alto:
        return "ALTA"
    if rotacion >= umbral_bajo:
        return "MEDIA"
    return "BAJA"


def _filtrar_sede(cols: List[str], sede: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Resuelve (col_stock_estante, col_deposito, col_ubicacion) según sede."""
    stock = base.resolver_columna(cols, _COLUMNAS_STOCK_SC if sede == "SC" else _COLUMNAS_STOCK_BQTO)
    deposito = base.resolver_columna(cols, _COLUMNAS_DEPOSITO_SC if sede == "SC" else _COLUMNAS_DEPOSITO_BQTO)
    ubicacion = base.resolver_columna(cols, base.CAND_COL_UBICACION)
    return stock, deposito, ubicacion


def _consulta_rotacion(
    conn: Any,
    motor: str,
    col_art: str,
    dias: int,
) -> Dict[str, float]:
    """Sumatoria de unidades despachadas por artículo en los últimos N días.

    Consulta ``reng_fac`` y ``reng_nde`` (tablas que existan en el esquema);
    la cantidad y la fecha se resuelven por columnas candidatas. Retorna
    ``{cod_art: total_unidades}``. Nunca lanza.
    """
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
            sql = (
                f"SELECT [{col_art}]"
                if motor == "sqlserver"
                else f"SELECT `{col_art}`"
            )
            sql += ", SUM(" + (f"[{col_cant}]" if motor == "sqlserver" else f"`{col_cant}`") + ")"
            sql += " FROM " + (f"[{tabla}]" if motor == "sqlserver" else f"`{tabla}`")
            sql += " WHERE " + base.condicion_desde_fecha(motor, col_fecha, dias)
            sql += " GROUP BY " + (f"[{col_art}]" if motor == "sqlserver" else f"`{col_art}`")
            cur.execute(sql)
            for fila in cur.fetchall():
                cod = base.san(fila[0])
                if cod:
                    totales[cod] = totales.get(cod, 0.0) + base.num(fila[1])
        cur.close()
    except Exception:
        return totales
    return totales


def obtener_cola_surtido(sede: str = "SC", dias_rotacion: int = 30) -> Dict[str, Any]:
    """Cola de surtido prioritario de Bulto Cerrado para una sede.

    Parámetros:
        sede (str): 'SC' (San Cristóbal) o 'BQTO' (Barquisimeto).
        dias_rotacion (int): ventana de días para la rotación (default 30).

    Retorna::

        {
          "status": "ok"|"error",
          "sede": "SC",
          "motor": "sqlserver"|"mysql",
          "tabla_articulos": "articulos"|...,
          "columnas_resueltas": {"stock": "DESPACHO", "deposito": "DEPOSITO", ...},
          "cola": [
            {"cod_art", "descripcion", "ubicacion_deposito", "unidades_a_bajar",
             "rotacion_30d", "nivel_prioridad"},
            ...
          ],
          "total_articulos": int,
          "tiempo_ms": int,
          "aviso": "..."  # solo si hubo degradación o esquema incompleto
        }

    Si el esquema no tiene las columnas del mapeo estricto retorna ``status``
    ``"error"`` con los requisitos listados (diagnóstico honesto, nunca vacío).
    """
    t0 = base.time.perf_counter()
    sede_norm = sede.strip().upper()
    if sede_norm not in ("SC", "BQTO"):
        return {
            "status": "error",
            "sede": sede,
            "aviso": "Sede inválida: use 'SC' (San Cristóbal) o 'BQTO' (Barquisimeto).",
        }

    conn, motor, err = base.conectar()
    if conn is None:
        return {
            "status": "error",
            "sede": sede_norm,
            "aviso": f"No hay motor de BD disponible: {err}",
        }

    try:
        tabla = base.resolver_tabla(conn, motor, base.CAND_TABLAS_ARTICULOS)
        if tabla is None:
            return {
                "status": "error",
                "sede": sede_norm,
                "motor": motor,
                "aviso": (
                    "Ninguna tabla de artículos encontrada (candidatas: "
                    + ", ".join(base.CAND_TABLAS_ARTICULOS) + ")."
                ),
            }

        cols = base.listar_columnas(conn, motor, tabla)
        col_cod = base.resolver_columna(cols, base.CAND_COL_COD_ART)
        col_desc = base.resolver_columna(cols, base.CAND_COL_DESC)
        col_stock, col_deposito, col_ubicacion = _filtrar_sede(cols, sede_norm)

        if col_cod is None or col_stock is None or col_deposito is None:
            return {
                "status": "error",
                "sede": sede_norm,
                "motor": motor,
                "tabla_articulos": tabla,
                "columnas_resueltas": {
                    "cod_art": col_cod,
                    "descripcion": col_desc,
                    "stock_estante": col_stock,
                    "deposito_bulto_cerrado": col_deposito,
                },
                "requisitos": (
                    "El esquema debe exponer (para SC): DESPACHO/DESPACHO_BQTO "
                    "(stock del estante de picking) y DEPOSITO/DEPOSTO_BQTO "
                    "(stock del bulto cerrado) en la tabla de artículos."
                ),
                "aviso": "Mapeo estricto de columnas no disponible en el esquema actual.",
            }

        # Consulta de la cola filtrada.
        q = "SELECT "
        q += (f"[{col_cod}]" if motor == "sqlserver" else f"`{col_cod}`")
        if col_desc:
            q += ", " + (f"[{col_desc}]" if motor == "sqlserver" else f"`{col_desc}`")
        if col_ubicacion:
            q += ", " + (f"[{col_ubicacion}]" if motor == "sqlserver" else f"`{col_ubicacion}`")
        q += ", " + (f"[{col_deposito}]" if motor == "sqlserver" else f"`{col_deposito}`")
        q += " FROM " + (f"[{tabla}]" if motor == "sqlserver" else f"`{tabla}`")
        q += " WHERE "
        q += (f"[{col_stock}] = 0" if motor == "sqlserver" else f"`{col_stock}` = 0")
        q += " AND "
        q += (f"[{col_deposito}] > 0" if motor == "sqlserver" else f"`{col_deposito}` > 0")

        cur = conn.cursor()
        cur.execute(q)
        filas = cur.fetchall()
        cur.close()

        candidatos: List[Dict[str, Any]] = []
        for fila in filas:
            candidatos.append({
                "cod_art": base.san(fila[0]),
                "descripcion": base.san(fila[1]) if col_desc else "",
                "ubicacion_deposito": base.san(fila[2]) if col_ubicacion else "",
                "unidades_a_bajar": base.num(fila[3]),
            })

        # Rotación histórica (reng_fac + reng_nde) en la ventana.
        rotacion = _consulta_rotacion(conn, motor, col_cod, dias_rotacion)
        for c in candidatos:
            c["rotacion_30d"] = rotacion.get(c["cod_art"], 0.0)

        # Orden descendente por rotación (Prioridad 1 = el más vendido en 0).
        candidatos.sort(key=lambda c: c["rotacion_30d"], reverse=True)

        # Clasificación por terciles sobre la cola ya ordenada.
        if candidatos:
            rots = [c["rotacion_30d"] for c in candidatos]
            max_r, min_r = max(rots), min(rots)
            for c in candidatos:
                c["nivel_prioridad"] = clasificar_prioridad(c["rotacion_30d"], max_r, min_r)

        aviso = ""
        if not candidatos:
            aviso = "Ningún artículo cumple el filtro (estante en 0 con stock en bulto cerrado)."
        elif not col_ubicacion:
            aviso = "Columna de ubicación no disponible; 'ubicacion_deposito' vacío."

        return {
            "status": "ok",
            "sede": sede_norm,
            "motor": motor,
            "tabla_articulos": tabla,
            "columnas_resueltas": {
                "cod_art": col_cod,
                "descripcion": col_desc,
                "stock_estante": col_stock,
                "deposito_bulto_cerrado": col_deposito,
                "ubicacion": col_ubicacion,
            },
            "cola": candidatos,
            "total_articulos": len(candidatos),
            "tiempo_ms": int((base.time.perf_counter() - t0) * 1000),
            "aviso": aviso,
        }
    except Exception as e:
        base.traceback.print_exc()
        return {
            "status": "error",
            "sede": sede_norm,
            "motor": motor,
            "aviso": f"Error consultando la cola de surtido: {e}",
        }
    finally:
        base.cerrar(conn)


if __name__ == "__main__":
    import json
    salida = obtener_cola_surtido(sede="SC", dias_rotacion=30)
    print(json.dumps(salida, ensure_ascii=False, indent=2, default=str))
