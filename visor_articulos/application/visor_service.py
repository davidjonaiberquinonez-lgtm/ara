# -*- coding: utf-8 -*-
"""
Servicio de aplicación del visor de artículos.

Orquesta el pipeline síncrono: captura de cámara -> búsqueda visual en RAM
-> extracción OCR (si el puerto está disponible) -> scoring híbrido con
desempate por OCR cuando el ranking visual es ambiguo.

Mide y expone las latencias de cada etapa (para el benchmark de 100 pruebas).
"""
from __future__ import annotations

import time
from typing import List, Optional

from visor_articulos.domain.articulo_matcher import (
    PESO_VISUAL_DEFAULT,
    ResultadoBusqueda,
    TextExtractionPort,
    VisualIndexPort,
    VisualMatch,
    desempatar_por_ocr,
)


class MotorBusquedaVisual:
    """Motor síncrono de reconocimiento: visual (RAM) + OCR, fusionados."""

    def __init__(
        self,
        indice_visual: VisualIndexPort,
        extractor_texto: Optional[TextExtractionPort] = None,
        peso_visual: float = PESO_VISUAL_DEFAULT,
        top_k: int = 5,
    ):
        self.indice_visual = indice_visual
        self.extractor_texto = extractor_texto
        self.peso_visual = peso_visual
        self.top_k = top_k

    def buscar(self, imagen_capturada: bytes) -> ResultadoBusqueda:
        resultado = ResultadoBusqueda()

        t0 = time.perf_counter()
        candidatos_visuales = self.indice_visual.buscar_similares_visuales(
            imagen_capturada, top_k=self.top_k
        )
        resultado.latencia_visual_ms = (time.perf_counter() - t0) * 1000.0

        texto = None
        if self.extractor_texto is not None:
            t1 = time.perf_counter()
            try:
                texto = self.extractor_texto.extraer_texto(imagen_capturada)
            except Exception:
                texto = None
            resultado.latencia_ocr_ms = (time.perf_counter() - t1) * 1000.0

        if not candidatos_visuales:
            resultado.latencia_total_ms = resultado.latencia_visual_ms
            return resultado

        resultado.top = candidatos_visuales
        resultado.score_visual = candidatos_visuales[0].similitud

        if texto is not None:
            resultado.confianza_ocr = texto.confianza
            final = desempatar_por_ocr(candidatos_visuales, texto, self.peso_visual)
            resultado.top = final
            resultado.uso_desempate_ocr = final is not candidatos_visuales

        mejor = resultado.top[0].producto
        resultado.codigo_obtenido = mejor.co_art
        resultado.score_hibrido = resultado.top[0].similitud
        resultado.latencia_total_ms = resultado.latencia_visual_ms + resultado.latencia_ocr_ms
        return resultado

    def buscar_bruto(self, imagen_capturada: bytes) -> List[VisualMatch]:
        """Búsqueda únicamente visual (para benchmarks de latencia pura)."""
        return self.indice_visual.buscar_similares_visuales(imagen_capturada, top_k=self.top_k)
