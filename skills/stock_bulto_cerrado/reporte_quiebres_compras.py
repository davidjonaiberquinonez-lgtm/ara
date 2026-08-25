"""Skill de Reporte de Quiebres para Compras — Agotados totales por Laboratorio.

Identifica artículos con quiebre TOTAL (bulto cerrado vacío en ambas sedes y
stock global en 0), los clasifica por demanda (ABC/Tiers: Top 20% de salidas
históricas = Tier 1 de acción rápida) y genera un reporte ejecutivo agrupado
por laboratorio/proveedor en Excel (openpyxl) o PDF (reportlab).

Reglas documentadas (ajustables):

  * Quiebre total: ``DEPOSITO == 0`` AND ``DEPOSTO_BQTO == 0`` AND ``STOCK_ACT == 0``.
  * Tier 1: artículos dentro del Top 20% con mayor volumen de salidas
    históricas (ventana ``DIAS_ROTACION``); el resto Tier 2.
  * Sugerencia estimada de compra por SKU: unidades despachadas en la ventana
    de rotación; si no hay histórico de ventas, ``SUGERENCIA_MINIMA`` (default 1).

Salidas: dict estructurado + archivo generado (Excel/PDF/CSV degradado).
Best-effort: si la librería de salida no está instalada se degrada a CSV/JSON
con aviso; nunca lanza excepción.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Tuple

from skills import base

DIAS_ROTACION: int = 30
FRACCION_TIER1: float = 0.20  # Top 20% de demanda = Tier 1.
SUGERENCIA_MINIMA: int = 1
DIR_SALIDA: str = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reportes_quiebres")


def clasificar_tier(volumen: float, umbral_tier1: float) -> str:
    """Tier por demanda: Tier 1 si volumen >= umbral (Top 20%), si no Tier 2."""
    return "Tier 1" if volumen >= umbral_tier1 else "Tier 2"


def _quiebres_y_volumenes(
    conn: Any,
    motor: str,
    tabla: str,
    cols: List[str],
    dias: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, float], Dict[str, str]]:
    """SKUs con quiebre total + volúmenes históricos + laboratorio por SKU."""
    col_cod = base.resolver_columna(cols, base.CAND_COL_COD_ART)
    col_desc = base.resolver_columna(cols, base.CAND_COL_DESC)
    col_stock = base.resolver_columna(cols, base.CAND_COL_STOCK)
    col_dep_sc = base.resolver_columna(cols, base.CAND_COL_STOCK_DEPOSITO_SC)
    col_dep_bqto = base.resolver_columna(cols, base.CAND_COL_STOCK_DEPOSITO_BQTO)
    col_lab = base.resolver_columna(cols, base.CAND_COL_LABORATORIO)

    if col_cod is None or col_stock is None or col_dep_sc is None or col_dep_bqto is None:
        return [], {}, {}

    def q(nombre: str) -> str:
        return f"[{nombre}]" if motor == "sqlserver" else f"`{nombre}`"

    cur = conn.cursor()
    cur.execute(
        "SELECT " + ", ".join(
            [q(col_cod)]
            + ([q(col_desc)] if col_desc else [])
            + ([q(col_lab)] if col_lab else [])
            + [q(col_stock), q(col_dep_sc), q(col_dep_bqto)]
        ) + " FROM " + q(tabla)
        + " WHERE " + q(col_dep_sc) + " = 0 AND " + q(col_dep_bqto) + " = 0"
        + " AND " + q(col_stock) + " = 0"
    )
    filas = cur.fetchall()
    cur.close()

    laboratorios: Dict[str, str] = {}
    quiebres: List[Dict[str, Any]] = []
    for fila in filas:
        idx = 0
        cod = base.san(fila[idx]); idx += 1
        desc = base.san(fila[idx]) if col_desc else ""; idx += 1 if col_desc else 0
        lab = base.san(fila[idx]) if col_lab else ""; idx += 1 if col_lab else 0
        if not cod:
            continue
        quiebres.append({
            "cod_art": cod,
            "descripcion": desc,
            "laboratorio": lab or "SIN_LABORATORIO",
            "stock_act": 0,
            "deposito_sc": 0,
            "deposito_bqto": 0,
        })
        laboratorios[cod] = lab or "SIN_LABORATORIO"

    volumenes = _consulta_volumenes(conn, motor, col_cod, dias)
    return quiebres, volumenes, laboratorios


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


def _exportar_excel(datos: Dict[str, Any], ruta: str) -> Optional[str]:
    """Excel con una pestaña de resumen + una por laboratorio (openpyxl)."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
    except ImportError as e:
        return str(e)

    wb = Workbook()
    hoja = wb.active
    hoja.title = "Resumen"
    resumen = datos["resumen"]
    hoja.append(["REPORTE DE QUIEBRES PARA COMPRAS"])
    hoja.append(["Generado", resumen["generado"]])
    hoja.append(["Total SKUs Agotados", resumen["total_skus_agotados"]])
    hoja.append(["Tier 1 (Top 20%)", resumen["total_tier1"]])
    hoja.append(["Tier 2", resumen["total_tier2"]])
    hoja.append(["Sugerencia estimada total", resumen["sugerencia_total"]])
    hoja.append(["Laboratorios", resumen["total_laboratorios"]])
    hoja.append([])
    hoja.append(["Laboratorio", "SKUs Agotados", "Tier 1", "Sugerencia Estimada"])
    for lab in resumen["por_laboratorio"]:
        hoja.append([
            lab["laboratorio"],
            lab["total_skus"],
            lab["tier1_count"],
            lab["sugerencia_estimada"],
        ])
    ancho = [24, 14, 8, 20]
    for i, w in enumerate(ancho, start=1):
        hoja.column_dimensions[chr(64 + i)].width = w

    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")
    for celda in hoja[1]:
        celda.fill = header_fill
        celda.font = header_font

    for lab in resumen["por_laboratorio"]:
        ws = wb.create_sheet(lab["laboratorio"][:31] or "SIN_LABORATORIO")
        ws.append(["Código", "Descripción", "Tier", "Volumen 30d", "Sugerencia Estimada"])
        for fila in lab["detalle"]:
            ws.append([
                fila["cod_art"],
                fila["descripcion"],
                fila["tier"],
                fila["volumen_30d"],
                fila["sugerencia"],
            ])
        for i, w in enumerate([16, 44, 10, 14, 18], start=1):
            ws.column_dimensions[chr(64 + i)].width = w

    wb.save(ruta)
    return None


def _exportar_pdf(datos: Dict[str, Any], ruta: str) -> Optional[str]:
    """PDF ejecutivo con secciones por laboratorio (reportlab)."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        )
        from reportlab.lib import colors
    except ImportError as e:
        return str(e)

    estilos = getSampleStyleSheet()
    titulo = ParagraphStyle("Titulo", parent=estilos["Title"], fontSize=16)
    sub = ParagraphStyle("Sub", parent=estilos["Heading2"], fontSize=11, textColor=colors.HexColor("#1F4E78"))
    cuerpo = estilos["Normal"]

    historia: List[Any] = []
    resumen = datos["resumen"]
    historia.append(Paragraph("REPORTE DE QUIEBRES PARA COMPRAS", titulo))
    historia.append(Spacer(1, 4 * mm))
    historia.append(Paragraph(
        f"Generado: {resumen['generado']} · Total SKUs agotados: "
        f"{resumen['total_skus_agotados']} · Tier 1: {resumen['total_tier1']} · "
        f"Tier 2: {resumen['total_tier2']} · Sugerencia estimada: "
        f"{resumen['sugerencia_total']} und.", cuerpo
    ))
    historia.append(Spacer(1, 4 * mm))

    tabla_resumen = Table(
        [["Laboratorio", "SKUs", "Tier 1", "Sugerencia"]]
        + [
            [l["laboratorio"], str(l["total_skus"]), str(l["tier1_count"]), str(l["sugerencia_estimada"])]
            for l in resumen["por_laboratorio"]
        ],
        colWidths=[70 * mm, 20 * mm, 20 * mm, 35 * mm],
    )
    tabla_resumen.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E78")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
    ]))
    historia.append(tabla_resumen)
    historia.append(Spacer(1, 6 * mm))

    for lab in resumen["por_laboratorio"]:
        historia.append(Paragraph(f"Laboratorio: {lab['laboratorio']}", sub))
        detalle = Table(
            [["Código", "Descripción", "Tier", "Vol 30d", "Sugerencia"]]
            + [
                [
                    f["cod_art"], f["descripcion"], f["tier"],
                    str(f["volumen_30d"]), str(f["sugerencia"]),
                ]
                for f in lab["detalle"]
            ],
            colWidths=[22 * mm, 70 * mm, 16 * mm, 18 * mm, 22 * mm],
        )
        detalle.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D9E2F3")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
        ]))
        historia.append(detalle)
        historia.append(Spacer(1, 3 * mm))

    doc = SimpleDocTemplate(ruta, pagesize=letter)
    doc.build(historia)
    return None


def generar_reporte_compras_laboratorio(formato: str = "excel") -> Dict[str, Any]:
    """Reporte ejecutivo de quiebres totales agrupado por laboratorio.

    Parámetros:
        formato (str): 'excel' (openpyxl) o 'pdf' (reportlab); si la librería
        falta se degrada a CSV con aviso.

    Retorna dict con ``status``, ``archivo_generado``, ``ruta``, ``resumen``
    (incluye ``por_laboratorio`` con detalle por SKU) y ``aviso`` si hubo
    degradación. Best-effort absoluto.
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
        col_dep_sc = base.resolver_columna(cols, base.CAND_COL_STOCK_DEPOSITO_SC)
        col_dep_bqto = base.resolver_columna(cols, base.CAND_COL_STOCK_DEPOSITO_BQTO)
        col_lab = base.resolver_columna(cols, base.CAND_COL_LABORATORIO)

        if col_cod is None or col_stock is None or col_dep_sc is None or col_dep_bqto is None:
            return {
                "status": "error",
                "tabla_articulos": tabla,
                "requisitos": (
                    "El esquema debe exponer: STOCK_ACT (stock global), "
                    "DEPOSITO y DEPOSTO_BQTO (bulto cerrado S/C y BQTO)."
                ),
                "aviso": "Mapeo estricto de columnas no disponible en el esquema actual.",
            }

        quiebres, volumenes, laboratorios = _quiebres_y_volumenes(
            conn, motor, tabla, cols, DIAS_ROTACION
        )

        if not quiebres:
            return {
                "status": "ok",
                "aviso": "No hay artículos con quiebre total (DEPOSITO=0, DEPOSTO_BQTO=0, STOCK_ACT=0).",
                "resumen": {
                    "generado": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "total_skus_agotados": 0,
                    "total_tier1": 0,
                    "total_tier2": 0,
                    "sugerencia_total": 0,
                    "total_laboratorios": 0,
                    "por_laboratorio": [],
                },
                "tiempo_ms": int((time.perf_counter() - t0) * 1000),
            }

        # Umbral Tier 1 = Top 20% del volumen total de la cola de quiebres.
        volumen_total = sum(volumenes.get(q["cod_art"], 0.0) for q in quiebres)
        umbral_tier1 = volumen_total * FRACCION_TIER1 if volumen_total > 0 else 0.0

        por_laboratorio: Dict[str, Dict[str, Any]] = {}
        for q in quiebres:
            lab = q["laboratorio"]
            q["volumen_30d"] = volumenes.get(q["cod_art"], 0.0)
            q["tier"] = clasificar_tier(q["volumen_30d"], umbral_tier1)
            q["sugerencia"] = (
                int(q["volumen_30d"]) if q["volumen_30d"] >= SUGERENCIA_MINIMA else SUGERENCIA_MINIMA
            )
            grupo = por_laboratorio.setdefault(lab, {"detalle": [], "tier1": 0, "sugerencia": 0})
            grupo["detalle"].append(q)
            if q["tier"] == "Tier 1":
                grupo["tier1"] += 1
            grupo["sugerencia"] += q["sugerencia"]

        resumen_por_lab = []
        for lab in sorted(por_laboratorio):
            grupo = por_laboratorio[lab]
            resumen_por_lab.append({
                "laboratorio": lab,
                "total_skus": len(grupo["detalle"]),
                "tier1_count": grupo["tier1"],
                "sugerencia_estimada": grupo["sugerencia"],
                "detalle": grupo["detalle"],
            })

        resumen = {
            "generado": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_skus_agotados": len(quiebres),
            "total_tier1": sum(g["tier1"] for g in por_laboratorio.values()),
            "total_tier2": len(quiebres) - sum(g["tier1"] for g in por_laboratorio.values()),
            "sugerencia_total": sum(g["sugerencia"] for g in por_laboratorio.values()),
            "total_laboratorios": len(por_laboratorio),
            "por_laboratorio": resumen_por_lab,
            "umbral_tier1_volumen": round(umbral_tier1, 2),
        }

        # ── Generación del archivo ───────────────────────────────────────────
        try:
            os.makedirs(DIR_SALIDA, exist_ok=True)
        except Exception:
            pass
        fecha = time.strftime("%Y%m%d_%H%M%S")
        aviso = ""
        archivo = ""
        if formato.lower() == "pdf":
            archivo = os.path.join(DIR_SALIDA, f"reporte_quiebres_{fecha}.pdf")
            error = _exportar_pdf({"resumen": resumen}, archivo)
            if error:
                archivo = os.path.join(DIR_SALIDA, f"reporte_quiebres_{fecha}.csv")
                _exportar_csv({"resumen": resumen}, archivo)
                aviso = f"reportlab no disponible ({error}); degradado a CSV."
        else:
            archivo = os.path.join(DIR_SALIDA, f"reporte_quiebres_{fecha}.xlsx")
            error = _exportar_excel({"resumen": resumen}, archivo)
            if error:
                archivo = os.path.join(DIR_SALIDA, f"reporte_quiebres_{fecha}.csv")
                _exportar_csv({"resumen": resumen}, archivo)
                aviso = f"openpyxl no disponible ({error}); degradado a CSV."

        return {
            "status": "ok",
            "formato": "csv" if aviso else formato.lower(),
            "archivo_generado": os.path.basename(archivo),
            "ruta": archivo,
            "resumen": resumen,
            "aviso": aviso,
            "tiempo_ms": int((time.perf_counter() - t0) * 1000),
        }
    except Exception as e:
        base.traceback.print_exc()
        return {"status": "error", "aviso": f"Error generando el reporte: {e}"}
    finally:
        base.cerrar(conn)


def _exportar_csv(datos: Dict[str, Any], ruta: str) -> None:
    """Degradación: CSV plano del resumen por laboratorio."""
    import csv

    with open(ruta, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Laboratorio", "SKUs Agotados", "Tier 1", "Sugerencia Estimada"])
        for lab in datos["resumen"]["por_laboratorio"]:
            w.writerow([
                lab["laboratorio"], lab["total_skus"], lab["tier1_count"], lab["sugerencia_estimada"],
            ])
        w.writerow([])
        w.writerow(["Código", "Descripción", "Laboratorio", "Tier", "Volumen 30d", "Sugerencia"])
        for lab in datos["resumen"]["por_laboratorio"]:
            for d in lab["detalle"]:
                w.writerow([
                    d["cod_art"], d["descripcion"], d["laboratorio"],
                    d["tier"], d["volumen_30d"], d["sugerencia"],
                ])


if __name__ == "__main__":
    import json
    salida = generar_reporte_compras_laboratorio(formato="excel")
    print(json.dumps(salida, ensure_ascii=False, indent=2, default=str))
