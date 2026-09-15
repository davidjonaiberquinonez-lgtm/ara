# -*- coding: utf-8 -*-
"""Adaptador OCR real: motor VL de la GDX (Qwen2.5-VL-7B-Instruct, vLLM
puerto 8001, API compatible OpenAI vision) — reemplaza OcrTextoAdapter
(Ollama LLaVA local, 03/09/2026, a pedido explícito del usuario: "conectar
el visor híbrido al motor VL y a la base vectorial de la GDX").

Reconectado 10/09 al modelo VL real vigente: el puerto 8000 (donde apuntaba
antes) pasó a servir Qwen3.6-27B, un modelo de TEXTO sin visión — el visor
llevaba un tiempo roto sin que nadie lo notara porque nada lo estaba usando.
El motor de visión real de la GDX vive en el puerto 8001 (Qwen2.5-VL-7B,
confirmado en vivo contra /v1/models). Ese puerto no exige API key todavía
(LAN abierta) a diferencia del 8000.

Mismo contrato (TextExtractionPort) y mismo prompt/formato JSON que el
adaptador anterior — solo cambia el motor detrás.
"""
from __future__ import annotations

import base64
import json
import os
import re
from typing import Optional

import requests

from visor_articulos.domain.articulo_matcher import TextExtractionPort, TextoExtraido

GDX_VL_URL = os.environ.get("ARA_VISOR_GDX_VL_URL", "http://192.168.4.4:8001/v1/chat/completions")
GDX_VL_MODELO = os.environ.get("ARA_VISOR_GDX_VL_MODELO", "Qwen2.5-VL-7B")
TIMEOUT_S = float(os.environ.get("ARA_VISOR_GDX_VL_TIMEOUT_S", "30"))

PROMPT_OCR = (
    "Eres un lector experto de etiquetas de productos farmacéuticos. Analiza la imagen "
    "con cuidado, campo por campo, antes de responder.\n\n"
    "Extraé estos 5 campos:\n"
    "- codigo_barra: primero respondé para vos mismo (no lo escribas en el JSON): ¿hay líneas "
    "verticales paralelas de código de barras VISIBLES en esta imagen, sí o no? Si la respuesta "
    "es NO, este campo va vacío \"\" — sin excepción, aunque veas números sueltos en cualquier "
    "otra parte de la etiqueta (dosis, presentación, registro sanitario, etc. NO son código de "
    "barras). Solo si la respuesta es SÍ, copiá el número de dígitos impreso justo debajo de "
    "esas líneas.\n"
    "- codigo: cualquier OTRO código alfanumérico impreso en el empaque que identifique el "
    "producto (código de registro sanitario, SKU interno, código de producto estandarizado, "
    "etc.) — típicamente mezcla letras y números (ej. \"CPE0420479212\"). Este es el lugar "
    "correcto para ese tipo de código cuando no hay barcode real.\n"
    "- descripcion: SOLO principio activo + concentración + forma farmacéutica + presentación "
    "(ej. \"SIMETICONA 125MG CJ X 10 CAPS BLANDAS\"). NO incluyas el laboratorio, la marca "
    "comercial, ni frases publicitarias/indicaciones de uso (ej. \"Dolor | Fiebre | Resfriado\" "
    "es publicidad, no descripción del producto — nunca la incluyas).\n"
    "- laboratorio: el FABRICANTE (el laboratorio/empresa que produce el medicamento), NO el "
    "nombre comercial del producto. Buscalo en TODA la etiqueta, no solo cerca del nombre del "
    "producto — suele estar en letra más chica, cerca del código de barras, del número de lote "
    "o del registro sanitario, a veces precedido por \"Fabricado por\", \"Elaborado por\", "
    "\"Laboratorios\", \"Distribuido por\" o solo el nombre de la empresa terminado en "
    "\"C.A.\", \"S.A.\", \"de Venezuela\" o similar. Si el nombre comercial entre paréntesis "
    "junto al principio activo (ej. \"SIMETICONA 125MG (EVIGAX)\") es una MARCA, no lo copies acá "
    "salvo que sea la única mención de fabricante que encuentres en toda la imagen.\n"
    "- dosis: la concentración/dosis del principio activo (ej. \"125MG\").\n\n"
    "Si un campo no es legible o no aparece en la imagen, usa cadena vacía — nunca inventes "
    "ni copies el valor de otro campo para rellenarlo.\n\n"
    "Respondé SOLO con este JSON, sin texto antes ni después: "
    '{"codigo_barra":"","codigo":"","descripcion":"","laboratorio":"","dosis":""}'
)


def _extraer_json(texto: str) -> dict:
    m = re.search(r"\{.*\}", texto, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except ValueError:
        return {}


class VlGdxAdapter(TextExtractionPort):
    def __init__(self, url: Optional[str] = None, modelo: Optional[str] = None, timeout: Optional[float] = None):
        self.url = url or GDX_VL_URL
        self.modelo = modelo or GDX_VL_MODELO
        self.timeout = timeout or TIMEOUT_S
        self._sesion = requests.Session()

    def extraer_texto(self, imagen_capturada: bytes) -> TextoExtraido:
        b64 = base64.b64encode(imagen_capturada).decode("ascii")
        payload = {
            "model": self.modelo,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT_OCR},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ],
                }
            ],
            "temperature": 0.1,
            "max_tokens": 300,
        }
        r = self._sesion.post(self.url, json=payload, timeout=self.timeout)
        r.raise_for_status()
        texto_bruto = r.json()["choices"][0]["message"].get("content") or ""
        datos = _extraer_json(texto_bruto)
        confianza = 0.6 if datos else 0.0
        # Saneo de codigo_barra (10/09): el modelo (7B) no logra de forma
        # confiable razonar "no hay código de barras visible → vacío" solo
        # con el prompt — probado en vivo devolviendo la dosis ("650 mg") o
        # el código de registro cuando no hay barras reales en la imagen.
        # Más confiable exigirlo acá en código: si no es solo dígitos, no es
        # un código de barras real, se descarta en vez de propagar basura.
        codigo_barra = (datos.get("codigo_barra") or "").strip()
        if not codigo_barra.isdigit():
            codigo_barra = ""
        return TextoExtraido(
            texto=datos.get("descripcion", ""),
            codigo_barra=codigo_barra,
            dosis=datos.get("dosis", ""),
            laboratorio=datos.get("laboratorio", ""),
            confianza=confianza,
        )
