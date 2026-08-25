"""Skill de Recepción de Mercancía vía Visión OCR — Facturas y Notas de Crédito.

Procesa la foto de una factura física o nota de crédito usando visión
multimodal LOCAL (Ollama con modelo LLaVA — sin servicios pagos), con fallback
a Tesseract OCR (pytesseract) si el modelo de visión no está disponible, y
genera un PDF normalizado membretado con nomenclatura automática:

    PDFs_Recepcion/[LABORATORIO]_[TIPO_DOC]_[NRO_FACTURA]_[FECHA].pdf

Extracción estructurada: proveedor, rif, nro_factura, fecha_emision y
renglones (codigo_prov, descripcion, cantidad, precio_unitario).

Best-effort absoluto: si la imagen no existe, el modelo no responde y el OCR
falla, se retorna un dict con diagnóstico honesto (jamás vacío).
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from skills import base

OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
MODELO_VISION: str = os.getenv("ARA_VISION_MODEL", "llava")
TIMEOUT_VISION_S: int = 90
DIR_PDFS: str = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "PDFs_Recepcion")

PROMPT_EXTRACCION: str = """\
TAREA: Extrae los datos de la FACTURA o NOTA DE CREDITO de esta fotografía.

Responde EXCLUSIVAMENTE un JSON plano con esta estructura (sin texto adicional):
{
  "tipo_documento": "FACTURA" | "NOTACREDITO",
  "proveedor": "nombre del laboratorio/proveedor",
  "rif": "J-000000000-0",
  "nro_factura": "00012345",
  "fecha_emision": "YYYY-MM-DD",
  "renglones": [
    {"codigo_prov": "codigo del producto", "descripcion": "descripcion",
     "cantidad": 10, "precio_unitario": 123.45}
  ]
}
Campos desconocidos: usa "" (texto) o null (numero). Si no hay renglones, usa [].
"""


# ─────────────────────────────────────────────────────────────────────────────
# Visión local (Ollama / LLaVA) y OCR fallback
# ─────────────────────────────────────────────────────────────────────────────

def _leer_imagen_b64(ruta_imagen: str) -> Optional[str]:
    """Imagen a base64 con límite de tamaño (32 MB, patrón del proyecto)."""
    try:
        tamano = os.path.getsize(ruta_imagen)
        if tamano > 32 * 1024 * 1024:
            return None
        with open(ruta_imagen, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
    except Exception:
        return None


def _llamar_ollama_vision(b64: str) -> Optional[str]:
    """Llama al modelo de visión local vía /api/generate (Ollama)."""
    try:
        import urllib.request

        cuerpo = json.dumps({
            "model": MODELO_VISION,
            "prompt": PROMPT_EXTRACCION,
            "images": [b64],
            "stream": False,
            "temperature": 0.0,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{OLLAMA_BASE_URL}/api/generate",
            data=cuerpo,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_VISION_S) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        texto = str(data.get("response", "")).strip()
        return texto or None
    except Exception:
        return None


def _llamar_tesseract(ruta_imagen: str) -> Optional[str]:
    """Fallback: OCR local con pytesseract (si está instalado)."""
    try:
        import pytesseract
        from PIL import Image

        return pytesseract.image_to_string(Image.open(ruta_imagen))
    except Exception:
        return None


def _extraer_json(texto: str) -> Optional[Dict[str, Any]]:
    """Primer bloque JSON de la respuesta (```json ... ``` o {...} directo)."""
    if not texto:
        return None
    m = re.search(r"```json\s*(\{.*?\})\s*```", texto, re.S)
    if m:
        texto = m.group(1)
    m = re.search(r"\{.*\}", texto, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _normalizar_extraccion(datos: Dict[str, Any]) -> Dict[str, Any]:
    """Normaliza y valida los campos extraídos (nunca None en el output)."""
    renglones_raw = datos.get("renglones")
    renglones: List[Dict[str, Any]] = []
    if isinstance(renglones_raw, list):
        for r in renglones_raw:
            if not isinstance(r, dict):
                continue
            renglones.append({
                "codigo_prov": base.san(r.get("codigo_prov")),
                "descripcion": base.san(r.get("descripcion")),
                "cantidad": base.num(r.get("cantidad")),
                "precio_unitario": base.num(r.get("precio_unitario")),
            })
    tipo = base.san(datos.get("tipo_documento")).upper()
    if tipo not in ("FACTURA", "NOTACREDITO"):
        tipo = "FACTURA"
    fecha = base.san(datos.get("fecha_emision"))
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", fecha):
        fecha = ""
    return {
        "tipo_documento": tipo,
        "proveedor": base.san(datos.get("proveedor")),
        "rif": base.san(datos.get("rif")),
        "nro_factura": base.san(datos.get("nro_factura")),
        "fecha_emision": fecha,
        "renglones": renglones,
        "completo": bool(
            datos.get("proveedor") and datos.get("nro_factura") and renglones
        ),
    }


def _nombre_archivo(extraido: Dict[str, Any], laboratorio_override: Optional[str]) -> str:
    """Nomenclatura: [LABORATORIO]_[TIPO_DOC]_[NRO_FACTURA]_[FECHA].pdf."""
    lab = (laboratorio_override or extraido["proveedor"] or "SIN_LAB").upper()
    lab = re.sub(r"[^A-Z0-9]+", "_", lab).strip("_") or "SIN_LAB"
    tipo = "NOTACREDITO" if extraido["tipo_documento"] == "NOTACREDITO" else "FACTURA"
    nro = re.sub(r"[^A-Z0-9]+", "", extraido["nro_factura"]) or "SIN_NRO"
    fecha = extraido["fecha_emision"] or time.strftime("%Y%m%d")
    fecha = re.sub(r"\D", "", fecha)
    return f"{lab}_{tipo}_{nro}_{fecha}.pdf"


def _generar_pdf_normalizado(extraido: Dict[str, Any], ruta: str) -> Optional[str]:
    """PDF membretado corporativo (reportlab). Retorna None o el error."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        )
    except ImportError as e:
        return str(e)

    estilos = getSampleStyleSheet()
    titulo = ParagraphStyle("Titulo", parent=estilos["Title"], fontSize=16, textColor=colors.HexColor("#1F4E78"))
    sub = ParagraphStyle("Sub", parent=estilos["Heading2"], fontSize=10)
    cuerpo = estilos["Normal"]

    historia: List[Any] = []
    historia.append(Paragraph("RECEPCIÓN DE MERCANCÍA — FACTURA / NOTA DE CRÉDITO", titulo))
    historia.append(Spacer(1, 4 * mm))
    cabecera = Table(
        [
            ["Proveedor / Laboratorio", extraido["proveedor"] or "—"],
            ["RIF", extraido["rif"] or "—"],
            ["N° Documento", extraido["nro_factura"] or "—"],
            ["Tipo", extraido["tipo_documento"]],
            ["Fecha de Emisión", extraido["fecha_emision"] or "—"],
            ["Renglones", str(len(extraido["renglones"]))],
        ],
        colWidths=[55 * mm, 100 * mm],
    )
    cabecera.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#D9E2F3")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    historia.append(cabecera)
    historia.append(Spacer(1, 5 * mm))
    historia.append(Paragraph("Detalle de Renglones", sub))
    tabla = Table(
        [["Código Prov.", "Descripción", "Cantidad", "Precio Unitario", "Subtotal"]]
        + [
            [
                r["codigo_prov"] or "—",
                r["descripcion"] or "—",
                str(r["cantidad"]),
                f"{r['precio_unitario']:.2f}",
                f"{r['cantidad'] * r['precio_unitario']:.2f}",
            ]
            for r in extraido["renglones"]
        ],
        colWidths=[28 * mm, 60 * mm, 20 * mm, 30 * mm, 25 * mm],
    )
    tabla.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E78")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
    ]))
    historia.append(tabla)

    SimpleDocTemplate(ruta, pagesize=letter).build(historia)
    return None


def procesar_foto_factura(
    ruta_imagen: str, laboratorio_override: Optional[str] = None
) -> Dict[str, Any]:
    """Procesa la foto de una factura/nota de crédito y genera su PDF normalizado.

    Parámetros:
        ruta_imagen (str): ruta del archivo de imagen (jpg/png/webp...).
        laboratorio_override (str|None): fuerza el nombre del laboratorio en la
            nomenclatura del PDF (ignora el extraído por visión).

    Retorna::

        {
          "status": "ok"|"error"|"parcial",
          "imagen": ruta_imagen,
          "motor_vision": "ollama_llava"|"tesseract"|null,
          "extraccion": {...},
          "pdf_generado": bool,
          "ruta_pdf": "PDFs_Recepcion/....pdf",
          "aviso": "..."
        }

    Best-effort: nunca lanza; ante fallos de visión/OCR retorna ``status
    "error"`` con diagnóstico del motor usado y los datos parciales.
    """
    t0 = time.perf_counter()
    if not ruta_imagen or not os.path.isfile(ruta_imagen):
        return {"status": "error", "aviso": f"La imagen no existe: {ruta_imagen}"}

    aviso = ""
    motor = None
    texto = None

    b64 = _leer_imagen_b64(ruta_imagen)
    if b64 is not None:
        texto = _llamar_ollama_vision(b64)
        if texto is not None:
            motor = "ollama_llava"
        else:
            aviso = "Modelo de visión local (LLaVA) no respondió; probando OCR."

    if texto is None:
        texto = _llamar_tesseract(ruta_imagen)
        if texto is not None:
            motor = "tesseract"
            aviso = aviso or ""
        else:
            aviso = "Fallo total de visión local y OCR (verifique Ollama/LLaVA y pytesseract)."

    extraccion: Dict[str, Any] = {}
    datos = _extraer_json(texto) if texto else None
    if datos is None:
        if texto and motor == "tesseract":
            extraccion = {
                "tipo_documento": "FACTURA",
                "proveedor": "",
                "rif": "",
                "nro_factura": "",
                "fecha_emision": "",
                "renglones": [],
                "completo": False,
                "texto_ocr": texto[:2000],
            }
            aviso = "El OCR no produjo JSON estructurado; se adjunta el texto crudo."
        elif motor is None:
            return {"status": "error", "aviso": aviso, "motor_vision": None}
    else:
        extraccion = _normalizar_extraccion(datos)
        if motor == "tesseract":
            aviso = "Extracción vía OCR (Tesseract); verifique manualmente la exactitud."

    if laboratorio_override:
        extraccion["proveedor"] = base.san(laboratorio_override) or extraccion["proveedor"]

    try:
        os.makedirs(DIR_PDFS, exist_ok=True)
    except Exception:
        pass

    ruta_pdf = os.path.join(DIR_PDFS, _nombre_archivo(extraccion, laboratorio_override))
    error_pdf = _generar_pdf_normalizado(extraccion, ruta_pdf)
    if error_pdf:
        aviso = (aviso + " | " if aviso else "") + f"PDF no generado (reportlab: {error_pdf})."
        ruta_pdf = ""

    status = "ok" if extraccion.get("completo") and not error_pdf else "parcial"
    if not extraccion.get("completo"):
        aviso = (aviso + " | " if aviso else "") + (
            "Extracción incompleta: revise los campos faltantes (proveedor, nro_factura, renglones)."
        )

    return {
        "status": status,
        "imagen": ruta_imagen,
        "motor_vision": motor,
        "extraccion": extraccion,
        "pdf_generado": bool(ruta_pdf),
        "ruta_pdf": ruta_pdf,
        "aviso": aviso,
        "tiempo_ms": int((time.perf_counter() - t0) * 1000),
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Uso: python -m skills.recepcion.factura_vision_ocr <ruta_imagen> [laboratorio]")
        sys.exit(1)
    override = sys.argv[2] if len(sys.argv) > 2 else None
    resultado = procesar_foto_factura(sys.argv[1], override)
    print(json.dumps(resultado, ensure_ascii=False, indent=2, default=str))
