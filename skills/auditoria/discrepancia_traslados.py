"""Skill de Auditoría — Discrepancia en Traslados Inter-Sedes (S/C → BQTO).

Concilia el manifiesto de carga emitido en ORIGEN (Almacén 02 S/C, columna
``DEPOSITO``) contra el conteo físico de recepción en DESTINO (Almacén 05
BQTO, columna ``DEPOSTO_BQTO``) para un código de traslado.

Si existen faltantes o averías:

  * Genera un **Acta de Discrepancia en Tránsito** (PDF normalizado + JSON).
  * Marca el estado del renglón afectado como ``EN_RECLAMO`` (best-effort,
    solo con ``modo_real=True``; por defecto la escritura queda en dry-run).

Tablas y columnas se resuelven dinámicamente vía INFORMATION_SCHEMA
(candidatas: ``traslado``/``traslados``/``manifiesto`` con detalle). Si el
esquema real no expone las tablas o las columnas del mapeo estricto, se
retorna un diagnóstico honesto con los requisitos (jamás respuesta vacía).
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from skills import base

DIR_ACTAS: str = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "actas_discrepancia")

CAND_COL_COD_TRASLADO: Tuple[str, ...] = (
    "cod_traslado", "nro_traslado", "numero_traslado", "num_traslado", "codigo", "id",
)
CAND_COL_ESTADO_RENGLON: Tuple[str, ...] = ("estado", "estatus", "statu")
CAND_COL_CANT_ENVIADA: Tuple[str, ...] = ("cantidad", "cant", "enviado", "cantidad_enviada", "unidades")
CAND_COL_CANT_RECIBIDA: Tuple[str, ...] = (
    "cantidad_recibida", "recibido", "cantidad_llegada", "conteo_fisico",
)
CAND_COL_AVERIAS: Tuple[str, ...] = ("averias", "averia", "observaciones", "danos", "nota")

VALOR_RECLAMO: str = "EN_RECLAMO"


def _detalle_manifiesto(
    conn: Any,
    motor: str,
    cod_traslado: str,
) -> Dict[str, Any]:
    """Lee el manifiesto del traslado (cabecera + renglones) y el esquema usado."""
    try:
        tabla = base.resolver_tabla(conn, motor, base.CAND_TABLAS_TRASLADO)
        if tabla is None:
            return {"esquema_ok": False, "aviso": "Ninguna tabla de traslado encontrada "
                    "(candidatas: " + ", ".join(base.CAND_TABLAS_TRASLADO) + ")."}

        cols = base.listar_columnas(conn, motor, tabla)
        col_id = base.resolver_columna(cols, CAND_COL_COD_TRASLADO)
        col_cod_art = base.resolver_columna(cols, base.CAND_COL_COD_ART)
        col_desc = base.resolver_columna(cols, base.CAND_COL_DESC)
        col_cant = base.resolver_columna(cols, CAND_COL_CANT_ENVIADA)
        col_recibido = base.resolver_columna(cols, CAND_COL_CANT_RECIBIDA)
        col_averias = base.resolver_columna(cols, CAND_COL_AVERIAS)
        col_estado = base.resolver_columna(cols, CAND_COL_ESTADO_RENGLON)

        if col_id is None or col_cod_art is None or col_cant is None:
            return {
                "esquema_ok": False,
                "aviso": (
                    "La tabla de traslado no tiene las columnas requeridas "
                    "(código de traslado, código de artículo, cantidad enviada)."
                ),
            }

        def q(nombre: str) -> str:
            return f"[{nombre}]" if motor == "sqlserver" else f"`{nombre}`"

        cur = conn.cursor()
        cur.execute(
            "SELECT " + ", ".join(
                [q(col_id), q(col_cod_art)]
                + ([q(col_desc)] if col_desc else [])
                + [q(col_cant)]
                + ([q(col_recibido)] if col_recibido else [])
                + ([q(col_averias)] if col_averias else [])
                + ([q(col_estado)] if col_estado else [])
            ) + " FROM " + q(tabla)
            + " WHERE " + q(col_id) + " = " + base.placeholder(motor),
            (cod_traslado,),
        )
        filas = cur.fetchall()
        cur.close()

        renglones: List[Dict[str, Any]] = []
        for fila in filas:
            idx = 0
            id_traslado = base.san(fila[idx]); idx += 1
            cod = base.san(fila[idx]); idx += 1
            desc = base.san(fila[idx]) if col_desc else ""; idx += 1 if col_desc else 0
            cant = base.num(fila[idx]); idx += 1
            recibido = base.num(fila[idx]) if col_recibido else None; idx += 1 if col_recibido else 0
            averias = base.san(fila[idx]) if col_averias else ""; idx += 1 if col_averias else 0
            estado = base.san(fila[idx]) if col_estado else ""; idx += 1 if col_estado else 0
            if not cod:
                continue
            renglones.append({
                "id_traslado": id_traslado,
                "cod_art": cod,
                "descripcion": desc,
                "cantidad_enviada": cant,
                "cantidad_recibida_registrada": recibido,
                "averias": averias,
                "estado_actual": estado,
            })

        if not renglones:
            return {
                "esquema_ok": True,
                "aviso": f"El traslado {cod_traslado} no tiene renglones en el manifiesto.",
                "tabla": tabla,
                "columnas": cols,
            }

        return {
            "esquema_ok": True,
            "tabla": tabla,
            "columnas_resueltas": {
                "id_traslado": col_id,
                "cod_art": col_cod_art,
                "descripcion": col_desc,
                "cantidad_enviada": col_cant,
                "cantidad_recibida": col_recibido,
                "averias": col_averias,
                "estado": col_estado,
            },
            "renglones": renglones,
            "total_items_manifiesto": len(renglones),
        }
    except Exception as e:
        return {"esquema_ok": False, "aviso": f"Error leyendo el manifiesto: {e}"}


def _stock_destino(conn: Any, motor: str, codigos: List[str]) -> Dict[str, float]:
    """Stock físico actual en destino (DEPOSTO_BQTO / almacén 05) por SKU."""
    if not codigos:
        return {}
    try:
        tabla = base.resolver_tabla(conn, motor, base.CAND_TABLAS_ARTICULOS)
        if tabla is None:
            return {}
        cols = base.listar_columnas(conn, motor, tabla)
        col_cod = base.resolver_columna(cols, base.CAND_COL_COD_ART)
        col_dest = base.resolver_columna(cols, base.CAND_COL_STOCK_DEPOSITO_BQTO)
        if col_cod is None or col_dest is None:
            return {}

        def q(nombre: str) -> str:
            return f"[{nombre}]" if motor == "sqlserver" else f"`{nombre}`"

        stock: Dict[str, float] = {}
        cur = conn.cursor()
        for i in range(0, len(codigos), 200):
            lote = codigos[i:i + 200]
            marks = ", ".join([base.placeholder(motor)] * len(lote))
            cur.execute(
                f"SELECT {q(col_cod)}, {q(col_dest)} FROM {q(tabla)} "
                f"WHERE {q(col_cod)} IN ({marks})",
                lote,
            )
            for fila in cur.fetchall():
                stock[base.san(fila[0])] = base.num(fila[1])
        cur.close()
        return stock
    except Exception:
        return {}


def _generar_acta_pdf(acta: Dict[str, Any], ruta: str) -> Optional[str]:
    """Acta de Discrepancia en Tránsito (reportlab). None = éxito."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    except ImportError as e:
        return str(e)

    estilos = getSampleStyleSheet()
    titulo = ParagraphStyle("T", parent=estilos["Title"], fontSize=15, textColor=colors.HexColor("#8B0000"))
    sub = ParagraphStyle("S", parent=estilos["Heading2"], fontSize=10)
    cuerpo = estilos["Normal"]

    historia: List[Any] = []
    historia.append(Paragraph("ACTA DE DISCREPANCIA EN TRÁNSITO", titulo))
    historia.append(Spacer(1, 3 * mm))
    historia.append(Paragraph(
        f"Traslado: {acta['cod_traslado']} · Origen: S/C Almacén 02 (DEPOSITO) · "
        f"Destino: BQTO Almacén 05 (DEPOSTO_BQTO) · Fecha: {acta['fecha']}", cuerpo
    ))
    historia.append(Spacer(1, 4 * mm))

    faltantes = [r for r in acta["renglones"] if r["faltante"] > 0 or r["averias"]]
    if faltantes:
        historia.append(Paragraph("Renglones con discrepancias (marcados EN_RECLAMO)", sub))
        tabla = Table(
            [["Código", "Descripción", "Enviado", "Recibido", "Faltante", "Averías"]]
            + [
                [
                    r["cod_art"], r["descripcion"], str(r["cantidad_enviada"]),
                    str(r["recibido_estimado"]), str(r["faltante"]), r["averias"] or "—",
                ]
                for r in faltantes
            ],
            colWidths=[24 * mm, 52 * mm, 18 * mm, 20 * mm, 18 * mm, 40 * mm],
        )
        tabla.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#8B0000")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
        ]))
        historia.append(tabla)
    else:
        historia.append(Paragraph("Sin discrepancias detectadas (manifiesto conforme).", cuerpo))

    SimpleDocTemplate(ruta, pagesize=letter).build(historia)
    return None


def auditar_traslado_intersedes(cod_traslado: str, modo_real: bool = False) -> Dict[str, Any]:
    """Audita un traslado inter-sedes y genera el acta si hay discrepancia.

    Parámetros:
        cod_traslado (str): código del traslado a conciliar.
        modo_real (bool): si True, ejecuta el UPDATE de estado ``EN_RECLAMO``
            sobre los renglones con discrepancia (best-effort). False (default)
            = dry-run: solo reporta el SQL que se ejecutaría.

    Retorna dict con ``status``, ``cod_traslado``, ``renglones`` (cada uno con
    ``faltante``, ``recibido_estimado``, ``estado_final``), ``acta`` (ruta del
    PDF + JSON) y ``aviso``. Nunca lanza.
    """
    t0 = time.perf_counter()
    cod = base.san(cod_traslado)
    if not cod:
        return {"status": "error", "aviso": "Falta el código del traslado."}

    conn, motor, err = base.conectar()
    if conn is None:
        return {"status": "error", "cod_traslado": cod, "aviso": f"No hay motor de BD disponible: {err}"}

    try:
        manifiesto = _detalle_manifiesto(conn, motor, cod)
        if not manifiesto.get("esquema_ok"):
            return {
                "status": "error",
                "cod_traslado": cod,
                "aviso": manifiesto.get("aviso", "Esquema no disponible."),
            }
        if "renglones" not in manifiesto:
            return {"status": "ok", "cod_traslado": cod, "renglones": [], "aviso": manifiesto.get("aviso")}

        renglones = manifiesto["renglones"]
        stock_destino = _stock_destino(conn, motor, [r["cod_art"] for r in renglones])

        # Conciliación renglón por renglón.
        renglones_auditados: List[Dict[str, Any]] = []
        en_reclamo: List[Dict[str, Any]] = []
        for r in renglones:
            recibido_reg = r.get("cantidad_recibida_registrada")
            if recibido_reg is not None:
                recibido = recibido_reg
            else:
                recibido = stock_destino.get(r["cod_art"], 0.0)
            faltante = max(0.0, r["cantidad_enviada"] - recibido)
            estado = r.get("estado_actual") or ""
            si_reclamo = faltante > 0 or bool(r.get("averias"))
            fila = {
                "cod_art": r["cod_art"],
                "descripcion": r.get("descripcion", ""),
                "cantidad_enviada": r["cantidad_enviada"],
                "recibido_estimado": recibido,
                "faltante": faltante,
                "averias": r.get("averias", ""),
                "estado_anterior": estado,
                "estado_final": VALOR_RECLAMO if si_reclamo else (estado or "CONFORME"),
            }
            renglones_auditados.append(fila)
            if si_reclamo:
                en_reclamo.append(fila)

        sql_reclamo: Optional[str] = None
        registros_actualizados = 0
        col_estado = manifiesto.get("columnas_resueltas", {}).get("estado")
        tabla = manifiesto.get("tabla")
        if en_reclamo and col_estado and tabla:
            def q(nombre: str) -> str:
                return f"[{nombre}]" if motor == "sqlserver" else f"`{nombre}`"

            col_id = manifiesto["columnas_resueltas"]["id_traslado"]
            col_cod_art = manifiesto["columnas_resueltas"]["cod_art"]
            sql_reclamo = (
                f"UPDATE {q(tabla)} SET {q(col_estado)} = {base.placeholder(motor)} "
                f"WHERE {q(col_id)} = {base.placeholder(motor)} AND {q(col_cod_art)} = "
                + base.placeholder(motor)
            )
            if modo_real:
                try:
                    cur = conn.cursor()
                    for r in en_reclamo:
                        cur.execute(sql_reclamo, (VALOR_RECLAMO, cod, r["cod_art"]))
                        registros_actualizados += cur.rowcount
                    conn.commit()
                    cur.close()
                    base._print_sync(
                        f"✅ Traslado {cod}: {len(en_reclamo)} renglón(es) marcado(s) {VALOR_RECLAMO} "
                        f"({registros_actualizados} fila(s))"
                    )
                except Exception:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    sql_reclamo = None
            else:
                base._print_sync(
                    f"ℹ️ Traslado {cod}: {len(en_reclamo)} renglón(es) listo(s) para {VALOR_RECLAMO} "
                    "(dry-run; use modo_real=True para escribir)."
                )

        aviso = ""
        if en_reclamo:
            aviso = (
                f"{len(en_reclamo)} renglón(es) con discrepancia: "
                + (f"estado marcado {VALOR_RECLAMO}." if modo_real and registros_actualizados
                   else "acta generada; estado en dry-run (use modo_real=True).")
            )
        else:
            aviso = "Manifiesto conforme: sin faltantes ni averías."

        # ── Acta de Discrepancia en Tránsito ─────────────────────────────────
        acta: Dict[str, Any] = {
            "tipo": "ACTA_DISCREPANCIA_TRANSITO",
            "cod_traslado": cod,
            "fecha": time.strftime("%Y-%m-%d %H:%M:%S"),
            "origen": "S/C Almacén 02 (DEPOSITO)",
            "destino": "BQTO Almacén 05 (DEPOSTO_BQTO)",
            "total_items": len(renglones_auditados),
            "items_discrepantes": len(en_reclamo),
            "renglones": renglones_auditados,
            "estado_aplicado": VALOR_RECLAMO if en_reclamo else None,
            "registros_actualizados": registros_actualizados,
        }
        ruta_acta = ""
        acta_generada = False
        if en_reclamo:
            try:
                os.makedirs(DIR_ACTAS, exist_ok=True)
            except Exception:
                pass
            fecha_pdf = time.strftime("%Y%m%d_%H%M%S")
            ruta_pdf = os.path.join(DIR_ACTAS, f"acta_discrepancia_{cod}_{fecha_pdf}.pdf")
            error = _generar_acta_pdf(acta, ruta_pdf)
            if error:
                aviso += f" | Acta PDF no generada (reportlab: {error})."
            else:
                ruta_acta = ruta_pdf
                acta_generada = True
                ruta_json = ruta_pdf.replace(".pdf", ".json")
                try:
                    with open(ruta_json, "w", encoding="utf-8") as f:
                        json.dump(acta, f, ensure_ascii=False, indent=2, default=str)
                except Exception:
                    pass

        return {
            "status": "ok",
            "cod_traslado": cod,
            "motor": motor,
            "tabla_manifiesto": manifiesto.get("tabla"),
            "renglones": renglones_auditados,
            "acta_generada": acta_generada,
            "ruta_acta": ruta_acta,
            "sql_marcado_reclamo": sql_reclamo,
            "registros_actualizados": registros_actualizados,
            "modo_real": modo_real,
            "aviso": aviso,
            "tiempo_ms": int((time.perf_counter() - t0) * 1000),
        }
    except Exception as e:
        base.traceback.print_exc()
        return {"status": "error", "cod_traslado": cod, "aviso": f"Error auditando el traslado: {e}"}
    finally:
        base.cerrar(conn)


if __name__ == "__main__":
    import sys
    cod = sys.argv[1] if len(sys.argv) > 1 else ""
    real = "--confirmar" in sys.argv
    print(json.dumps(auditar_traslado_intersedes(cod, modo_real=real), ensure_ascii=False, indent=2, default=str))
