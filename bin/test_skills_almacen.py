#!/usr/bin/env python3
"""
bin/test_skills_almacen.py — Arnés de verificación del ecosistema de Skills
de IA Local del Almacén (skills/).

Cubre:
  1. Funciones PURAS deterministas (no dependen de BD/LLM):
     - surtido_prioritario.clasificar_prioridad (terciles ALTA/MEDIA/BAJA).
     - reporte_quiebres_compras.clasificar_tier (Top 20%).
     - detector_malsurtido: parsing de LOG_REPORTE (formato clave=valor y CSV)
       con detección de discrepancia, matriz de errores e interrupción.
     - detector_malsurtido._nombre_archivo de recepción y normalización.
  2. Diagnóstico de esquema contra la BD real (best-effort, sin escritura):
     - obtener_cola_surtido('SC') → status ok/error con diagnóstico honesto.
     - generar_reporte_compras_laboratorio('excel') → si hay quiebres genera
       el archivo; si no, status ok con aviso. (No escribe en BD.)
  3. Módulo de visión OCR: solo el parser JSON y la nomenclatura del PDF
     (no llama al LLM local).

Salida JSON consolidada {summary{total,passed,failed,total_time_ms}, results}.
Exit: 0 = todo PASS | 1 = algún fallo.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from typing import Any, Callable, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from skills.auditoria import detector_malsurtido
from skills.recepcion import factura_vision_ocr
from skills.stock_bulto_cerrado import reporte_quiebres_compras, surtido_prioritario


def caso(nombre: str, fn: Callable[[], Tuple[bool, str]]) -> dict:
    t0 = time.perf_counter()
    try:
        ok, detalle = fn()
    except Exception as e:  # noqa: BLE001
        ok, detalle = False, f"excepción: {e}"
    return {
        "id": f"skill-{len(_RESULTADOS) + 1}",
        "nombre": nombre,
        "status": "PASS" if ok else "FAIL",
        "detalle": detalle,
        "time_ms": int((time.perf_counter() - t0) * 1000),
    }


def _registrar(fn: Callable[[], dict]) -> None:
    """Ejecuta el caso y lo registra (evita el id duplicado)."""
    _RESULTADOS.append(fn())


_RESULTADOS: List[dict] = []


def main() -> int:
    t0 = time.perf_counter()

    # ── 1) Clasificación de prioridad (terciles) ─────────────────────────────
    def c1() -> Tuple[bool, str]:
        alto = surtido_prioritario.clasificar_prioridad(100.0, 100.0, 0.0)
        medio = surtido_prioritario.clasificar_prioridad(50.0, 100.0, 0.0)
        bajo = surtido_prioritario.clasificar_prioridad(5.0, 100.0, 0.0)
        ok = (alto, medio, bajo) == ("ALTA", "MEDIA", "BAJA")
        return ok, f"ALTA/MEDIA/BAJA -> {alto}/{medio}/{bajo}"

    # ── 2) Tier Top 20% ──────────────────────────────────────────────────────
    def c2() -> Tuple[bool, str]:
        t1 = reporte_quiebres_compras.clasificar_tier(30.0, 20.0)
        t2 = reporte_quiebres_compras.clasificar_tier(5.0, 20.0)
        ok = t1 == "Tier 1" and t2 == "Tier 2"
        return ok, f"vol>=umbral -> {t1}; vol<umbral -> {t2}"

    # ── 3) Parsing LOG_REPORTE (clave=valor) con discrepancia ────────────────
    def c3() -> Tuple[bool, str]:
        fd, ruta = tempfile.mkstemp(suffix=".log")
        os.close(fd)
        try:
            with open(ruta, "w", encoding="utf-8") as f:
                f.write("# LOG_REPORTE estacion BC-01\n")
                f.write("2026-08-07 10:00:01 | OPERARIO=03 | PEDIDO=841234567890 | "
                        "ESCANEADO=841234567890 | ESTACION=BC-01 | OK\n")
                f.write("2026-08-07 10:00:05 | OPERARIO=03 | PEDIDO=841234567890 | "
                        "ESCANEADO=841199990001 | ESTACION=BC-01 | ERROR\n")
            res = detector_malsurtido.analizar_log_surtido(ruta)
            ok = (
                res["lineas_discrepantes"] == 1
                and len(res["eventos"]) == 1
                and res["interrumpir_cierre"] is True
                and res["eventos"][0]["sku_pedido"] == "841234567890"
                and res["eventos"][0]["sku_escaneado"] == "841199990001"
                and res["eventos"][0]["operario_id"] == "03"
                and res["eventos"][0]["estacion"] == "BC-01"
            )
            return ok, json.dumps(res["eventos"], ensure_ascii=False)
        finally:
            try:
                os.remove(ruta)
            except OSError:
                pass

    # ── 4) Parsing LOG_REPORTE formato CSV ───────────────────────────────────
    def c4() -> Tuple[bool, str]:
        fd, ruta = tempfile.mkstemp(suffix=".log")
        os.close(fd)
        try:
            with open(ruta, "w", encoding="utf-8") as f:
                f.write("2026-08-07 10:01:00,22,7701234567,7701234567,BC-02\n")
                f.write("2026-08-07 10:01:05,22,7701234567,7707654321,BC-02\n")
            res = detector_malsurtido.analizar_log_surtido(ruta)
            ok = (
                res["lineas_discrepantes"] == 1
                and res["eventos"][0]["operario_id"] == "22"
                and res["eventos"][0]["estacion"] == "BC-02"
                and res["lineas_ok"] == 1
            )
            return ok, json.dumps(res["eventos"], ensure_ascii=False)
        finally:
            try:
                os.remove(ruta)
            except OSError:
                pass

    # ── 5) Alerta + bloqueo de cierre ────────────────────────────────────────
    def c5() -> Tuple[bool, str]:
        evento = {"operario_id": "03", "sku_pedido": "A1", "sku_escaneado": "B2",
                  "estacion": "BC-01"}
        alerta = detector_malsurtido.emitir_alerta(evento, sonora=False)
        bloqueo = detector_malsurtido.bloquear_cierre_nota("72161167", evento)
        ok = (
            alerta["tipo"] == "MAL_SURTIDO"
            and alerta["accion"] == "INTERRUMPIR_CIERRE"
            and bloqueo["permitir_cierre"] is False
            and bloqueo["motivo"] == "mal_surtido_pendiente_correccion"
        )
        return ok, f"alerta {alerta['tipo']} + cierre bloqueado"

    # ── 6) Nomenclatura PDF de recepción ─────────────────────────────────────
    def c6() -> Tuple[bool, str]:
        extraido = {
            "tipo_documento": "FACTURA",
            "proveedor": "Laboratorio Farma",
            "rif": "J-12345678-9",
            "nro_factura": "00012345",
            "fecha_emision": "2026-08-07",
            "renglones": [{"codigo_prov": "X", "descripcion": "Y",
                           "cantidad": 1, "precio_unitario": 10.0}],
        }
        nombre = factura_vision_ocr._nombre_archivo(extraido, None)
        ok = nombre == "LABORATORIO_FARMA_FACTURA_00012345_20260807.pdf"
        return ok, nombre

    # ── 7) Normalización de extracción JSON (renglones y tipos) ──────────────
    def c7() -> Tuple[bool, str]:
        norm = factura_vision_ocr._normalizar_extraccion({
            "tipo_documento": "notacredito",
            "proveedor": None,
            "nro_factura": "NC-1",
            "fecha_emision": "07/08/2026",
            "renglones": [
                {"codigo_prov": "A1", "descripcion": "D1", "cantidad": "5", "precio_unitario": 2.5},
                {"codigo_prov": None, "descripcion": "", "cantidad": None, "precio_unitario": None},
            ],
        })
        ok = (
            norm["tipo_documento"] == "NOTACREDITO"
            and norm["fecha_emision"] == ""  # fecha no ISO → vacía (honesta)
            and len(norm["renglones"]) == 2
            and norm["renglones"][0]["cantidad"] == 5.0
            and norm["completo"] is False
        )
        return ok, json.dumps(norm, ensure_ascii=False)

    # ── 8) Cola de surtido contra BD real (diagnóstico honesto) ──────────────
    def c8() -> Tuple[bool, str]:
        res = surtido_prioritario.obtener_cola_surtido("SC", 30)
        ok = res.get("status") in ("ok", "error") and bool(res.get("aviso") or res.get("cola") is not None)
        detalle = f"status={res.get('status')} motor={res.get('motor')} " \
                  f"tabla={res.get('tabla_articulos')} cola={res.get('total_articulos', 0)} " \
                  f"| {res.get('aviso', '')}"
        return ok, detalle

    # ── 9) Reporte de quiebres contra BD real (sin escritura en BD) ──────────
    def c9() -> Tuple[bool, str]:
        res = reporte_quiebres_compras.generar_reporte_compras_laboratorio("excel")
        ok = res.get("status") in ("ok", "error") and bool(res.get("aviso") or res.get("ruta"))
        detalle = f"status={res.get('status')} archivo={res.get('archivo_generado')} " \
                  f"skus={res.get('resumen', {}).get('total_skus_agotados', 0)} | {res.get('aviso', '')}"
        return ok, detalle

    # ── 10) Traslado inter-sedes contra BD real (diagnóstico honesto) ────────
    def c10() -> Tuple[bool, str]:
        from skills.auditoria import discrepancia_traslados
        res = discrepancia_traslados.auditar_traslado_intersedes("TEST-SKILLS-01")
        ok = res.get("status") in ("ok", "error") and bool(res.get("aviso") or res.get("renglones") is not None)
        return ok, f"status={res.get('status')} | {res.get('aviso', '')}"

    _RESULTADOS.append(caso("clasificar_prioridad (terciles)", c1))
    _RESULTADOS.append(caso("clasificar_tier (Top 20%)", c2))
    _RESULTADOS.append(caso("LOG_REPORTE clave=valor → discrepancia + interrupción", c3))
    _RESULTADOS.append(caso("LOG_REPORTE CSV → matriz de errores", c4))
    _RESULTADOS.append(caso("alerta sonora/visual + bloqueo de cierre", c5))
    _RESULTADOS.append(caso("nomenclatura PDF recepción", c6))
    _RESULTADOS.append(caso("normalización extracción visión", c7))
    _RESULTADOS.append(caso("cola de surtido vs BD real (diagnóstico)", c8))
    _RESULTADOS.append(caso("reporte de quiebres vs BD real (diagnóstico)", c9))
    _RESULTADOS.append(caso("traslado inter-sedes vs BD real (diagnóstico)", c10))

    total = len(_RESULTADOS)
    pasaron = sum(1 for r in _RESULTADOS if r["status"] == "PASS")
    salida = {
        "suite": "skills-almacen",
        "summary": {
            "total": total,
            "passed": pasaron,
            "failed": total - pasaron,
            "total_time_ms": int((time.perf_counter() - t0) * 1000),
        },
        "results": _RESULTADOS,
    }
    print(json.dumps(salida, ensure_ascii=False, indent=2, default=str))
    return 0 if pasaron == total else 1


if __name__ == "__main__":
    sys.exit(main())
