# -*- coding: utf-8 -*-
"""Adaptador OCR real: envuelve el motor de visión del sistema (Ollama LLaVA)."""
from __future__ import annotations

import base64
import json
import re
import urllib.request

from visor_articulos.domain.articulo_matcher import TextExtractionPort, TextoExtraido

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_MODELO = "llava"
TIMEOUT_S = 30

PROMPT_OCR = (
    "Eres un lector de etiquetas de productos farmacéuticos. Responde SOLO con JSON: "
    '{"codigo_barra":"","codigo":"","descripcion":"","laboratorio":"","dosis":""}. '
    "Si un campo no es legible usa cadena vacía. No agregues texto fuera del JSON."
)


def _extraer_json(texto: str) -> dict:
    m = re.search(r"\{.*\}", texto, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except ValueError:
        return {}


class OcrTextoAdapter(TextExtractionPort):
    """OCR vía Ollama LLaVA (mismo motor de visión de `ara_vision.py`)."""

    def __init__(self, url: str = OLLAMA_URL, modelo: str = OLLAMA_MODELO, timeout: int = TIMEOUT_S):
        self.url = url
        self.modelo = modelo
        self.timeout = timeout

    def extraer_texto(self, imagen_capturada: bytes) -> TextoExtraido:
        b64 = base64.b64encode(imagen_capturada).decode("ascii")
        body = json.dumps(
            {
                "model": self.modelo,
                "prompt": PROMPT_OCR,
                "images": [b64],
                "stream": False,
                "temperature": 0.1,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            respuesta = json.loads(resp.read().decode("utf-8"))
        texto_bruto = respuesta.get("response", "")
        datos = _extraer_json(texto_bruto)
        confianza = 0.6 if datos else 0.0
        return TextoExtraido(
            texto=datos.get("descripcion", ""),
            codigo_barra=datos.get("codigo_barra", ""),
            dosis=datos.get("dosis", ""),
            laboratorio=datos.get("laboratorio", ""),
            confianza=confianza,
        )
