# -*- coding: utf-8 -*-
"""
Dominio del visor de artículos.

Define los puertos (interfaces) que conectan el motor visual con el resto
del sistema, los modelos de datos del dominio y el algoritmo de scoring
ponderado que fusiona la similitud vectorial (visual) con la coincidencia
textual del OCR.

Puertos:
    - VisualIndexPort:     índice de huellas vectoriales en memoria.
    - TextExtractionPort:  extracción de texto/OCR desde una imagen.

El matcher de dominio NO depende de librerías de visión: recibe similitudes
visuales ya calculadas y textos extraídos, y produce el ranking final.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol

# ---------------------------------------------------------------------------
# Modelos de dominio
# ---------------------------------------------------------------------------


@dataclass
class Producto:
    """Artículo del catálogo indexado por su huella visual."""

    co_art: str
    descripcion: str = ""
    laboratorio: str = ""
    dosis: str = ""
    codigo_barra: str = ""
    imagen_url: str = ""
    ubicacion: str = ""

    def clave_textual(self) -> str:
        return " ".join(
            p for p in [self.descripcion, self.laboratorio, self.dosis] if p
        ).lower()


@dataclass
class VisualMatch:
    """Candidato visual con su similitud en [0, 1]."""

    producto: Producto
    similitud: float


@dataclass
class TextoExtraido:
    """Resultado del extractor OCR (real o simulado)."""

    texto: str = ""
    codigo_barra: str = ""
    dosis: str = ""
    laboratorio: str = ""
    confianza: float = 0.0  # 0..1: 1 = OCR perfecto, 0 = ilegible


@dataclass
class ResultadoBusqueda:
    """Respuesta del motor: ranking híbrido + métricas de latencia."""

    codigo_obtenido: str = ""
    acierto: bool = False
    top: List[VisualMatch] = field(default_factory=list)
    score_visual: float = 0.0
    score_hibrido: float = 0.0
    confianza_ocr: float = 0.0
    uso_desempate_ocr: bool = False
    latencia_total_ms: float = 0.0
    latencia_visual_ms: float = 0.0
    latencia_ocr_ms: float = 0.0

    def top_codigos(self) -> List[str]:
        return [m.producto.co_art for m in self.top[:3]]

    def acierta_con(self, codigo_esperado: str, top_k: int = 1) -> bool:
        codigos = self.top_codigos()
        if not codigos:
            return False
        return codigo_esperado in codigos[:top_k]


# ---------------------------------------------------------------------------
# Puertos (interfaces hexagonales)
# ---------------------------------------------------------------------------


class VisualIndexPort(Protocol):
    """Índice vectorial en memoria (adaptador: inmemory_vector_adapter)."""

    def buscar_similares_visuales(
        self, imagen_capturada: bytes, top_k: int = 5
    ) -> List[VisualMatch]:
        ...

    def registrar_producto(self, producto: Producto, imagen: bytes) -> None:
        ...


class TextExtractionPort(Protocol):
    """Extractor de texto OCR (adaptador: ocr_text_adapter u OCR sintético)."""

    def extraer_texto(self, imagen_capturada: bytes) -> TextoExtraido:
        ...


# ---------------------------------------------------------------------------
# Scoring ponderado Visual + OCR (lógica de dominio pura)
# ---------------------------------------------------------------------------

PESO_VISUAL_DEFAULT = 0.65
PESO_TEXTO_DEFAULT = 1.0 - PESO_VISUAL_DEFAULT

# Si el Top-1 y Top-2 visuales están a menos de este margen, hay ambigüedad
# visual y el OCR debe desempatar.
UMBRAL_AMBIGUEDAD_VISUAL = 0.04

# Confianza OCR mínima para que el texto participe del desempate.
UMBRAL_CONFIANZA_OCR_DESEMPATE = 0.55

# Boost al score si el código de barras del OCR coincide exacto con el candidato.
BOOST_BARCODE = 0.25


def _similitud_texto(candidato: Producto, texto: TextoExtraido) -> float:
    """Similitud textual [0,1] entre candidato y texto OCR (token + dosis + laboratorio)."""
    if not texto.texto and not texto.dosis:
        return 0.0
    sim = 0.0
    total = 0

    def _token_sim(a: str, b: str) -> float:
        ta, tb = a.lower().strip(), b.lower().strip()
        if not ta or not tb:
            return 0.0
        if ta == tb:
            return 1.0
        if ta in tb or tb in ta:
            return 0.8
        # Dice aproximado para etiquetas desgastadas (corto)
        if len(ta) > 3 and len(tb) > 3 and (ta in tb or tb in ta):
            return 0.8
        return 0.0

    if texto.texto:
        tokens_leidos = [t for t in texto.texto.lower().split() if len(t) > 2]
        tokens_candidato = [t for t in candidato.clave_textual().split() if len(t) > 2]
        if tokens_leidos and tokens_candidato:
            aciertos = sum(
                1 for tl in tokens_leidos if any(_token_sim(tl, tc) for tc in tokens_candidato)
            )
            sim += aciertos / max(len(tokens_leidos), 1)
            total += 1

    if texto.dosis:
        sim += _token_sim(candidato.dosis, texto.dosis)
        total += 1

    if texto.laboratorio:
        sim += _token_sim(candidato.laboratorio, texto.laboratorio)
        total += 1

    if total == 0:
        return 0.0
    return sim / total


def _aplica_barcode(candidato: Producto, texto: TextoExtraido) -> bool:
    cb_t = (texto.codigo_barra or "").strip()
    return bool(cb_t) and cb_t == candidato.codigo_barra.strip()


def score_hibrido(
    candidato: Producto,
    similitud_visual: float,
    texto: TextoExtraido,
    peso_visual: float = PESO_VISUAL_DEFAULT,
) -> float:
    """Fusiona similitud visual con coincidencia textual OCR (ponderada).

    Si el OCR leyó un código de barras exacto se aplica un boost fuerte:
    el barcode es evidencia casi determinante.
    """
    peso_texto = 1.0 - peso_visual
    sim_texto = _similitud_texto(candidato, texto)
    base = peso_visual * similitud_visual + peso_texto * sim_texto
    if _aplica_barcode(candidato, texto):
        base = min(1.0, base + BOOST_BARCODE * texto.confianza)
    return base


def desempatar_por_ocr(
    candidatos_visuales: List[VisualMatch],
    texto: TextoExtraido,
    peso_visual: float = PESO_VISUAL_DEFAULT,
) -> List[VisualMatch]:
    """Reordena candidatos usando OCR cuando el ranking visual es ambiguo.

    Devuelve la lista reordenada y marca el estado de desempate mediante el
    atributo `similitud` (el ranking final lo define el servicio de aplicación).
    """
    if len(candidatos_visuales) < 2:
        return candidatos_visuales

    top1, top2 = candidatos_visuales[0], candidatos_visuales[1]
    ambiguo = (top1.similitud - top2.similitud) < UMBRAL_AMBIGUEDAD_VISUAL
    ocr_fiable = texto.confianza >= UMBRAL_CONFIANZA_OCR_DESEMPATE

    if ambiguo and ocr_fiable:
        peso_texto_alto = 1.0 - peso_visual
        # En ambigüedad visual el texto manda (pesos invertidos)
        pesos = (peso_texto_alto, peso_visual)
    else:
        pesos = (peso_visual, 1.0 - peso_visual)

    w_v, w_t = pesos
    scored = []
    for m in candidatos_visuales:
        sim_texto = _similitud_texto(m.producto, texto)
        puntaje = w_v * m.similitud + w_t * sim_texto
        if _aplica_barcode(m.producto, texto):
            puntaje = min(1.0, puntaje + BOOST_BARCODE * texto.confianza)
        scored.append((puntaje, m))

    scored.sort(key=lambda t: t[0], reverse=True)
    return [m for _, m in scored]


def es_busqueda_ambigua(candidatos_visuales: List[VisualMatch]) -> bool:
    return (
        len(candidatos_visuales) >= 2
        and (candidatos_visuales[0].similitud - candidatos_visuales[1].similitud)
        < UMBRAL_AMBIGUEDAD_VISUAL
    )
