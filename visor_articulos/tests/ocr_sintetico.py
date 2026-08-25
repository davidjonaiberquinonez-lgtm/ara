# -*- coding: utf-8 -*-
"""OCR sintético para el benchmark: simula la lectura de un lector real.

Comportamiento por escenario (fiel al problema):
    - perfecto:  lee descripción, dosis y barcode con alta confianza (0.95).
    - reflejo:   lee descripción y dosis, confianza media (0.75); barcode puede perderse.
    - desgastado: la etiqueta está tapada/borrosa: lee solo fragmentos (confianza 0.35).
    - idéntico:  el diseño es ambiguo (gemelos), pero la dosis es legible (0.90);
                 el barcode NO es legible (así el desempate es por dosis, híbrido).
"""
from __future__ import annotations

import hashlib
import random
from typing import Optional

from visor_articulos.domain.articulo_matcher import Producto, TextExtractionPort, TextoExtraido

# Comportamientos por escenario: (confianza, leer_barcode, corromper_dosis, corromper_descripcion)
PERFIL_ESCENARIO = {
    "perfecto": dict(confianza=0.95, leer_barcode=True, corromper_dosis=False, corromper_descripcion=False),
    "reflejo": dict(confianza=0.75, leer_barcode=False, corromper_dosis=False, corromper_descripcion=False),
    "desgastado": dict(confianza=0.35, leer_barcode=False, corromper_dosis=True, corromper_descripcion=True),
    "identico": dict(confianza=0.90, leer_barcode=False, corromper_dosis=False, corromper_descripcion=False),
}


def _corromper(texto: str, rng: random.Random, tasa: float = 0.45) -> str:
    """Simula OCR con caracteres perdidos (etiqueta desgastada)."""
    out = []
    for ch in texto:
        if ch.isalnum() and rng.random() < tasa:
            out.append("?" if rng.random() < 0.5 else "")
        else:
            out.append(ch)
    return "".join(out)


class OcrSintetico(TextExtractionPort):
    """Extractor simulado: conoce al producto fotografiado y simula su lectura."""

    def __init__(self, producto_fotografiado: Producto, escenario: str):
        self.producto = producto_fotografiado
        self.escenario = escenario
        perfil = PERFIL_ESCENARIO.get(escenario, PERFIL_ESCENARIO["perfecto"])
        self._perfil = perfil
        self._rng = random.Random(
            int(hashlib.md5(f"{producto_fotografiado.co_art}|{escenario}".encode("utf-8")).hexdigest()[:8], 16)
        )

    def extraer_texto(self, imagen_capturada: bytes) -> TextoExtraido:
        p = self.producto
        perfil = self._perfil
        dosis = _corromper(p.dosis, self._rng) if perfil["corromper_dosis"] else p.dosis
        descripcion = p.descripcion
        if perfil["corromper_descripcion"]:
            tokens = descripcion.split()
            descripcion = " ".join(_corromper(t, self._rng) for t in tokens[:1])
        barcode = p.codigo_barra if perfil["leer_barcode"] else ""
        if perfil["corromper_descripcion"] and descripcion == p.descripcion:
            descripcion = "????"
        return TextoExtraido(
            texto=descripcion,
            codigo_barra=barcode,
            dosis=dosis,
            laboratorio=p.laboratorio,
            confianza=perfil["confianza"],
        )
