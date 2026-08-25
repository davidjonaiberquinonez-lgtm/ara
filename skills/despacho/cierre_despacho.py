"""Skill de Despacho — Validación de Bultos y Precierre Transaccional.

Valida la estructura de los bultos del Bulto Cerrado antes del cierre del
despacho y produce el plan de PRECIERRE transaccional (gate 1:1):

  * ``validar_estructura_bultos(bultos, items)`` (pura, sin BD): verifica
    que cada bulto del despacho contenga EXACTAMENTE un ítem de la nota
    (gate 1:1 del cierre transaccional v3.29) y reporta bultos vacíos,
    multi-ítem, ítems sin bulto y conteos. Ideal para smoke tests.
  * ``validar_bultos(nota, sede)``: estado del despacho de la nota en el
    legacy (estatus PREPARACION/EMBALADA/CHEQUEO) con el conteo físico de
    cajas/bultos registrados; diagnóstico honesto si el esquema no expone
    las tablas del flujo.
  * ``precierre_transaccional(nota, sede, modo_real)``: decide si el
    precierre puede ejecutarse (gate 1:1 + nota en estado transaccional);
    con ``modo_real=True`` devolvería las transacciones (UPDATE + bitácora)
    listas para ejecutar — el cierre real lo aplica la capa de persistencia
    ARA_SYNC. Default ``modo_real=False`` (dry-run, sin escrituras).

Best-effort absoluto: jamás lanza; respuestas con ``status``/``aviso``.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from skills import base

ESTADOS_CIERRE_VALIDOS: tuple = ("EMBALADA", "CHEQUEO")


# ─────────────────────────────────────────────────────────────────────────────
# Validación pura de la estructura bulto/ítem (gate 1:1) — sin BD
# ─────────────────────────────────────────────────────────────────────────────

def validar_estructura_bultos(
    bultos: List[Any],
    items: List[Any],
) -> Dict[str, Any]:
    """Valida el gate 1:1 entre bultos del despacho y ítems de la nota.

    Parámetros:
        bultos (list): bultos físicos del despacho. Cada elemento puede ser
            un str (código de bulto con un solo ítem) o un dict
            {"codigo": ..., "cod_art"/"sku"/"item": ...} para bultos multi.
        items (list): códigos de artículo (str) esperados de la nota.

    Retorna dict con conteos, bultos vacíos, bultos multi-ítem, ítems sin
    bulto y ``gate_1_1`` (bool). Nunca lanza.
    """
    esperados = [base.san(i) for i in (items or []) if base.san(i)]
    bulto_lista = list(bultos or [])

    contenedores: List[Dict[str, Any]] = []
    for b in bulto_lista:
        if isinstance(b, dict):
            codigo = base.san(b.get("codigo") or b.get("cod_bulto") or b.get("bulto"))
            contenido = base.san(
                b.get("cod_art") or b.get("sku") or b.get("item") or b.get("articulo")
            )
            contenedores.append({"codigo": codigo or "SIN_CODIGO", "contenido": contenido})
        else:
            contenedores.append({"codigo": base.san(b) or "SIN_CODIGO", "contenido": base.san(b)})

    vacios = [c for c in contenedores if c["contenido"] == ""]
    multi = [
        c for c in contenedores
        if c["contenido"] != "" and c["contenido"].count(",") > 0
    ]
    asignados = {
        c["contenido"].split(",")[0]
        for c in contenedores if c["contenido"] != "" and c["contenido"].count(",") == 0
    }
    sin_bulto = [e for e in esperados if e not in asignados]
    con_bulto = [e for e in esperados if e in asignados]

    return {
        "total_bultos": len(contenedores),
        "total_items_esperados": len(esperados),
        "bultos_vacios": vacios,
        "bultos_multi_item": multi,
        "items_sin_bulto": sin_bulto,
        "items_con_bulto": con_bulto,
        "gate_1_1": (
            len(contenedores) == len(esperados) > 0
            and not vacios
            and not multi
            and not sin_bulto
        ),
        "aviso": (
            "Gate 1:1 OK: todo bulto tiene exactamente 1 item y todo item tiene bulto."
            if len(contenedores) == len(esperados) > 0 and not vacios and not multi and not sin_bulto
            else (
                "Gate 1:1 FALLIDO: revisar bultos vacios/multi-item o items sin bulto."
                if contenedores else "Sin bultos registrados para validar."
            )
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Estado real del despacho en el legacy (MySQL .148 / SQL Server CRISTM25)
# ─────────────────────────────────────────────────────────────────────────────

def _tabla_notas(conn: Any, motor: str) -> Optional[str]:
    """Primera tabla real de notas del flujo (rep_not/notas_entrega/notas)."""
    return base.resolver_tabla(
        conn, motor, ("rep_not", "notas_entrega", "notas", "notas_despacho")
    )


def _tabla_cajas(conn: Any, motor: str) -> Optional[str]:
    """Primera tabla real de cajas/bultos del flujo."""
    return base.resolver_tabla(
        conn, motor, ("cajas_embalaje", "cajas", "bultos", "cajas_despacho")
    )


def validar_bultos(nota: str = "", sede: str = "BQTO") -> Dict[str, Any]:
    """Estado del despacho de la nota y conteo de bultos/cajas registrados.

    Parámetros:
        nota (str): código de la nota de despacho.
        sede (str): 'SC' o 'BQTO' (default 'BQTO').

    Retorna dict con ``status``, estado del flujo (estatus), cantidad de
    bultos/cajas y aviso. Si el esquema no expone las tablas del flujo,
    devuelve diagnóstico honesto con los requisitos.
    """
    t0 = time.perf_counter()
    conn, motor, err = base.conectar()
    if conn is None:
        return {"status": "error", "aviso": f"No hay motor de BD disponible: {err}"}

    try:
        nota = base.san(nota)
        if not nota:
            return {"status": "error", "aviso": "Parámetro 'nota' es obligatorio."}

        tabla_notas = _tabla_notas(conn, motor)
        if tabla_notas is None:
            return {
                "status": "error",
                "requisitos": "El esquema debe exponer una tabla de notas del flujo (rep_not/notas_entrega/notas).",
                "aviso": "Tabla de notas no resuelta en el esquema actual.",
            }

        def q(nombre: str) -> str:
            return f"[{nombre}]" if motor == "sqlserver" else f"`{nombre}`"

        cols_notas = base.listar_columnas(conn, motor, tabla_notas)
        col_estatus = base.resolver_columna(cols_notas, ("estatus", "status", "estado"))
        col_nota = base.resolver_columna(cols_notas, ("cod_nota", "nota", "numero_nota", "nro_nota"))

        if col_estatus is None or col_nota is None:
            return {
                "status": "error",
                "tabla_notas": tabla_notas,
                "requisitos": "La tabla de notas debe exponer: cod_nota (nota) y estatus (estado del flujo).",
                "aviso": "Mapeo estricto de columnas no disponible en el esquema actual.",
            }

        estatus = "DESCONOCIDO"
        cur = conn.cursor()
        ph = base.placeholder(motor)
        try:
            cur.execute(
                f"SELECT {q(col_estatus)} FROM {q(tabla_notas)} WHERE {q(col_nota)} = {ph}",
                (nota,),
            )
            fila = cur.fetchone()
            if fila is not None:
                estatus = base.san(fila[0])
        except Exception:
            estatus = "DESCONOCIDO"

        bultos = 0
        tabla_cajas = _tabla_cajas(conn, motor)
        if tabla_cajas is not None:
            cols_cajas = base.listar_columnas(conn, motor, tabla_cajas)
            col_nro = base.resolver_columna(cols_cajas, ("nro_nota", "numero_nota", "nota", "cod_nota"))
            try:
                if col_nro is not None:
                    cur.execute(
                        f"SELECT COUNT(*) FROM {q(tabla_cajas)} WHERE {q(col_nro)} = {ph}",
                        (nota,),
                    )
                    fila = cur.fetchone()
                    bultos = int(fila[0]) if fila is not None else 0
            except Exception:
                bultos = 0
        cur.close()

        return {
            "status": "ok",
            "motor": motor,
            "nota": nota,
            "sede": sede,
            "estatus": estatus,
            "bultos_registrados": bultos,
            "cierre_elegible": estatus.upper() in ESTADOS_CIERRE_VALIDOS,
            "tabla_notas": tabla_notas,
            "tiempo_ms": int((time.perf_counter() - t0) * 1000),
        }
    except Exception as e:
        return {"status": "error", "aviso": f"Error validando bultos: {e}"}
    finally:
        base.cerrar(conn)


def precierre_transaccional(
    nota: str = "",
    sede: str = "BQTO",
    modo_real: bool = False,
    bultos: Optional[List[Any]] = None,
    items: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """Plan de PRECIERRE transaccional del despacho (dry-run por defecto).

    Combina la validación estructural (gate 1:1, pura) con el estado real
    del flujo en el legacy. Con ``modo_real=True`` devuelve las transacciones
    (UPDATE estatus + bitácora) listas para que la capa de persistencia las
    ejecute; el cierre real lo aplica ARA_SYNC (nunca se escribe aquí).

    Parámetros:
        nota (str): código de la nota.
        sede (str): 'SC' o 'BQTO'.
        modo_real (bool): default False (dry-run). True solo PLANIFICA el
            UPDATE, no lo ejecuta.
        bultos (list, opcional): bultos físicos para validar el gate 1:1.
        items (list, opcional): ítems esperados de la nota para el gate 1:1.

    Retorna dict con ``permitir_cierre``, motivo, transacciones planeadas,
    validación estructural y estado del flujo.
    """
    t0 = time.perf_counter()
    nota = base.san(nota)

    estructura = validar_estructura_bultos(
        bultos if bultos is not None else [],
        items if items is not None else [],
    ) if (bultos is not None or items is not None) else None

    estado: Dict[str, Any] = {"status": "sin_validar"}
    if nota:
        estado = validar_bultos(nota=nota, sede=sede)

    permitir = True
    motivos: List[str] = []
    if estructura is not None:
        if not estructura["gate_1_1"]:
            permitir = False
            motivos.append(estructura["aviso"])
    if estado.get("status") == "ok" and not estado.get("cierre_elegible", False):
        permitir = False
        motivos.append(f"Nota {nota} en estatus {estado.get('estatus')}: no elegible para cierre.")
    if estructura is None and estado.get("status") != "ok":
        permitir = False
        motivos.append("Sin datos de bultos/ítems ni estado de nota para validar.")

    transacciones: List[Dict[str, Any]] = []
    if permitir and estado.get("status") == "ok" and nota:
        transacciones.append({
            "tabla": estado.get("tabla_notas", ""),
            "operacion": "UPDATE",
            "set": {"estatus": "CHEQUEADO" if modo_real else "CHEQUEADO (PLAN)"},
            "where": {"cod_nota": nota},
            "bitacora": "precierre_despacho_v4.2",
            "ejecutada": False,
        })

    return {
        "status": "ok",
        "nota": nota,
        "sede": sede,
        "modo_real": bool(modo_real),
        "permitir_cierre": permitir,
        "motivo": ("; ".join(motivos)) if motivos else "Gate 1:1 OK y nota en estado transaccional.",
        "validacion_estructura": estructura,
        "estado_flujo": estado,
        "transacciones_planeadas": transacciones,
        "tiempo_ms": int((time.perf_counter() - t0) * 1000),
    }


if __name__ == "__main__":
    import json
    import sys

    nota = sys.argv[1] if len(sys.argv) > 1 else ""
    salida = precierre_transaccional(nota=nota)
    print(json.dumps(salida, ensure_ascii=False, indent=2, default=str))
